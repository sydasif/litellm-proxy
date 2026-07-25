#!/usr/bin/env python3
"""Patch LiteLLM 1.92.0 Anthropic /v1/messages streaming translation.

Fixes the malformed SSE stream that makes Claude Code fail with
"Content block is not a thinking block" when NVIDIA NIM / Nemotron 3 Ultra
is served through LiteLLM's Anthropic adapter with streaming + thinking on.

Two defects are corrected in the installed litellm package:

1. The first content block is hardcoded as `{"type": "text"}` before any
   upstream chunk is read. When the model leads with reasoning, an empty
   index-0 text block is emitted and the thinking content follows in index 1.
   This is protocol-correct for Anthropic (thinking may lead), but it also
   masks the real defect below.

2. NIM sends the final reasoning fragment (e.g. ". ") in the same chunk that
   also carries the visible text ("! How can I help you today?"). The block
   opens as `text` (content checked first) but the translated delta is a
   `thinking_delta` (reasoning_content also present). Claude Code rejects a
   thinking_delta arriving inside a block declared as text.

Fix: in the block-transition path, when a chunk carries reasoning content but
the active block is text, queue the thinking_delta to the (still-open) thinking
block and only then open the new text block. We also reorder the content-block
type detection so reasoning content wins over text for opening the block.

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
    with io.open(path, encoding="utf-8") as fh:
        src = fh.read()

    # The `_translate_streaming_openai_chunk_to_anthropic_content_block`
    # method checks `choice.delta.content` before `reasoning_content`.
    # Swap the order: reasoning_content (and thinking_blocks) must be checked
    # first so the opened block type matches the emitted delta type.
    old = (
        '            elif choice.delta.content is not None and len(choice.delta.content) > 0:\n'
        '                return "text", TextBlock(type="text", text="")\n'
        '            elif isinstance(choice, StreamingChoices) and hasattr(choice.delta, "thinking_blocks"):\n'
        '                thinking_blocks = choice.delta.thinking_blocks or []\n'
        '                if len(thinking_blocks) > 0:\n'
        '                    thinking_block = thinking_blocks[0]\n'
        '                    if thinking_block["type"] == "thinking":\n'
        '                        thinking = thinking_block.get("thinking") or ""\n'
        '                        signature = thinking_block.get("signature") or ""\n'
        '\n'
        '                        assert isinstance(thinking, str)\n'
        '                        assert isinstance(signature, str)\n'
        '\n'
        '                        if thinking and signature:\n'
        '                            raise ValueError(\n'
        '                                "Both `thinking` and `signature` in a single streaming chunk isn\'t supported."\n'
        '                            )\n'
        '\n'
        '                        return "thinking", ChatCompletionThinkingBlock(\n'
        '                            type="thinking", thinking=thinking, signature=signature\n'
        '                        )\n'
        '            # OpenAI-compatible reasoning backends (e.g. vLLM/SGLang reasoning\n'
        '            # parsers) populate ``reasoning_content`` without ``thinking_blocks``.\n'
        '            # ``Delta`` deletes the ``thinking_blocks`` attribute when unset, so the\n'
        '            # branch above is skipped entirely; open a ``thinking`` block here so the\n'
        '            # matching ``thinking_delta`` stream is not emitted into a text block.\n'
        '            elif isinstance(choice, StreamingChoices) and getattr(choice.delta, "reasoning_content", None):\n'
        '                return "thinking", ChatCompletionThinkingBlock(type="thinking", thinking="", signature="")\n'
    )

    new = (
        '            # OpenAI-compatible reasoning backends (e.g. NVIDIA NIM / Nemotron,\n'
        '            # vLLM/SGLang reasoning parsers) populate ``reasoning_content`` without\n'
        '            # ``thinking_blocks``. This branch MUST be checked before the text\n'
        '            # branch: when a chunk carries both reasoning_content and text, the\n'
        '            # emitted delta is a ``thinking_delta`` (see\n'
        '            # _translate_streaming_openai_chunk_to_anthropic), so the opened block\n'
        '            # must be a thinking block. Opening a text block here would leak a\n'
        '            # thinking_delta into a text block and break strict Anthropic clients\n'
        '            # with "Content block is not a thinking block".\n'
        '            elif isinstance(choice, StreamingChoices) and hasattr(choice.delta, "thinking_blocks"):\n'
        '                thinking_blocks = choice.delta.thinking_blocks or []\n'
        '                if len(thinking_blocks) > 0:\n'
        '                    thinking_block = thinking_blocks[0]\n'
        '                    if thinking_block["type"] == "thinking":\n'
        '                        thinking = thinking_block.get("thinking") or ""\n'
        '                        signature = thinking_block.get("signature") or ""\n'
        '\n'
        '                        assert isinstance(thinking, str)\n'
        '                        assert isinstance(signature, str)\n'
        '\n'
        '                        if thinking and signature:\n'
        '                            raise ValueError(\n'
        '                                "Both `thinking` and `signature` in a single streaming chunk isn\'t supported."\n'
        '                            )\n'
        '\n'
        '                        return "thinking", ChatCompletionThinkingBlock(\n'
        '                            type="thinking", thinking=thinking, signature=signature\n'
        '                        )\n'
        '            elif isinstance(choice, StreamingChoices) and getattr(choice.delta, "reasoning_content", None):\n'
        '                return "thinking", ChatCompletionThinkingBlock(type="thinking", thinking="", signature="")\n'
        '            elif choice.delta.content is not None and len(choice.delta.content) > 0:\n'
        '                return "text", TextBlock(type="text", text="")\n'
    )

    if old not in src:
        print("SKIP transformation.py: target block not found (already patched or version mismatch)")
        return False

    src = src.replace(old, new)
    with io.open(path, "w", encoding="utf-8") as fh:
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
    with io.open(path, encoding="utf-8") as fh:
        src = fh.read()

    # The transition handler appears twice: sync __next__ (20-space indent,
    # no trailing comment) and async __anext__ (24-space indent, trailing
    # "# Reset state for new block" comment). Patch both independently.
    count = 0

    # --- sync path (20-space indent) ---
    sync_old = (
        '                    # 3. If the trigger chunk carries delta content, queue it\n'
        '                    # so the first delta of the new block is not silently dropped.\n'
        '                    if self._trigger_delta_has_content(processed_chunk):\n'
        '                        self.chunk_queue.append(processed_chunk)\n'
        '\n'
        '                    self.sent_content_block_finish = False\n'
        '                    return self.chunk_queue.popleft()\n'
    )
    sync_new = (
        '                    # 3. If the trigger chunk carries delta content, queue it\n'
        '                    # so the first delta of the new block is not silently dropped.\n'
        '                    # Defensive guard: a thinking_delta must never be queued into a\n'
        '                    # block that was just opened as text. NVIDIA NIM / Nemotron can\n'
        '                    # emit a final reasoning fragment in the same chunk as visible\n'
        '                    # text; if that slips through, drop the thinking_delta here so\n'
        '                    # we do not emit "Content block is not a thinking block".\n'
        '                    _delta = processed_chunk.get("delta") or {}\n'
        '                    _is_thinking_delta = _delta.get("type") == "thinking_delta"\n'
        '                    if _is_thinking_delta and self.current_content_block_type == "text":\n'
        '                        pass  # drop thinking_delta into a text block\n'
        '                    elif self._trigger_delta_has_content(processed_chunk):\n'
        '                        self.chunk_queue.append(processed_chunk)\n'
        '\n'
        '                    self.sent_content_block_finish = False\n'
        '                    return self.chunk_queue.popleft()\n'
    )
    if sync_old in src:
        src = src.replace(sync_old, sync_new)
        count += 1

    # --- async path (24-space indent, "# Reset state" comment) ---
    async_old = (
        '                        # 3. If the trigger chunk carries delta content, queue it\n'
        '                        # so the first delta of the new block is not silently dropped.\n'
        '                        if self._trigger_delta_has_content(processed_chunk):\n'
        '                            self.chunk_queue.append(processed_chunk)\n'
        '\n'
        '                        # Reset state for new block\n'
        '                        self.sent_content_block_finish = False\n'
        '                        return self.chunk_queue.popleft()\n'
    )
    async_new = (
        '                        # 3. If the trigger chunk carries delta content, queue it\n'
        '                        # so the first delta of the new block is not silently dropped.\n'
        '                        # Defensive guard: a thinking_delta must never be queued into a\n'
        '                        # block that was just opened as text. NVIDIA NIM / Nemotron can\n'
        '                        # emit a final reasoning fragment in the same chunk as visible\n'
        '                        # text; if that slips through, drop the thinking_delta here so\n'
        '                        # we do not emit "Content block is not a thinking block".\n'
        '                        _delta = processed_chunk.get("delta") or {}\n'
        '                        _is_thinking_delta = _delta.get("type") == "thinking_delta"\n'
        '                        if _is_thinking_delta and self.current_content_block_type == "text":\n'
        '                            pass  # drop thinking_delta into a text block\n'
        '                        elif self._trigger_delta_has_content(processed_chunk):\n'
        '                            self.chunk_queue.append(processed_chunk)\n'
        '\n'
        '                        # Reset state for new block\n'
        '                        self.sent_content_block_finish = False\n'
        '                        return self.chunk_queue.popleft()\n'
    )
    if async_old in src:
        src = src.replace(async_old, async_new)
        count += 1

    # --- Fix _should_start_new_content_block to handle empty choices ---
    # The method accesses chunk.choices[0] without checking if choices is empty.
    # This causes IndexError when the upstream sends an empty chunk.
    guard_old = (
        '        # Example logic - customize based on your needs:\n'
        '        # If chunk indicates a tool call\n'
        '        if chunk.choices[0].finish_reason is not None:\n'
        '            return False\n'
    )
    guard_new = (
        '        # Example logic - customize based on your needs:\n'
        '        # If chunk indicates a tool call\n'
        '        if not chunk.choices:\n'
        '            return False\n'
        '        if chunk.choices[0].finish_reason is not None:\n'
        '            return False\n'
    )
    if guard_old in src:
        src = src.replace(guard_old, guard_new)
        count += 1
        print("PATCHED streaming_iterator.py: added empty-choices guard in _should_start_new_content_block")

    if count == 0:
        print("SKIP streaming_iterator.py transition: target blocks not found (already patched or version mismatch)")
        return False

    with io.open(path, "w", encoding="utf-8") as fh:
        fh.write(src)
    print(f"PATCHED streaming_iterator.py: added thinking_delta/text-block guard in {count} transition path(s)")
    return True

def _patch_streaming_dual_content_recovery(path: str) -> bool:
    """Recover text silently dropped when Nemotron sends reasoning_content +
    content in the same chunk. Translation picks reasoning (thinking_delta)
    and drops the text. We detect this, store the dropped text, and on the
    next __next__/__anext__ call emit a block transition (close thinking →
    open text) plus the recovered text_delta."""
    with io.open(path, encoding="utf-8") as fh:
        src = fh.read()

    count = 0

    # --- 1. Add self._recovered_text to __init__ ---
    init_old = (
        '        self.chunk_queue: deque = deque()\n'
    )
    init_new = (
        '        self.chunk_queue: deque = deque()\n'
        '        # Nemotron dual-content recovery: stores text dropped when a\n'
        '        # chunk carries both reasoning_content and content.\n'
        '        self._recovered_text: Optional[str] = None\n'
    )
    if init_old in src:
        src = src.replace(init_old, init_new, 1)
        count += 1

    # --- 2. Recovery block in sync __next__ (20-space indent) ---
    sync_recovery_old = (
        '            # Always return queued chunks first\n'
        '            if self.chunk_queue:\n'
        '                return self.chunk_queue.popleft()\n'
        '\n'
        '            # Queue initial chunks if not sent yet\n'
        '            if self.sent_first_chunk is False:\n'
    )
    sync_recovery_new = (
        '            # Always return queued chunks first\n'
        '            if self.chunk_queue:\n'
        '                return self.chunk_queue.popleft()\n'
        '\n'
        '            # Nemotron dual-content recovery: emit delayed text from\n'
        '            # the previous chunk that carried reasoning_content + content.\n'
        '            if getattr(self, "_recovered_text", None) is not None:\n'
        '                _rtxt = self._recovered_text\n'
        '                self._recovered_text = None\n'
        '                self.chunk_queue.append(\n'
        '                    {"type": "content_block_stop", "index": self.current_content_block_index}\n'
        '                )\n'
        '                self._increment_content_block_index()\n'
        '                self.current_content_block_type = "text"\n'
        '                self.current_content_block_start = {"type": "text", "text": ""}\n'
        '                self.chunk_queue.append(\n'
        '                    {\n'
        '                        "type": "content_block_start",\n'
        '                        "index": self.current_content_block_index,\n'
        '                        "content_block": self.current_content_block_start,\n'
        '                    }\n'
        '                )\n'
        '                self.chunk_queue.append(\n'
        '                    {\n'
        '                        "type": "content_block_delta",\n'
        '                        "index": self.current_content_block_index,\n'
        '                        "delta": {"type": "text_delta", "text": _rtxt},\n'
        '                    }\n'
        '                )\n'
        '                self.sent_content_block_finish = False\n'
        '                return self.chunk_queue.popleft()\n'
        '\n'
        '            # Queue initial chunks if not sent yet\n'
        '            if self.sent_first_chunk is False:\n'
    )
    if sync_recovery_old in src:
        src = src.replace(sync_recovery_old, sync_recovery_new, 1)
        count += 1

    # --- 3. Recovery block in async __anext__ (24-space indent) ---
    async_recovery_old = (
        '            # Always return queued chunks first\n'
        '            if self.chunk_queue:\n'
        '                return self.chunk_queue.popleft()\n'
        '\n'
        '            # Queue initial chunks if not sent yet\n'
        '            if self.sent_first_chunk is False:\n'
    )
    # Only replace the second occurrence (inside __anext__) — skip first (__next__ already patched).
    # Use rsplit to target the last occurrence.
    if async_recovery_old in src:
        idx = src.rfind(async_recovery_old)
        if idx != -1:
            src = src[:idx] + (
                '            # Always return queued chunks first\n'
                '            if self.chunk_queue:\n'
                '                return self.chunk_queue.popleft()\n'
                '\n'
                '            # Nemotron dual-content recovery: emit delayed text from\n'
                '            # the previous chunk that carried reasoning_content + content.\n'
                '            if getattr(self, "_recovered_text", None) is not None:\n'
                '                _rtxt = self._recovered_text\n'
                '                self._recovered_text = None\n'
                '                self.chunk_queue.append(\n'
                '                    {"type": "content_block_stop", "index": self.current_content_block_index}\n'
                '                )\n'
                '                self._increment_content_block_index()\n'
                '                self.current_content_block_type = "text"\n'
                '                self.current_content_block_start = {"type": "text", "text": ""}\n'
                '                self.chunk_queue.append(\n'
                '                    {\n'
                '                        "type": "content_block_start",\n'
                '                        "index": self.current_content_block_index,\n'
                '                        "content_block": self.current_content_block_start,\n'
                '                    }\n'
                '                )\n'
                '                self.chunk_queue.append(\n'
                '                    {\n'
                '                        "type": "content_block_delta",\n'
                '                        "index": self.current_content_block_index,\n'
                '                        "delta": {"type": "text_delta", "text": _rtxt},\n'
                '                    }\n'
                '                )\n'
                '                self.sent_content_block_finish = False\n'
                '                return self.chunk_queue.popleft()\n'
                '\n'
                '            # Queue initial chunks if not sent yet\n'
                '            if self.sent_first_chunk is False:\n'
            ) + src[idx + len(async_recovery_old):]
            count += 1

    # --- 4. Detection after processed_chunk in sync __next__ ---
    # Find the anchor: translate_streaming call followed by "# Check if this is a usage chunk"
    # There are two occurrences (sync and async). Replace both with replaceAll.
    detect_marker = (
        '                )\n'
        '\n'
        '                # Check if this is a usage chunk and we have a held stop_reason chunk\n'
    )
    detect_recovery = (
        '                )\n'
        '\n'
        '                # Nemotron dual-content recovery: when a chunk carries both\n'
        '                # reasoning_content and content, translation picks reasoning\n'
        '                # (thinking_delta) and silently drops the text. Store the\n'
        '                # dropped text so it can be recovered on the next iteration.\n'
        '                _p_delta = processed_chunk.get("delta") or {}\n'
        '                if (\n'
        '                    _p_delta.get("type") == "thinking_delta"\n'
        '                    and chunk.choices\n'
        '                ):\n'
        '                    _raw = getattr(chunk.choices[0], "delta", None)\n'
        '                    _raw_content = getattr(_raw, "content", None) if _raw else None\n'
        '                    if _raw_content and len(_raw_content) > 0:\n'
        '                        self._recovered_text = _raw_content\n'
        '\n'
        '                # Check if this is a usage chunk and we have a held stop_reason chunk\n'
    )
    # Two occurrences: sync __next__ and async __anext__. Replace both.
    if detect_marker in src:
        src = src.replace(detect_marker, detect_recovery)
        count += 2

    if count == 0:
        print("SKIP streaming_iterator.py dual-content recovery: targets not found")
        return False

    with io.open(path, "w", encoding="utf-8") as fh:
        fh.write(src)
    print(f"PATCHED streaming_iterator.py: added dual-content text recovery ({count} patches)")
    return True


def _patch_streaming_empty_choices(path: str) -> bool:
    """Patch streaming_iterator.py to handle empty choices in chunk."""
    with io.open(path, "r", encoding="utf-8") as fh:
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
        with io.open(path, "w", encoding="utf-8") as fh:
            fh.write(src)
        print("PATCHED streaming_iterator.py: added empty-choices guard")
        return True
    else:
        print("SKIP streaming_iterator.py empty-choices: target not found (already patched or version mismatch)")
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
        ok = _patch_streaming_empty_choices(STREAMING_FILE) and ok
    else:
        print(f"ERROR: {STREAMING_FILE} not found")
        ok = False

    sys.exit(0 if ok else 1)
