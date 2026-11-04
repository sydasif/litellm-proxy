#!/usr/bin/env python3
"""Build gate for patches/fix_pass_through_tool_choice_string.py.

Positive control: a string tool_choice ("required", "auto", "none") is
normalized without a TypeError. Negative control: a dict tool_choice still
routes through the original dict path and returns the mapped value.

Run from inside the image at build time::

    python3 /app/patches/verify_pass_through_tool_choice.py
"""
from __future__ import annotations

import sys

from litellm.llms.anthropic.pass_through.adapters.transformation import (
    LiteLLMAnthropicMessagesAdapter,
)


def main() -> int:
    adapter = LiteLLMAnthropicMessagesAdapter()

    cases = [
        ("required", "required"),
        ("auto", "auto"),
        ("none", "none"),
    ]

    problems: list[str] = []
    for tool_choice, expected in cases:
        try:
            result = adapter.translate_anthropic_tool_choice_to_openai(tool_choice)
        except TypeError as exc:
            problems.append(f"string tool_choice={tool_choice!r} raised TypeError: {exc}")
            continue
        if result != expected:
            problems.append(
                f"string tool_choice={tool_choice!r}: expected {expected!r}, got {result!r}"
            )

    # Dict path must be untouched.
    dict_cases = [
        ({"type": "auto"}, "auto"),
        ({"type": "any"}, "required"),
        ({"type": "none"}, "none"),
    ]
    for dict_choice, expected in dict_cases:
        try:
            result = adapter.translate_anthropic_tool_choice_to_openai(dict_choice)
        except Exception as exc:
            problems.append(
                f"dict tool_choice {dict_choice!r} raised {type(exc).__name__}: {exc}"
            )
            continue
        if result != expected:
            problems.append(
                f"dict tool_choice {dict_choice!r}: expected {expected!r}, got {result!r}"
            )

    if problems:
        for p in problems:
            print("FAIL:", p)
        return 1

    print("PASS: string tool_choice normalized; dict tool_choice unchanged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
