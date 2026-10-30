#!/usr/bin/env python3
"""Make Gemini context-cache write failures fail open.

LiteLLM 1.105.1 raises from ``ContextCachingEndpoints.check_and_create_cache``
when Google rejects a ``POST /v1beta/cachedContents``. On Gemini free-tier keys
the explicit-cache storage quota is ``limit=0``, so every Claude Code request
that carries Anthropic ``cache_control`` markers is answered with::

    "code": 429, "status": "RESOURCE_EXHAUSTED"
    TotalCachedContentStorageTokensPerModelFreeTier limit exceeded
    for model gemini-3.5-flash-lite: limit=0, requested=14743

LiteLLM maps that to ``litellm.RateLimitError``, which cools every Gemini
deployment for ``cooldown_time`` seconds and returns 429 to the caller.

A cache write is an optimization: a rejected or timed-out write must continue
uncached, not fail the request. This patch rewrites the two cache-write
``except`` blocks (sync + async) in the installed package.

Two invariants the patch must preserve, or the request ships broken:

* ``tools`` / ``tool_choice`` are popped from ``optional_params`` before the
  POST (lines 335-336 sync, 495-496 async) and must be restored on the failure
  path, otherwise every tool definition is dropped;
* the original ``messages`` must be returned, not ``non_cached_messages``,
  because no cache name is returned to reference the cached prefix.

Every run is idempotent: on an already-patched tree it reports ALREADY PATCHED,
writes nothing and exits 0. If the anchor no longer matches exactly twice (base
image bump), it reports SKIP and exits 1 so the build fails loudly.

Run from inside the image at build time::

    python3 /app/patches/fix_gemini_context_caching.py
"""

from __future__ import annotations

import sys
from pathlib import Path

MARKER = "Gemini context cache write rejected"

TARGET_RELATIVE = Path("llms/vertex_ai/context_caching/vertex_ai_context_caching.py")

ANCHOR = """\
        except httpx.HTTPStatusError as err:
            error_code: Final = err.response.status_code
            raise VertexAIError(status_code=error_code, message=err.response.text)
        except httpx.TimeoutException:
            raise VertexAIError(status_code=408, message="Timeout error occurred.")
"""

REPLACEMENT = """\
        except httpx.HTTPStatusError as err:
            # Gemini context caching is an optimization, not a request
            # requirement. A rejected cache write (e.g. 429
            # TotalCachedContentStorageTokensPerModelFreeTier limit=0) must
            # degrade to an uncached request, not fail the caller.
            verbose_logger.warning(
                "Gemini context cache write rejected (HTTP %s) - continuing uncached: %s",
                err.response.status_code,
                err.response.text,
            )
            optional_params["tools"] = tools
            if tool_choice is not None:
                optional_params["tool_choice"] = tool_choice
            return messages, optional_params, None
        except httpx.TimeoutException:
            verbose_logger.warning(
                "Gemini context cache write timed out - continuing uncached"
            )
            optional_params["tools"] = tools
            if tool_choice is not None:
                optional_params["tool_choice"] = tool_choice
            return messages, optional_params, None
"""


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
    if occurrences != 2:
        print(f"SKIP: anchor matched {occurrences} times (expected 2) in {path}")
        print("Re-target patches/fix_gemini_context_caching.py for this base image.")
        return 1

    tmp_path = path.with_suffix(".py.tmp")
    tmp_path.write_text(source.replace(ANCHOR, REPLACEMENT, 2), encoding="utf-8")
    tmp_path.replace(path)
    print(f"PATCHED: 2 cache-write sites in {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
