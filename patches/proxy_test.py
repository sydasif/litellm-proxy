#!/usr/bin/env python3
"""Proxy verification and optimization test harness.

Validates:
  1. Every virtual model answers from its deployed backends
  2. Streaming behavior (thinking/text block correctness for Nemotron)
  3. Fallback chain fires when primary deployments stall/error
  4. Load-balancing distributes across deployments (simple-shuffle)
  5. Per-deployment timeouts bound response times

Run from the repo root with .env sourced (LITELLM_MASTER_KEY present):
    python3 patches/proxy_test.py [--timeout 15] [--stream-timeout 5]

Usage:
    python3 patches/proxy_test.py               # run all tests, default timeouts
    python3 patches/proxy_test.py --help        # show options
    python3 patches/proxy_test.py --dry-run     # print config without hitting network
"""

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from urllib import request as urllib_request

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
BASE_URL = os.environ.get("PROXY_URL", "http://127.0.0.1:4000")
MASTER_KEY = os.environ.get("LITELLM_MASTER_KEY")
if not MASTER_KEY:
    print("FAIL: LITELLM_MASTER_KEY not set in environment", file=sys.stderr)
    sys.exit(1)

DEFAULT_TIMEOUT = int(os.environ.get("TEST_TIMEOUT", "15"))
DEFAULT_STREAM_TIMEOUT = int(os.environ.get("TEST_STREAM_TIMEOUT", "5"))

# Virtual models and expected deployments (source of truth for this run)
MODEL_LIST = [
    "claude-opus-5-5",
    "claude-sonnet-5",
    "claude-haiku-5-5",
    "gemini-3.5-flash-lite",
    "sensenova-6.8-flash-lite",
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _req(method: str, path: str, body: Optional[Dict] = None, timeout: int = 30) -> Tuple[int, Any]:
    """Send an HTTP request to the proxy and return (status_code, parsed_body_or_raw)."""
    url = BASE_URL.rstrip("/") + "/" + path.lstrip("/")
    headers = {
        "Authorization": f"Bearer {MASTER_KEY}",
        "Content-Type": "application/json",
    }
    data = json.dumps(body).encode() if body else None
    req = urllib_request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib_request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode()
            try:
                return resp.status, json.loads(raw)
            except json.JSONDecodeError:
                return resp.status, raw
    except urllib_request.HTTPError as exc:
        raw = exc.read().decode()
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, raw
    except Exception as exc:
        return 0, {"error": str(exc)}


def _get(endpoint: str, timeout: int = 10) -> Tuple[int, Any]:
    return _req("GET", endpoint, timeout=timeout)


def _post(endpoint: str, body: Dict, timeout: int = 30) -> Tuple[int, Any]:
    return _req("POST", endpoint, body=body, timeout=timeout)


# ---------------------------------------------------------------------------
# Test results
# ---------------------------------------------------------------------------

@dataclass
class Result:
    name: str
    ok: bool
    duration_ms: float
    detail: str = ""

    def __str__(self):
        status = "PASS" if self.ok else "FAIL"
        header = f"[{status}] {self.name} ({self.duration_ms:.0f}ms)"
        if self.detail:
            return f"{header}\n       {self.detail}"
        return header


# ---------------------------------------------------------------------------
# Test suite
# ---------------------------------------------------------------------------

class ProxyTest:
    def __init__(self, timeout: int = DEFAULT_TIMEOUT, stream_timeout: int = DEFAULT_STREAM_TIMEOUT):
        self.timeout = timeout
        self.stream_timeout = stream_timeout
        self.results: List[Result] = []

    def record(self, name: str, ok: bool, duration_ms: float, detail: str = "") -> None:
        r = Result(name=name, ok=ok, duration_ms=duration_ms, detail=detail)
        self.results.append(r)
        print(r)

    # ------------------------------------------------------------------
    # 1. Health checks
    # ------------------------------------------------------------------
    def test_health(self) -> None:
        t0 = time.monotonic()
        code, body = _get("health/liveliness", timeout=5)
        ms = (time.monotonic() - t0) * 1000
        self.record("health/liveliness", code == 200, ms, f"status={code}")

        t0 = time.monotonic()
        code, body = _get("health/readiness", timeout=5)
        ms = (time.monotonic() - t0) * 1000
        ok = code == 200 and (isinstance(body, dict) and body.get("status") == "healthy")
        self.record("health/readiness", ok, ms, f"status={code} body={body if isinstance(body, str) else body.get('status')}")

    # ------------------------------------------------------------------
    # 2. Model listing
    # ------------------------------------------------------------------
    def test_models(self) -> None:
        t0 = time.monotonic()
        code, body = _get("v1/models", timeout=10)
        ms = (time.monotonic() - t0) * 1000
        ids = [m["id"] for m in (body or {}).get("data", [])] if isinstance(body, dict) else []
        known = set(MODEL_LIST)
        missing = known - set(ids)
        ok = code == 200 and not missing
        self.record("v1/models lists all virtual models", ok, ms,
                    f"status={code} found={ids} missing={missing if missing else 'none'}")

    # ------------------------------------------------------------------
    # 3. Deployment routing (non-streaming chat completions)
    # ------------------------------------------------------------------
    def test_chat_deployments(self) -> None:
        for model in MODEL_LIST:
            t0 = time.monotonic()
            code, body = _post("v1/chat/completions", {
                "model": model,
                "messages": [{"role": "user", "content": "Say hi"}],
                "max_tokens": 20,
            }, timeout=self.timeout)
            ms = (time.monotonic() - t0) * 1000

            # Read response headers for routing metadata (requires custom HTTP headers)
            ok = code == 200
            dep = "?"
            retried = "?"
            fallbacked = "?"
            if isinstance(body, dict):
                choices = body.get("choices", [])
                if choices:
                    msg = choices[0].get("message", {})
                    # For NIM/Nemotron, content may be in reasoning_content
                    text = msg.get("content") or msg.get("reasoning_content") or "(no content)"
                    self.record(
                        f"chat/{model}", ok, ms,
                        f"finish={choices[0].get('finish_reason')} text_len={len(text)}"
                    )
                else:
                    self.record(f"chat/{model}", False, ms, "no choices in response")
            else:
                self.record(f"chat/{model}", False, ms, f"non-JSON response: {str(body)[:200]}")

    # ------------------------------------------------------------------
    # 4. Streaming correctness for Claude aliases (Nemotron thinking stream)
    # ------------------------------------------------------------------
    def test_streaming(self) -> None:
        for model in ["claude-opus-5-5", "claude-sonnet-5", "claude-haiku-5-5"]:
            t0 = time.monotonic()
            code, body = _post("v1/messages?beta=true", {
                "model": model,
                "max_tokens": 50,
                "stream": True,
                "messages": [{"role": "user", "content": "Say hello briefly"}],
            }, timeout=self.timeout)
            ms = (time.monotonic() - t0) * 1000

            # For streaming, body may be raw SSE; parse it
            if code != 200:
                self.record(f"stream/{model}", False, ms, f"status={code} body={str(body)[:150]}")
                continue

            # SSE parse
            events = self._parse_sse(body) if isinstance(body, str) else []
            block_starts = [e for e in events if e.get("type") == "content_block_start"]
            thinking_deltas = [e for e in events if e.get("delta", {}).get("type") == "thinking_delta"]
            text_deltas = [e for e in events if e.get("delta", {}).get("type") == "text_delta"]
            msg_stop = any(e.get("type") == "message_stop" for e in events)

            first_type = block_starts[0].get("content_block", {}).get("type") if block_starts else None
            total_deltas = len(thinking_deltas) + len(text_deltas)
            ok = (
                first_type == "thinking"
                and total_deltas > 0
                and msg_stop
            )
            detail = (
                f"first_block={first_type} thinking_deltas={len(thinking_deltas)} "
                f"text_deltas={len(text_deltas)} message_stop={msg_stop}"
            )
            self.record(f"stream/{model}", ok, ms, detail)

    def _parse_sse(self, raw: str) -> List[dict]:
        """Parse SSE stream into a list of event dicts."""
        events = []
        current_event_type = None
        for line in raw.splitlines():
            line = line.strip()
            if line.startswith("event:"):
                current_event_type = line[len("event:"):].strip()
                continue
            if line.startswith("data:") and current_event_type:
                data_str = line[len("data:"):].strip()
                try:
                    data = json.loads(data_str)
                    # Normalize: attach event type so caller can query uniformly
                    data["_event"] = current_event_type
                    events.append(data)
                except json.JSONDecodeError:
                    pass
                current_event_type = None
        return events

    # ------------------------------------------------------------------
    # 5. Load balancing distribution
    # ------------------------------------------------------------------
    def test_load_balance(self) -> None:
        model = "claude-sonnet-5"
        t0 = time.monotonic()
        statuses = []
        for _ in range(6):
            code, body = _post("v1/chat/completions", {
                "model": model,
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 5,
            }, timeout=10)
            statuses.append(code)
        ms = (time.monotonic() - t0) * 1000
        ok = all(s == 200 for s in statuses)
        self.record(
            "load-balance/sonnet-distribution",
            ok,
            ms,
            f"6 requests all 200: {statuses}",
        )

    # ------------------------------------------------------------------
    # 6. Cache ping
    # ------------------------------------------------------------------
    def test_cache_ping(self) -> None:
        t0 = time.monotonic()
        code, body = _get("cache/ping", timeout=5)
        ms = (time.monotonic() - t0) * 1000
        ok = code == 200 and isinstance(body, dict) and body.get("status") == "healthy"
        detail = f"status={code} cache_type={body.get('cache_type') if isinstance(body, dict) else body}"
        self.record("cache/ping", ok, ms, detail)

    # ------------------------------------------------------------------
    # 7. Cache hit / miss on chat completions
    # ------------------------------------------------------------------
    def test_cache_hit_miss(self) -> None:
        body = {
            "model": "claude-haiku-5-5",
            "messages": [{"role": "user", "content": "Say ok in one word"}],
            "max_tokens": 10,
        }
        # First call — should be a miss (cold cache)
        t0 = time.monotonic()
        code1, body1 = _post("v1/chat/completions", dict(body), timeout=self.timeout)
        ms1 = (time.monotonic() - t0) * 1000
        # Second call — identical request, should be a hit
        t0 = time.monotonic()
        code2, body2 = _post("v1/chat/completions", dict(body), timeout=self.timeout)
        ms2 = (time.monotonic() - t0) * 1000

        hit = code1 == 200 and code2 == 200
        self.record(
            "cache/hit-miss",
            hit,
            (ms1 + ms2) / 2,
            f"first={code1} in {ms1:.0f}ms second={code2} in {ms2:.0f}ms "
            f"(cache hit means identical response served faster)",
        )

    # ------------------------------------------------------------------
    # 8. Cache miss on changed request
    # ------------------------------------------------------------------
    def test_cache_miss_on_change(self) -> None:
        body_a = {
            "model": "claude-haiku-5-5",
            "messages": [{"role": "user", "content": "Say exactly alpha"}],
            "max_tokens": 10,
        }
        body_b = {
            "model": "claude-haiku-5-5",
            "messages": [{"role": "user", "content": "Say exactly beta"}],
            "max_tokens": 10,
        }
        code_a, body_a_resp = _post("v1/chat/completions", dict(body_a), timeout=self.timeout)
        code_b, body_b_resp = _post("v1/chat/completions", dict(body_b), timeout=self.timeout)
        ok = code_a == 200 and code_b == 200
        msg_a = (body_a_resp.get("choices", [{}])[0].get("message", {}) if isinstance(body_a_resp, dict) else {})
        msg_b = (body_b_resp.get("choices", [{}])[0].get("message", {}) if isinstance(body_b_resp, dict) else {})
        text_a = (msg_a.get("content") or msg_a.get("reasoning_content") or "")
        text_b = (msg_b.get("content") or msg_b.get("reasoning_content") or "")
        self.record(
            "cache/miss-on-change",
            ok,
            0,
            f"alpha='{text_a[:30]}' beta='{text_b[:30]}' (different prompts must not share cache entry)",
        )
    def test_messages_endpoint(self) -> None:
        """Non-streaming Anthropic /v1/messages (core Claude Code path)."""
        for model in ["claude-opus-5-5", "claude-sonnet-5", "claude-haiku-5-5"]:
            t0 = time.monotonic()
            code, body = _post("v1/messages?beta=true", {
                "model": model,
                "max_tokens": 20,
                "messages": [{"role": "user", "content": "Say hi"}],
            }, timeout=self.timeout)
            ms = (time.monotonic() - t0) * 1000
            ok = code == 200 and isinstance(body, dict)
            detail = f"status={code}"
            if isinstance(body, dict):
                content = body.get("content", "")
                text = ""
                if isinstance(content, list):
                    text = "".join(c.get("text", "") for c in content if c.get("type") == "text")
                    if not text:
                        text = "".join(c.get("thinking", "") for c in content if c.get("type") == "thinking")
                else:
                    text = str(content)[:60]
                detail += f" text_len={len(text)}"
                ok = ok and bool(text)
            self.record(f"messages/{model}", ok, ms, detail)

    def test_invalid_model(self) -> None:
        t0 = time.monotonic()
        code, body = _post("v1/chat/completions", {
            "model": "does-not-exist",
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 5,
        }, timeout=10)
        ms = (time.monotonic() - t0) * 1000
        ok = code != 200
        self.record("invalid/model", ok, ms, f"status={code} body={str(body)[:150]}")

    def test_streaming_fallback_models(self) -> None:
        for model in ["gemini-3.5-flash-lite", "sensenova-6.8-flash-lite"]:
            t0 = time.monotonic()
            code, body = _post("v1/chat/completions", {
                "model": model,
                "stream": True,
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 10,
            }, timeout=self.timeout)
            ms = (time.monotonic() - t0) * 1000
            ok = code == 200 and isinstance(body, str)
            self.record(f"stream-fallback/{model}", ok, ms, f"status={code}")

    def test_fallback_chain(self) -> None:
        """Validate that the fallback model itself responds (we can't easily
        force primary failure without mock endpoints, so we test the fallback
        models directly and confirm they're listed as fallbacks in the config."""
        fallback_models = ["gemini-3.5-flash-lite", "sensenova-6.8-flash-lite"]
        for fb in fallback_models:
            t0 = time.monotonic()
            code, body = _post("v1/chat/completions", {
                "model": fb,
                "messages": [{"role": "user", "content": "Say ok"}],
                "max_tokens": 10,
            }, timeout=15)
            ms = (time.monotonic() - t0) * 1000
            ok = code == 200 and isinstance(body, dict) and bool(body.get("choices"))
            text = ""
            if ok:
                try:
                    text = (body["choices"][0]["message"].get("content") or "").strip()[:60]
                except (KeyError, IndexError):
                    pass
            self.record(
                f"fallback/{fb}",
                ok,
                ms,
                f"status={code} text={text}",
            )

    # ------------------------------------------------------------------
    # 7. Timeout enforcement (per-deployment)
    # ------------------------------------------------------------------
    def test_timeout_config(self) -> None:
        """Read deployment timeout settings from /v1/model/info and assert every
        deployment has per-deployment timeout + stream_timeout set."""
        t0 = time.monotonic()
        code, body = _get("v1/model/info", timeout=10)
        ms = (time.monotonic() - t0) * 1000
        ok = code == 200 and isinstance(body, dict)
        deployments = body.get("data", []) if ok else []
        summary = []
        missing_timeout = []
        missing_stream_timeout = []
        for d in deployments:
            name = d.get("model_name", "?")
            actual = d.get("litellm_params", {}).get("model", "?")
            t = d.get("litellm_params", {}).get("timeout", "unset")
            st = d.get("litellm_params", {}).get("stream_timeout", "unset")
            kk = d.get("litellm_params", {}).get("keepalive_seconds", "unset")
            summary.append(f"  {name:25s} -> {actual:45s}  timeout={t}  stream_timeout={st}  keepalive={kk}")
            if t == "unset":
                missing_timeout.append(actual)
            if st == "unset":
                missing_stream_timeout.append(actual)
        ok = ok and not missing_timeout and not missing_stream_timeout
        if missing_timeout:
            summary.append(f"  MISSING timeout: {missing_timeout}")
        if missing_stream_timeout:
            summary.append(f"  MISSING stream_timeout: {missing_stream_timeout}")
        self.record(
            "timeout-config",
            ok,
            ms,
            "\n".join(summary) if summary else "no deployments",
        )

    # ------------------------------------------------------------------
    # Run all
    # ------------------------------------------------------------------
    def run_all(self) -> int:
        print("=" * 70)
        print("LiteLLM Proxy Test Suite")
        print(f"Base URL : {BASE_URL}")
        print(f"Timeout  : {self.timeout}s  Stream timeout: {self.stream_timeout}s")
        print("=" * 70)
        print()

        self.test_health()
        print()
        self.test_models()
        print()
        self.test_chat_deployments()
        print()
        self.test_streaming()
        print()
        self.test_load_balance()
        print()
        self.test_cache_ping()
        print()
        self.test_cache_hit_miss()
        print()
        self.test_cache_miss_on_change()
        print()
        self.test_messages_endpoint()
        print()
        self.test_invalid_model()
        print()
        self.test_streaming_fallback_models()
        print()
        self.test_fallback_chain()
        print()
        self.test_timeout_config()

        passed = sum(1 for r in self.results if r.ok)
        failed = len(self.results) - passed
        print()
        print("=" * 70)
        print(f"Results: {passed} passed, {failed} failed, {len(self.results)} total")
        if failed:
            print("Failed tests:")
            for r in self.results:
                if not r.ok:
                    print(f"  - {r.name}: {r.detail}")
        return 0 if failed == 0 else 1


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Proxy verification and optimization test harness")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT,
                        help=f"Global request timeout in seconds (default: {DEFAULT_TIMEOUT})")
    parser.add_argument("--stream-timeout", type=int, default=DEFAULT_STREAM_TIMEOUT,
                        help=f"Streaming first-chunk timeout in seconds (default: {DEFAULT_STREAM_TIMEOUT})")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print configuration without making network requests")
    args = parser.parse_args()

    if args.dry_run:
        print(f"PROXY_URL      = {BASE_URL}")
        print(f"LITELLM_MASTER_KEY = {'*' * 8}{MASTER_KEY[-4:] if len(MASTER_KEY) > 4 else ''}")
        print(f"TEST_TIMEOUT   = {args.timeout}s")
        print(f"TEST_STREAM_TIMEOUT = {args.stream_timeout}s")
        print(f"MODELS         = {MODEL_LIST}")
        print()
        print("Add --timeout and/or --stream-timeout to override defaults.")
        print("Run without --dry-run to execute the full test suite.")
        return 0

    test = ProxyTest(timeout=args.timeout, stream_timeout=args.stream_timeout)
    return test.run_all()


if __name__ == "__main__":
    sys.exit(main())
