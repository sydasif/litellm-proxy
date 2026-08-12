#!/usr/bin/env python3
"""Patch LiteLLM 1.92.0 Anthropic /v1/messages streaming translation.

Fixes the malformed SSE stream that makes Claude Code fail with
"Content block is not a thinking block" when NVIDIA NIM / Nemotron 3 Ultra
is served through LiteLLM's Anthropic adapter with streaming + thinking on.

Three defects are corrected in the installed litellm package:

1. The first content block is hardcoded as `{"type": "text"}` before any
   upstream chunk is read. When the model leads with reasoning, an empty
   index-0 text block is emitted and the thinking content follows in index 1.
   Fix: peek the first chunk and open a `thinking`/`text`/`tool_use` block to
   match (adapted from upstream PR BerriAI/litellm#33252).

2. NIM sends the final reasoning fragment (e.g. ". ") in the same chunk that
   also carries the visible text ("! How can I help you today?"). The block
   opens as `text` (content checked first) but the translated delta is a
   `thinking_delta` (reasoning_content also present). Claude Code rejects a
   thinking_delta arriving inside a block declared as text.
   Fix: reorder the content-block type detection so reasoning content wins
   over text, and guard the block-transition path so a thinking_delta is never
   queued into a text block.

3. When a chunk carries BOTH reasoning_content and content, translation picks
   reasoning (thinking_delta) and silently drops the visible text. Fix: store
   the dropped text and re-emit it as a text_delta in a new text block on the
   next iteration.

Run from inside the image at build time:
    python3 /app/patches/fix_nemotron_thinking_stream.py
"""

import io
import os
import sys


def _litellm_root() -> str:
    import litellm

    return os.path.dirname(litellm.__file__)


ADAPTER_DIR = os.path.join(
    _litellm_root(),
    "llms",
    "anthropic",
    "experimental_pass_through",
    "adapters",
)

STREAMING_FILE = os.path.join(ADAPTER_DIR, "streaming_iterator.py")
TRANSFORM_FILE = os.path.join(ADAPTER_DIR, "transformation.py")


def _patch_transformation(path: str) -> bool:
    """Reorder content-block type detection so reasoning_content wins over
    text. This prevents a chunk carrying both reasoning and text from opening
    a `text` block when its translated delta is a `thinking_delta`."""
    with open(path, encoding="utf-8") as fh:
        src = fh.read()

    # The `_translate_streaming_openai_chunk_to_anthropic_content_block`
    # method checks `choice.delta.content` before `reasoning_content`.
    # Swap the order: reasoning_content (and thinking_blocks) must be checked
    # first so the opened block type matches the emitted delta type.
    old = (
        "            elif choice.delta.content is not None and len(choice.delta.content) > 0:\n"
        '                return "text", TextBlock(type="text", text="")\n'
        '            elif isinstance(choice, StreamingChoices) and hasattr(choice.delta, "thinking_blocks"):\n'
        "                thinking_blocks = choice.delta.thinking_blocks or []\n"
        "                if len(thinking_blocks) > 0:\n"
        "                    thinking_block = thinking_blocks[0]\n"
        '                    if thinking_block["type"] == "thinking":\n'
        '                        thinking = thinking_block.get("thinking") or ""\n'
        '                        signature = thinking_block.get("signature") or ""\n'
        "\n"
        "                        assert isinstance(thinking, str)\n"
        "                        assert isinstance(signature, str)\n"
        "\n"
        "                        if thinking and signature:\n"
        "                            raise ValueError(\n"
        '                                "Both `thinking` and `signature` in a single streaming chunk isn\'t supported."\n'
        "                            )\n"
        "\n"
        '                        return "thinking", ChatCompletionThinkingBlock(\n'
        '                            type="thinking", thinking=thinking, signature=signature\n'
        "                        )\n"
        "            # OpenAI-compatible reasoning backends (e.g. vLLM/SGLang reasoning\n"
        "            # parsers) populate ``reasoning_content`` without ``thinking_blocks``.\n"
        "            # ``Delta`` deletes the ``thinking_blocks`` attribute when unset, so the\n"
        "            # branch above is skipped entirely; open a ``thinking`` block here so the\n"
        "            # matching ``thinking_delta`` stream is not emitted into a text block.\n"
        '            elif isinstance(choice, StreamingChoices) and getattr(choice.delta, "reasoning_content", None):\n'
        '                return "thinking", ChatCompletionThinkingBlock(type="thinking", thinking="", signature="")\n'
    )

    new = (
        "            # OpenAI-compatible reasoning backends (e.g. NVIDIA NIM / Nemotron,\n"
        "            # vLLM/SGLang reasoning parsers) populate ``reasoning_content`` without\n"
        "            # ``thinking_blocks``. This branch MUST be checked before the text\n"
        "            # branch: when a chunk carries both reasoning_content and text, the\n"
        "            # emitted delta is a ``thinking_delta`` (see\n"
        "            # _translate_streaming_openai_chunk_to_anthropic), so the opened block\n"
        "            # must be a thinking block. Opening a text block here would leak a\n"
        "            # thinking_delta into a text block and break strict Anthropic clients\n"
        '            # with "Content block is not a thinking block".\n'
        '            elif isinstance(choice, StreamingChoices) and hasattr(choice.delta, "thinking_blocks"):\n'
        "                thinking_blocks = choice.delta.thinking_blocks or []\n"
        "                if len(thinking_blocks) > 0:\n"
        "                    thinking_block = thinking_blocks[0]\n"
        '                    if thinking_block["type"] == "thinking":\n'
        '                        thinking = thinking_block.get("thinking") or ""\n'
        '                        signature = thinking_block.get("signature") or ""\n'
        "\n"
        "                        assert isinstance(thinking, str)\n"
        "                        assert isinstance(signature, str)\n"
        "\n"
        "                        if thinking and signature:\n"
        "                            raise ValueError(\n"
        '                                "Both `thinking` and `signature` in a single streaming chunk isn\'t supported."\n'
        "                            )\n"
        "\n"
        '                        return "thinking", ChatCompletionThinkingBlock(\n'
        '                            type="thinking", thinking=thinking, signature=signature\n'
        "                        )\n"
        '            elif isinstance(choice, StreamingChoices) and getattr(choice.delta, "reasoning_content", None):\n'
        '                return "thinking", ChatCompletionThinkingBlock(type="thinking", thinking="", signature="")\n'
        "            elif choice.delta.content is not None and len(choice.delta.content) > 0:\n"
        '                return "text", TextBlock(type="text", text="")\n'
    )

    if old not in src:
        print(
            "SKIP transformation.py: target block not found (already patched or version mismatch)"
        )
        return False

    src = src.replace(old, new)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(src)
    print("PATCHED transformation.py: reasoning-content block type detection reordered")
    return True


def _patch_streaming(path: str) -> bool:
    """Fix the first-block hardcoded text and the thinking->text transition.

    The original code opened the very first block as an empty text block
    unconditionally. We keep that for compatibility but additionally guard the
    block-transition so a thinking_delta is never emitted into a text block.
    Also fix _should_start_new_content_block to handle empty choices list.
    """
    with open(path, encoding="utf-8") as fh:
        src = fh.read()

    # The transition handler appears twice: sync __next__ (20-space indent,
    # no trailing comment) and async __anext__ (24-space indent, trailing
    # "# Reset state for new block" comment). Patch both independently.
    count = 0

    # --- sync path (20-space indent) ---
    sync_old = (
        "                    # 3. If the trigger chunk carries delta content, queue it\n"
        "                    # so the first delta of the new block is not silently dropped.\n"
        "                    if self._trigger_delta_has_content(processed_chunk):\n"
        "                        self.chunk_queue.append(processed_chunk)\n"
        "\n"
        "                    self.sent_content_block_finish = False\n"
        "                    return self.chunk_queue.popleft()\n"
    )
    sync_new = (
        "                    # 3. If the trigger chunk carries delta content, queue it\n"
        "                    # so the first delta of the new block is not silently dropped.\n"
        "                    # Defensive guard: a thinking_delta must never be queued into a\n"
        "                    # block that was just opened as text. NVIDIA NIM / Nemotron can\n"
        "                    # emit a final reasoning fragment in the same chunk as visible\n"
        "                    # text; if that slips through, drop the thinking_delta here so\n"
        '                    # we do not emit "Content block is not a thinking block".\n'
        '                    _delta = processed_chunk.get("delta") or {}\n'
        '                    _is_thinking_delta = _delta.get("type") == "thinking_delta"\n'
        '                    if _is_thinking_delta and self.current_content_block_type == "text":\n'
        "                        pass  # drop thinking_delta into a text block\n"
        "                    elif self._trigger_delta_has_content(processed_chunk):\n"
        "                        self.chunk_queue.append(processed_chunk)\n"
        "\n"
        "                    self.sent_content_block_finish = False\n"
        "                    return self.chunk_queue.popleft()\n"
    )
    if sync_old in src:
        src = src.replace(sync_old, sync_new)
        count += 1

    # --- async path (24-space indent, "# Reset state" comment) ---
    async_old = (
        "                        # 3. If the trigger chunk carries delta content, queue it\n"
        "                        # so the first delta of the new block is not silently dropped.\n"
        "                        if self._trigger_delta_has_content(processed_chunk):\n"
        "                            self.chunk_queue.append(processed_chunk)\n"
        "\n"
        "                        # Reset state for new block\n"
        "                        self.sent_content_block_finish = False\n"
        "                        return self.chunk_queue.popleft()\n"
    )
    async_new = (
        "                        # 3. If the trigger chunk carries delta content, queue it\n"
        "                        # so the first delta of the new block is not silently dropped.\n"
        "                        # Defensive guard: a thinking_delta must never be queued into a\n"
        "                        # block that was just opened as text. NVIDIA NIM / Nemotron can\n"
        "                        # emit a final reasoning fragment in the same chunk as visible\n"
        "                        # text; if that slips through, drop the thinking_delta here so\n"
        '                        # we do not emit "Content block is not a thinking block".\n'
        '                        _delta = processed_chunk.get("delta") or {}\n'
        '                        _is_thinking_delta = _delta.get("type") == "thinking_delta"\n'
        '                        if _is_thinking_delta and self.current_content_block_type == "text":\n'
        "                            pass  # drop thinking_delta into a text block\n"
        "                        elif self._trigger_delta_has_content(processed_chunk):\n"
        "                            self.chunk_queue.append(processed_chunk)\n"
        "\n"
        "                        # Reset state for new block\n"
        "                        self.sent_content_block_finish = False\n"
        "                        return self.chunk_queue.popleft()\n"
    )
    if async_old in src:
        src = src.replace(async_old, async_new)
        count += 1

    # --- Fix _should_start_new_content_block to handle empty choices ---
    # The method accesses chunk.choices[0] without checking if choices is empty.
    # This causes IndexError when the upstream sends an empty chunk.
    guard_old = (
        "        # Example logic - customize based on your needs:\n"
        "        # If chunk indicates a tool call\n"
        "        if chunk.choices[0].finish_reason is not None:\n"
        "            return False\n"
    )
    guard_new = (
        "        # Example logic - customize based on your needs:\n"
        "        # If chunk indicates a tool call\n"
        "        if not chunk.choices:\n"
        "            return False\n"
        "        if chunk.choices[0].finish_reason is not None:\n"
        "            return False\n"
    )
    if guard_old in src:
        src = src.replace(guard_old, guard_new)
        count += 1
        print(
            "PATCHED streaming_iterator.py: added empty-choices guard in _should_start_new_content_block"
        )

    if count == 0:
        print(
            "SKIP streaming_iterator.py transition: target blocks not found (already patched or version mismatch)"
        )
        return False

    with open(path, "w", encoding="utf-8") as fh:
        fh.write(src)
    print(
        f"PATCHED streaming_iterator.py: added thinking_delta/text-block guard in {count} transition path(s)"
    )
    return True


def _patch_streaming_dual_content_recovery(path: str) -> bool:
    """Recover text silently dropped when Nemotron sends reasoning_content +
    content in the same chunk. Translation picks reasoning (thinking_delta)
    and drops the text. We detect this, store the dropped text, and on the
    next __next__/__anext__ call emit a block transition (close thinking →
    open text) plus the recovered text_delta."""
    with open(path, encoding="utf-8") as fh:
        src = fh.read()

    count = 0

    # --- 1. Add self._recovered_text to __init__ ---
    init_old = "        self.chunk_queue: deque = deque()\n"
    init_new = (
        "        self.chunk_queue: deque = deque()\n"
        "        # Nemotron dual-content recovery: stores text dropped when a\n"
        "        # chunk carries both reasoning_content and content.\n"
        "        self._recovered_text: Optional[str] = None\n"
    )
    if init_old in src:
        src = src.replace(init_old, init_new, 1)
        count += 1

    # --- 2. Recovery block in sync __next__ (20-space indent) ---
    sync_recovery_old = (
        "            # Always return queued chunks first\n"
        "            if self.chunk_queue:\n"
        "                return self.chunk_queue.popleft()\n"
        "\n"
        "            # Queue initial chunks if not sent yet\n"
        "            if self.sent_first_chunk is False:\n"
    )
    sync_recovery_new = (
        "            # Always return queued chunks first\n"
        "            if self.chunk_queue:\n"
        "                return self.chunk_queue.popleft()\n"
        "\n"
        "            # Nemotron dual-content recovery: emit delayed text from\n"
        "            # the previous chunk that carried reasoning_content + content.\n"
        '            if getattr(self, "_recovered_text", None) is not None:\n'
        "                _rtxt = self._recovered_text\n"
        "                self._recovered_text = None\n"
        "                self.chunk_queue.append(\n"
        '                    {"type": "content_block_stop", "index": self.current_content_block_index}\n'
        "                )\n"
        "                self._increment_content_block_index()\n"
        '                self.current_content_block_type = "text"\n'
        '                self.current_content_block_start = {"type": "text", "text": ""}\n'
        "                self.chunk_queue.append(\n"
        "                    {\n"
        '                        "type": "content_block_start",\n'
        '                        "index": self.current_content_block_index,\n'
        '                        "content_block": self.current_content_block_start,\n'
        "                    }\n"
        "                )\n"
        "                self.chunk_queue.append(\n"
        "                    {\n"
        '                        "type": "content_block_delta",\n'
        '                        "index": self.current_content_block_index,\n'
        '                        "delta": {"type": "text_delta", "text": _rtxt},\n'
        "                    }\n"
        "                )\n"
        "                self.sent_content_block_finish = False\n"
        "                return self.chunk_queue.popleft()\n"
        "\n"
        "            # Queue initial chunks if not sent yet\n"
        "            if self.sent_first_chunk is False:\n"
    )
    if sync_recovery_old in src:
        src = src.replace(sync_recovery_old, sync_recovery_new, 1)
        count += 1

    # --- 3. Recovery block in async __anext__ (24-space indent) ---
    async_recovery_old = (
        "            # Always return queued chunks first\n"
        "            if self.chunk_queue:\n"
        "                return self.chunk_queue.popleft()\n"
        "\n"
        "            # Queue initial chunks if not sent yet\n"
        "            if self.sent_first_chunk is False:\n"
    )
    # Only replace the second occurrence (inside __anext__) — skip first (__next__ already patched).
    # Use rsplit to target the last occurrence.
    if async_recovery_old in src:
        idx = src.rfind(async_recovery_old)
        if idx != -1:
            src = (
                src[:idx]
                + (
                    "            # Always return queued chunks first\n"
                    "            if self.chunk_queue:\n"
                    "                return self.chunk_queue.popleft()\n"
                    "\n"
                    "            # Nemotron dual-content recovery: emit delayed text from\n"
                    "            # the previous chunk that carried reasoning_content + content.\n"
                    '            if getattr(self, "_recovered_text", None) is not None:\n'
                    "                _rtxt = self._recovered_text\n"
                    "                self._recovered_text = None\n"
                    "                self.chunk_queue.append(\n"
                    '                    {"type": "content_block_stop", "index": self.current_content_block_index}\n'
                    "                )\n"
                    "                self._increment_content_block_index()\n"
                    '                self.current_content_block_type = "text"\n'
                    '                self.current_content_block_start = {"type": "text", "text": ""}\n'
                    "                self.chunk_queue.append(\n"
                    "                    {\n"
                    '                        "type": "content_block_start",\n'
                    '                        "index": self.current_content_block_index,\n'
                    '                        "content_block": self.current_content_block_start,\n'
                    "                    }\n"
                    "                )\n"
                    "                self.chunk_queue.append(\n"
                    "                    {\n"
                    '                        "type": "content_block_delta",\n'
                    '                        "index": self.current_content_block_index,\n'
                    '                        "delta": {"type": "text_delta", "text": _rtxt},\n'
                    "                    }\n"
                    "                )\n"
                    "                self.sent_content_block_finish = False\n"
                    "                return self.chunk_queue.popleft()\n"
                    "\n"
                    "            # Queue initial chunks if not sent yet\n"
                    "            if self.sent_first_chunk is False:\n"
                )
                + src[idx + len(async_recovery_old) :]
            )
            count += 1

    # --- 4. Detection after processed_chunk in sync __next__ ---
    # Find the anchor: translate_streaming call followed by "# Check if this is a usage chunk"
    # There are two occurrences (sync and async). Replace both with replaceAll.
    detect_marker = (
        "                )\n"
        "\n"
        "                # Check if this is a usage chunk and we have a held stop_reason chunk\n"
    )
    detect_recovery = (
        "                )\n"
        "\n"
        "                # Nemotron dual-content recovery: when a chunk carries both\n"
        "                # reasoning_content and content, translation picks reasoning\n"
        "                # (thinking_delta) and silently drops the text. Store the\n"
        "                # dropped text so it can be recovered on the next iteration.\n"
        '                _p_delta = processed_chunk.get("delta") or {}\n'
        "                if (\n"
        '                    _p_delta.get("type") == "thinking_delta"\n'
        "                    and chunk.choices\n"
        "                ):\n"
        '                    _raw = getattr(chunk.choices[0], "delta", None)\n'
        '                    _raw_content = getattr(_raw, "content", None) if _raw else None\n'
        "                    if _raw_content and len(_raw_content) > 0:\n"
        "                        self._recovered_text = _raw_content\n"
        "\n"
        "                # Check if this is a usage chunk and we have a held stop_reason chunk\n"
    )
    # Two occurrences: sync __next__ and async __anext__. Replace both.
    if detect_marker in src:
        src = src.replace(detect_marker, detect_recovery)
        count += 2

    if count == 0:
        print("SKIP streaming_iterator.py dual-content recovery: targets not found")
        return False

    with open(path, "w", encoding="utf-8") as fh:
        fh.write(src)
    print(f"PATCHED streaming_iterator.py: added dual-content text recovery ({count} patches)")
    return True


def _patch_streaming_peek_first_chunk(path: str) -> bool:
    """Open the initial content block based on the first chunk's type.

    The base adapter hardcodes the very first ``content_block_start`` as
    ``{"type": "text"}`` before reading any upstream chunk. When the model
    leads with reasoning (NVIDIA NIM / Nemotron, vLLM/SGLang reasoning
    parsers), this emits a phantom empty ``text`` block (opened then
    immediately closed) before the real ``thinking`` block — harmless but
    noisy, and strict clients can flag the block/delta mismatch if the first
    delta were ever mistyped into it.

    Fix (informed by upstream PR BerriAI/litellm#33252): peek the first
    chunk, classify it, and open a ``thinking``/``text``/``tool_use`` block to
    match, queueing the first chunk's delta so no token is lost. A stop-only
    first chunk closes the block and holds the ``message_delta`` for the
    standard usage-merge path. The tool-name restore for ``tool_use`` blocks
    is inlined exactly as ``_should_start_new_content_block`` does it
    (``_restore_tool_name_mapping`` does not exist in 1.92.0).

    ``_delta_has_content`` does not exist in 1.92.0 either; the equivalent
    ``_trigger_delta_has_content`` is used instead.
    """
    with open(path, encoding="utf-8") as fh:
        src = fh.read()

    # Both __next__ and __anext__ open the first block with byte-identical
    # code, so disambiguate by position: the first occurrence is the sync
    # path, the second is the async path (async-for peek loop).
    old = (
        "            if self.sent_content_block_start is False:\n"
        "                self.sent_content_block_start = True\n"
        "                self.sent_content_block_finish = False\n"
        "                self.chunk_queue.append(\n"
        "                    {\n"
        '                        "type": "content_block_start",\n'
        '                        "index": self.current_content_block_index,\n'
        '                        "content_block": {"type": "text", "text": ""},\n'
        "                    }\n"
        "                )\n"
        "                return self.chunk_queue.popleft()\n"
    )

    def _build(peek_loop: str) -> str:
        return (
            "            if self.sent_content_block_start is False:\n"
            "                self.sent_content_block_start = True\n"
            "                self.sent_content_block_finish = False\n"
            "                # Peek at the first chunk to open the correct initial\n"
            "                # content block type instead of hardcoding text.\n"
            "                # Reasoning backends (NVIDIA NIM / Nemotron) lead with\n"
            "                # a thinking block.\n"
            "                first_chunk = None\n"
            f"                {peek_loop}:\n"
            "                    if _chunk == 'None' or _chunk is None:\n"
            "                        continue\n"
            "                    first_chunk = _chunk\n"
            "                    break\n"
            '                if first_chunk is not None and getattr(first_chunk, "choices", None):\n'
            "                    (\n"
            "                        block_type,\n"
            "                        content_block_start,\n"
            "                    ) = LiteLLMAnthropicMessagesAdapter()._translate_streaming_openai_chunk_to_anthropic_content_block(\n"
            "                        choices=first_chunk.choices\n"
            "                    )\n"
            "                    # Restore original tool name if it was truncated for\n"
            "                    # the OpenAI 64-char limit (same as _should_start_new_content_block).\n"
            '                    if block_type == "tool_use":\n'
            "                        from typing import cast\n"
            "\n"
            "                        from litellm.types.llms.anthropic import ToolUseBlock\n"
            "\n"
            "                        tool_block = cast(ToolUseBlock, content_block_start)\n"
            '                        if tool_block.get("name"):\n'
            '                            truncated_name = tool_block["name"]\n'
            "                            original_name = self.tool_name_mapping.get(truncated_name, truncated_name)\n"
            '                            tool_block["name"] = original_name\n'
            "                    self.current_content_block_type = block_type\n"
            "                    self.current_content_block_start = content_block_start\n"
            '                    if block_type == "thinking":\n'
            '                        initial_block: dict = {"type": "thinking", "thinking": ""}\n'
            '                    elif block_type == "tool_use":\n'
            "                        initial_block = dict(content_block_start)\n"
            "                    else:\n"
            '                        initial_block = {"type": "text", "text": ""}\n'
            "                    self.chunk_queue.append(\n"
            "                        {\n"
            '                            "type": "content_block_start",\n'
            '                            "index": self.current_content_block_index,\n'
            '                            "content_block": initial_block,\n'
            "                        }\n"
            "                    )\n"
            "                    processed_first = (\n"
            "                        LiteLLMAnthropicMessagesAdapter().translate_streaming_openai_response_to_anthropic(\n"
            "                            response=first_chunk,\n"
            "                            current_content_block_index=self.current_content_block_index,\n"
            "                        )\n"
            "                    )\n"
            "                    # Nemotron dual-content recovery: if the first chunk\n"
            "                    # carried both reasoning and content, translation\n"
            "                    # dropped the content (thinking_delta wins). Store it\n"
            "                    # so the recovery path emits it on the next iteration.\n"
            '                    _pf_delta = processed_first.get("delta") or {}\n'
            '                    if _pf_delta.get("type") == "thinking_delta":\n'
            '                        _raw = getattr(first_chunk.choices[0], "delta", None)\n'
            '                        _raw_content = getattr(_raw, "content", None) if _raw else None\n'
            "                        if _raw_content and len(_raw_content) > 0:\n"
            "                            self._recovered_text = _raw_content\n"
            "                    # Empty / stop-only first chunk: close the block, then\n"
            "                    # hold the message_delta so a subsequent usage-only\n"
            "                    # chunk can be merged (same gate as the main loop).\n"
            '                    if isinstance(processed_first, dict) and processed_first.get("type") == "message_delta":\n'
            "                        self.chunk_queue.append(\n"
            "                            {\n"
            '                                "type": "content_block_stop",\n'
            '                                "index": self.current_content_block_index,\n'
            "                            }\n"
            "                        )\n"
            "                        self.sent_content_block_finish = True\n"
            '                        if processed_first.get("delta", {}).get("stop_reason") is not None:\n'
            "                            self.holding_stop_reason_chunk = processed_first\n"
            "                        else:\n"
            "                            processed_first = self._augment_message_delta_usage(processed_first)\n"
            "                            self.chunk_queue.append(processed_first)\n"
            "                    elif self._trigger_delta_has_content(processed_first):\n"
            "                        self.chunk_queue.append(processed_first)\n"
            "                else:\n"
            "                    self.chunk_queue.append(\n"
            "                        {\n"
            '                            "type": "content_block_start",\n'
            '                            "index": self.current_content_block_index,\n'
            '                            "content_block": {"type": "text", "text": ""},\n'
            "                        }\n"
            "                    )\n"
            "                return self.chunk_queue.popleft()\n"
        )

    sync_new = _build("for _chunk in self.completion_stream")
    async_new = _build("async for _chunk in self.completion_stream")

    count = src.count(old)
    if count == 0:
        print(
            "SKIP streaming_iterator.py first-chunk peek: target block not found (already patched or version mismatch)"
        )
        return False
    if count > 2:
        print(
            f"SKIP streaming_iterator.py first-chunk peek: unexpected target block count ({count})"
        )
        return False

    # Replace the first (sync) occurrence.
    idx = src.index(old)
    src = src[:idx] + sync_new + src[idx + len(old) :]

    # The remaining occurrence is the async path.
    idx = src.index(old)
    src = src[:idx] + async_new + src[idx + len(old) :]

    with open(path, "w", encoding="utf-8") as fh:
        fh.write(src)
    print(
        f"PATCHED streaming_iterator.py: first-chunk peek opens correct initial block ({count} path(s))"
    )
    return True


def _patch_streaming_empty_choices(path: str) -> bool:
    """Patch streaming_iterator.py to handle empty choices in chunk."""
    with open(path, encoding="utf-8") as fh:
        src = fh.read()

    # Find the line: is_final_chunk = chunk.choices[0].finish_reason is not None
    # Add guard before it - use try/except to skip empty choices cleanly
    old_line = "                is_final_chunk = chunk.choices[0].finish_reason is not None"
    new_lines = """                # Guard against empty choices (upstream sends empty chunk)
                if not chunk.choices:
                    continue  # skip to next chunk in stream
                is_final_chunk = chunk.choices[0].finish_reason is not None"""

    if old_line in src:
        src = src.replace(old_line, new_lines)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(src)
        print("PATCHED streaming_iterator.py: added empty-choices guard")
        return True
    else:
        print(
            "SKIP streaming_iterator.py empty-choices: target not found (already patched or version mismatch)"
        )
        return False


if __name__ == "__main__":
    ok = True
    if os.path.exists(TRANSFORM_FILE):
        ok = _patch_transformation(TRANSFORM_FILE) and ok
    else:
        print(f"ERROR: {TRANSFORM_FILE} not found")
        ok = False

    if os.path.exists(STREAMING_FILE):
        ok = _patch_streaming(STREAMING_FILE) and ok
        ok = _patch_streaming_dual_content_recovery(STREAMING_FILE) and ok
        ok = _patch_streaming_peek_first_chunk(STREAMING_FILE) and ok
        ok = _patch_streaming_empty_choices(STREAMING_FILE) and ok
    else:
        print(f"ERROR: {STREAMING_FILE} not found")
        ok = False

    sys.exit(0 if ok else 1)
