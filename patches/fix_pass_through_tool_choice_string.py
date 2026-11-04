#!/usr/bin/env python3
"""Accept string tool_choice in the Anthropic pass_through adapter.

Claude Code sends Anthropic /v1/messages with ``tool_choice: "required"``
(a plain string). LiteLLM 1.105.1's pass_through adapter indexes it as a
dict:

    if tool_choice["type"] == "any":   # TypeError: string indices...

crashing every Claude Code tool-call request with HTTP 500
"string indices must be integers, not 'str'". The sibling chat adapter
(``llms/anthropic/chat/transformation.py::_map_tool_choice``) already
handles strings; the pass_through adapter does not.

This patch normalizes a string tool_choice before any dict indexing, so
the request proceeds unchanged:

* ``"required"``  -> handled as forced tool use (mapped to "any")
* ``"auto"``      -> auto tool choice
* ``"none"``      -> no tool choice
* anything else   -> passed through untouched

Every run is idempotent: on an already-patched tree it reports ALREADY
PATCHED, writes nothing and exits 0. If the anchor no longer matches
exactly once (base image bump), it reports SKIP and exits 1 so the build
fails loudly.

Run from inside the image at build time::

    python3 /app/patches/fix_pass_through_tool_choice_string.py
"""

from __future__ import annotations

import sys
from pathlib import Path

MARKER = "Normalize string tool_choice before dict indexing"

TARGET_RELATIVE = Path("llms/anthropic/pass_through/adapters/transformation.py")

ANCHOR = """\
    def translate_anthropic_tool_choice_to_openai(
        self, tool_choice: AnthropicMessagesToolChoice
    ) -> ChatCompletionToolChoiceValues:
        if tool_choice["type"] == "any":
"""

REPLACEMENT = """\
    def translate_anthropic_tool_choice_to_openai(
        self, tool_choice: AnthropicMessagesToolChoice
    ) -> ChatCompletionToolChoiceValues:
        # Normalize a string tool_choice (e.g. "required") before any dict
        # indexing, so Claude Code /v1/messages tool calls don't 500 with
        # "string indices must be integers, not 'str'".
        if isinstance(tool_choice, str):
            if tool_choice == "required":
                return "required"
            if tool_choice == "auto":
                return "auto"
            if tool_choice == "none":
                return "none"
            return tool_choice
        if tool_choice["type"] == "any":
"""

_PATCH_NOTICE = (
    "\n        # string tool_choice normalized above (fix_pass_through_tool_choice_string.py)\n"
)


def _target_path() -> Path:
    import litellm

    return Path(litellm.__file__).parent / TARGET_RELATIVE


def main() -> int:
    path = _target_path()
    if not path.exists():
        print(f"SKIP: not found: {path}")
        return 1

    source = path.read_text(encoding="utf-8")

    if MARKER in source:
        print("ALREADY PATCHED")
        return 0

    occurrences = source.count(ANCHOR)
    if occurrences != 1:
        print(
            f"SKIP: anchor matched {occurrences} times (expected 1) in {path}"
        )
        print("Re-target patches/fix_pass_through_tool_choice_string.py for this base image.")
        return 1

    new_source = source.replace(ANCHOR, REPLACEMENT, 1)
    tmp_path = path.with_suffix(".py.tmp")
    tmp_path.write_text(new_source, encoding="utf-8")
    tmp_path.replace(path)
    print(f"PATCHED: pass_through translate_anthropic_tool_choice_to_openai in {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
