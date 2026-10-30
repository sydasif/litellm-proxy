#!/usr/bin/env python3
"""Build gate for patches/fix_gemini_context_caching.py.

Drives the real ``ContextCachingEndpoints`` with a stubbed HTTP client and
asserts that a rejected or timed-out Gemini cache write continues uncached:

* no exception escapes ``check_and_create_cache``;
* the returned ``cached_content`` is ``None``;
* ``tools`` / ``tool_choice`` are restored on ``optional_params`` (they are
  popped before the POST, so a naive early return silently drops every tool);
* the original ``messages`` come back untouched (the full prefix still has to
  be sent when no cache is created);
* the cache-write POST was actually attempted, which guards against a false
  pass from an early return upstream.

A positive control runs first: a stubbed successful POST must still return the
cache name, proving the harness reaches the patched code path.

Exit code 0 on success, 1 on any regression, so the Docker build fails loudly.

Run from inside the image at build time::

    python3 /app/patches/verify_gemini_cache_fallback.py
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from litellm.llms.custom_httpx.http_handler import AsyncHTTPHandler, HTTPHandler
from litellm.llms.vertex_ai.context_caching.vertex_ai_context_caching import (
    ContextCachingEndpoints,
)

MODEL = "gemini-3.5-flash-lite"
PROVIDER = "gemini"
CACHE_URL = "https://generativelanguage.googleapis.com/v1beta/cachedContents"
CACHED_NAME = "cachedContents/abc"

FREE_TIER_MESSAGE = (
    "TotalCachedContentStorageTokensPerModelFreeTier limit exceeded "
    f"for model {MODEL}: limit=0, requested=3019"
)

MESSAGES = [
    {
        "role": "system",
        "content": [
            {
                "type": "text",
                "text": "word " * 3000,
                "cache_control": {"type": "ephemeral"},
            }
        ],
    }
]

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "Read",
            "description": "Read a file from the local filesystem.",
            "parameters": {
                "type": "object",
                "properties": {"file_path": {"type": "string"}},
                "required": ["file_path"],
            },
        },
    }
]


def _http_error(status: int, body: dict[str, Any]) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", CACHE_URL)
    return httpx.HTTPStatusError(
        f"Client error '{status} Too Many Requests'",
        request=request,
        response=httpx.Response(status, request=request, json=body),
    )


def _quota_body() -> dict[str, Any]:
    return {
        "error": {
            "code": 429,
            "message": FREE_TIER_MESSAGE,
            "status": "RESOURCE_EXHAUSTED",
        }
    }


def _get_ok(*args: Any, **kwargs: Any) -> httpx.Response:
    return httpx.Response(
        200,
        request=httpx.Request("GET", CACHE_URL),
        json={"cachedContents": []},
    )


async def _aget_ok(*args: Any, **kwargs: Any) -> httpx.Response:
    return _get_ok()


def _post_ok(*args: Any, **kwargs: Any) -> httpx.Response:
    return httpx.Response(
        200,
        request=httpx.Request("POST", CACHE_URL),
        json={"name": CACHED_NAME, "model": f"models/{MODEL}"},
    )


def _post_429(*args: Any, **kwargs: Any) -> httpx.Response:
    raise _http_error(429, _quota_body())


async def _apost_429(*args: Any, **kwargs: Any) -> httpx.Response:
    raise _http_error(429, _quota_body())


def _post_timeout(*args: Any, **kwargs: Any) -> httpx.Response:
    raise httpx.TimeoutException("cache write timed out")


class _Recorder:
    """Counts cache-write POST attempts so an early return cannot pass silently."""

    def __init__(self) -> None:
        self.post_calls = 0


class _QuietLogger:
    """Stands in for ``Logging``; the cache path only calls pre/post hooks."""

    def pre_call(self, *args: Any, **kwargs: Any) -> None: ...

    def post_call(self, *args: Any, **kwargs: Any) -> None: ...


def _kwargs(client: Any) -> dict[str, Any]:
    return {
        "messages": [
            {
                "role": "system",
                "content": [dict(block) for block in MESSAGES[0]["content"]],
            }
        ],
        "optional_params": {"tools": [dict(tool) for tool in TOOLS]},
        "api_key": "dummy",
        "api_base": None,
        "model": MODEL,
        "client": client,
        "timeout": None,
        "logging_obj": _QuietLogger(),
        "custom_llm_provider": PROVIDER,
        "vertex_project": None,
        "vertex_location": None,
        "vertex_auth_header": None,
    }


def _check(
    result: tuple[list[dict[str, Any]], dict[str, Any], str | None],
    kwargs: dict[str, Any],
    recorder: _Recorder,
    expect_cached: str | None,
    require_tools: bool,
) -> list[str]:
    problems: list[str] = []
    if recorder.post_calls != 1:
        problems.append(
            f"expected exactly 1 cache-write POST, saw {recorder.post_calls} "
            "(prompt rejected before the write, or harness drift)"
        )

    returned_messages, returned_params, cached_content = result
    if expect_cached is not None:
        if cached_content != expect_cached:
            problems.append(
                f"expected cached_content={expect_cached!r}, got {cached_content!r}"
            )
    elif cached_content is not None:
        problems.append(f"expected cached_content=None, got {cached_content!r}")

    if require_tools:
        if "tools" not in returned_params:
            problems.append("tools were dropped from optional_params")
        elif returned_params["tools"] != TOOLS:
            problems.append("tools were altered on the failure path")
        if returned_messages is not kwargs["messages"]:
            problems.append(
                "original messages were not returned (prefix would be lost)"
            )

    return problems


def _run_sync(
    post: Callable[..., httpx.Response],
    expect_cached: str | None,
    require_tools: bool,
) -> list[str]:
    recorder = _Recorder()

    def counting_post(*args: Any, **kwargs: Any) -> httpx.Response:
        recorder.post_calls += 1
        return post(*args, **kwargs)

    handler = HTTPHandler()
    handler.get = _get_ok  # type: ignore[method-assign]
    handler.post = counting_post  # type: ignore[method-assign]

    kwargs = _kwargs(handler)
    try:
        result = ContextCachingEndpoints().check_and_create_cache(**kwargs)
    except Exception as exc:  # noqa: BLE001 - the gate reports any escape
        return [f"raised {type(exc).__name__}: {exc}"]
    return _check(result, kwargs, recorder, expect_cached, require_tools)


def _run_async(
    post: Callable[..., Awaitable[httpx.Response]],
    expect_cached: str | None,
    require_tools: bool,
) -> list[str]:
    recorder = _Recorder()

    async def counting_post(*args: Any, **kwargs: Any) -> httpx.Response:
        recorder.post_calls += 1
        return await post(*args, **kwargs)

    # The async path discards any client that is not an AsyncHTTPHandler
    # (vertex_ai_context_caching.py:517), so the stub must be one.
    handler = AsyncHTTPHandler()
    handler.get = _aget_ok  # type: ignore[method-assign]
    handler.post = counting_post  # type: ignore[method-assign]

    kwargs = _kwargs(handler)
    try:
        result = asyncio.run(
            ContextCachingEndpoints().async_check_and_create_cache(**kwargs)
        )
    except Exception as exc:  # noqa: BLE001 - the gate reports any escape
        return [f"raised {type(exc).__name__}: {exc}"]
    return _check(result, kwargs, recorder, expect_cached, require_tools)


def main() -> int:
    cases = [
        (
            "sync success (positive control)",
            _run_sync(_post_ok, CACHED_NAME, require_tools=False),
        ),
        ("sync 429 free-tier quota", _run_sync(_post_429, None, require_tools=True)),
        (
            "sync cache-write timeout",
            _run_sync(_post_timeout, None, require_tools=True),
        ),
        ("async 429 free-tier quota", _run_async(_apost_429, None, require_tools=True)),
    ]

    failures = 0
    for name, problems in cases:
        if problems:
            failures += 1
            print(f"FAIL [{name}]")
            for problem in problems:
                print(f"  - {problem}")
        else:
            print(f"PASS [{name}]")

    if failures:
        print(f"FAIL: {failures}/{len(cases)} cache-write cases regressed")
        return 1

    print(f"PASS: {len(cases)}/{len(cases)} cache-write cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
