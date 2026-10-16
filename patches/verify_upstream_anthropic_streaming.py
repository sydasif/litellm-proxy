#!/usr/bin/env python3
"""Assert the installed litellm still contains the streaming fixes this image depends on.

History: from litellm 1.92.0 through the 1.9x line, `litellm`'s Anthropic
/v1/messages adapter produced malformed SSE for OpenAI-compatible reasoning
backends (NVIDIA NIM / Nemotron, vLLM/SGLang reasoning parsers), which made
Claude Code fail with "Content block is not a thinking block". Four defects had
to be patched in at build time by `fix_nemotron_thinking_stream.py`:

  1. the first content block was hardcoded as `{"type": "text"}` before any
     upstream chunk was read, so a stream that leads with reasoning opened a
     phantom empty text block first;
  2. a chunk carrying both `reasoning_content` and `content` opened a `text`
     block (content was checked first) while the translated delta was a
     `thinking_delta` — a block/delta type mismatch;
  3. the text in such a chunk was silently dropped;
  4. `_should_start_new_content_block` indexed `chunk.choices[0]` with no
     guard, raising `IndexError` on an empty upstream chunk.

As of litellm 1.105.1 all four are fixed upstream — combined reasoning+content
chunks are split per payload kind (#34795), the first block is opened from the
classified block type (#33252), and choiceless chunks are skipped instead of
indexed. The local patch is therefore no longer applied.

This script is the build-time gate that keeps that claim honest: it drives the
installed adapter with the exact Nemotron chunk shape that used to break
Claude Code and fails the build if any invariant regresses. Re-run it (and
re-read the "Image pin & streaming verification" section of CLAUDE.md) on every
base-image bump.

Run from inside the image at build time:
    python3 /app/patches/verify_upstream_anthropic_streaming.py
"""

import asyncio
import importlib
import sys
from typing import Any, List, Tuple

# The adapter moved from llms.anthropic.experimental_pass_through.adapters to
# llms.anthropic.pass_through.adapters in the 1.10x line; accept either.
ADAPTER_MODULES = (
    "litellm.llms.anthropic.pass_through.adapters.streaming_iterator",
    "litellm.llms.anthropic.experimental_pass_through.adapters.streaming_iterator",
)

MARKER_TEXT = "Hello! How can I help you today?"


def _load_wrapper() -> Tuple[Any, str]:
    errors: List[str] = []
    for module_name in ADAPTER_MODULES:
        try:
            module = importlib.import_module(module_name)
        except ImportError as exc:  # pragma: no cover - depends on installed version
            errors.append(f"  {module_name}: {exc}")
            continue
        wrapper = getattr(module, "AnthropicStreamWrapper", None)
        if wrapper is None:
            errors.append(f"  {module_name}: no AnthropicStreamWrapper")
            continue
        return wrapper, module_name
    print("FAIL: could not import the Anthropic streaming adapter")
    print("\n".join(errors))
    raise SystemExit(1)


def _fake_chunks():
    """The upstream chunk shape that used to break Claude Code.

    A reasoning-only chunk, then a chunk carrying a final reasoning fragment in
    the SAME chunk as the visible answer, then the stop chunk.
    """
    from litellm.types.utils import Delta, ModelResponseStream, StreamingChoices

    def make(reasoning=None, content=None, finish_reason=None, empty=False):
        delta = Delta(content=content)
        if reasoning is not None:
            delta.reasoning_content = reasoning
        choices = [] if empty else [StreamingChoices(index=0, delta=delta, finish_reason=finish_reason)]
        return ModelResponseStream(model="verify", choices=choices)

    yield make(reasoning="Let me think about this. ")
    yield make(reasoning=". ", content=MARKER_TEXT)
    yield make(finish_reason="stop")


def _fake_chunks_with_empty_choices():
    """Same as above plus an upstream chunk with an empty choices list."""
    from litellm.types.utils import Delta, ModelResponseStream, StreamingChoices

    def make(reasoning=None, content=None, finish_reason=None, empty=False):
        delta = Delta(content=content)
        if reasoning is not None:
            delta.reasoning_content = reasoning
        choices = [] if empty else [StreamingChoices(index=0, delta=delta, finish_reason=finish_reason)]
        return ModelResponseStream(model="verify", choices=choices)

    yield make(reasoning="thinking ")
    yield make(empty=True)
    yield make(content=MARKER_TEXT)
    yield make(finish_reason="stop")


async def _aiter_of(factory: Any):
    """Async counterpart of the sync chunk factories (the wrapper needs a
    real async iterator on the async path)."""
    for chunk in factory():
        yield chunk


def _collect(wrapper: Any, is_async: bool) -> List[dict]:
    events: List[dict] = []
    if is_async:

        async def drain() -> None:
            async for event in wrapper:
                events.append(event)

        asyncio.run(drain())
    else:
        for event in wrapper:
            events.append(event)
    return events


def _check(events: List[dict], expected_first: str, expected_text: str) -> List[str]:
    block_types: dict = {}
    visible: List[str] = []
    problems: List[str] = []

    for event in events:
        kind = event.get("type")
        if kind == "content_block_start":
            block_types[event["index"]] = event["content_block"]["type"]
        elif kind == "content_block_delta":
            delta = event["delta"]
            opened_as = block_types.get(event["index"])
            if delta.get("type") == "thinking_delta" and opened_as == "text":
                problems.append(f"thinking_delta inside a block opened as text (index {event['index']})")
            if delta.get("type") == "text_delta":
                visible.append(delta.get("text", ""))

    first = next((e for e in events if e.get("type") == "content_block_start"), None)
    first_type = first["content_block"]["type"] if first else None
    if first_type != expected_first:
        problems.append(f"first content block opened as {first_type!r}, expected {expected_first!r}")
    if "".join(visible) != expected_text:
        problems.append(f"visible text {''.join(visible)!r} != {expected_text!r}")
    if not any(e.get("type") == "message_stop" for e in events):
        problems.append("stream did not terminate with message_stop")
    return problems


def _run_case(
    wrapper_cls: Any,
    label: str,
    factory: Any,
    expected_first: str,
    expected_text: str,
    is_async: bool,
) -> bool:
    mode = "async" if is_async else "sync"
    try:
        stream = _aiter_of(factory) if is_async else factory()
        wrapper = wrapper_cls(completion_stream=stream, model="verify")
        events = _collect(wrapper, is_async)
    except Exception as exc:  # noqa: BLE001 - any crash here is a failed check
        print(f"FAIL: {label} [{mode}] raised {type(exc).__name__}: {exc}")
        return False

    problems = _check(events, expected_first, expected_text)
    if problems:
        print(f"FAIL: {label} [{mode}] -> " + "; ".join(problems))
        for event in events:
            print(f"        {event}")
        return False
    print(f"OK:   {label} [{mode}]")
    return True


def main() -> int:
    wrapper_cls, module_name = _load_wrapper()
    print(f"Checking {module_name}\n")

    checks = [
        ("reasoning then combined reasoning+text", _fake_chunks, "thinking", MARKER_TEXT),
        ("chunk with empty choices", _fake_chunks_with_empty_choices, "thinking", MARKER_TEXT),
    ]

    ok = True
    for label, factory, expected_first, expected_text in checks:
        for is_async in (False, True):
            ok = _run_case(wrapper_cls, label, factory, expected_first, expected_text, is_async) and ok

    if not ok:
        print(
            "\nThe installed litellm no longer produces the stream this image expects.\n"
            "Re-read 'Image pin & streaming verification' in CLAUDE.md: a base-image bump may\n"
            "have reintroduced one of the four Nemotron streaming defects, in which\n"
            "case patches/fix_nemotron_thinking_stream.py has to be re-targeted to the\n"
            "new adapter sources and applied again."
        )
        return 1
    print("\nAll upstream streaming invariants hold.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
