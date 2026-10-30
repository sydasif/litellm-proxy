# CLAUDE.md

Guidance for working with this repository.

## Project Overview

AI Proxy Gateway routing **Claude Code** through **LiteLLM** to NVIDIA NIM, Agnes AI, SenseNova, Google Gemini, AMD Radeon. Single Gunicorn worker (`--num_workers 1`), in-memory router state (no Redis/Postgres). Binds `127.0.0.1:4000` on host (container `0.0.0.0:4000`). Pinned `litellm:1.105.1`. Four Nemotron thinking-stream defects fixed upstream; one local patch (Gemini cache-write fail-open) is applied and gated at build time.

**Single-user.** No worker pool, no autoscaling, no per-deployment `rpm`/`tpm`. Routing exists for failover/latency hygiene, not throughput.

## Architecture

**Model aliases → provider deployments:**

| Virtual Model              | Deployment 1                                                | Deployment 2                                                |
| -------------------------- | ----------------------------------------------------------- | ----------------------------------------------------------- |
| `claude-opus-5-5`          | `nvidia_nim/nvidia/nemotron-3-ultra-550b-a55b` (key 1)      | `nvidia_nim/nvidia/nemotron-3-ultra-550b-a55b` (key 2)      |
| `claude-sonnet-5`          | `nvidia_nim/nvidia/nemotron-3.5-super-vl-120b-a12b` (key 1) | `nvidia_nim/nvidia/nemotron-3.5-super-vl-120b-a12b` (key 2) |
| `claude-haiku-5-5`         | `nvidia_nim/nvidia/nemotron-3.5-lightning-30b-a3b` (key 1)  | `nvidia_nim/nvidia/nemotron-3.5-lightning-30b-a3b` (key 2)  |
| `gemini-3.5-flash-lite`    | `gemini/gemini-3.5-flash-lite` (key 1)                      | `gemini/gemini-3.5-flash-lite` (key 2)                      |
| `sensenova-6.8-flash-lite` | `openai/sensenova-6.8-flash-lite` (SenseNova)               | —                                                           |
| `agnes-2.5-flash`          | `openai/agnes-2.5-flash` (Agnes AI)                         | —                                                           |
| `qwen3.8-27b`              | `openai/Qwen3.8-27B` (AMD Radeon)                           | —                                                           |

**Fallback chain (identical for all Claude aliases):**

1. `agnes-2.5-flash`
2. `sensenova-6.8-flash-lite`
3. `gemini-3.5-flash-lite`

**Fallback mesh:** every model group falls back to every other group. Order within each list is the priority (agnes first). `max_fallbacks` defaults to 5, so a full mesh (≤4 targets) terminates on its own.

**Router settings:** `routing_strategy: simple-shuffle`, `num_retries: 1`, `cooldown_time: 30`, `optional_pre_call_checks: ["enforce_model_rate_limits"]`, `fallbacks` nested under `router_settings` (top-level is silently dropped).

**Timeouts:** `request_timeout: 90` is the global cap; no per-deployment `timeout`/`stream_timeout`/`keepalive_seconds` are set (removed in `8fc9bfb`), so a stalled provider is cut off by the global cap. `http2: True` is re-enabled for outbound provider traffic. Local response cache is initialized (`cache: True`, `type: local`, `ttl: 300`) but no call types are cached (`supported_call_types: []`) because caching streaming SSE responses corrupts `content_block` structure (text instead of thinking). Worst case before fallback: one attempt under the 90s global cap + fallback latency.

**Gemini prompt caching:** Claude Code's `cache_control` markers still trigger an explicit `cachedContents` write for `gemini-3.5-flash-lite`. On free-tier keys the storage quota is `limit=0`, so the write is always rejected; the local patch turns that into a warning and an uncached request instead of a 429 (see "Image Pin, Streaming & Local Patches").

**Gemini quotas:** free tier is **15 RPM / 250K TPM / 500 RPD per project, per model**. `GEMINI_API_KEY_1` and `GEMINI_API_KEY_2` are separate Google projects, so the pair doubles each ceiling (30 RPM / 500K TPM / 1000 RPD). Each deployment therefore declares `rpm: 15` and `tpm: 250000`. Malformed-quota 429s from Google name the metric inside the body — `quotaId: ...PerProjectPerModel-FreeTier ... quotaValue: "15"` means RPM, a bare `"Resource has been exhausted"` means TPM.

## Key Behaviors

- `fallbacks` **must be under `router_settings`** — top-level is parsed but silently dropped by `proxy_server.load_config()`.
- `order` **must be inside `litellm_params`** — top-level `order` is ignored by `litellm.utils._get_deployment_order`.
- `rpm`/`tpm` at deployment top-level are promoted into `litellm_params`.
- Claude Code sends `reasoning_effort: high` for Opus; Nemotron rejects it (`high` is not a valid value — only `xhigh`/`medium`/`low`). Dropping the param lets NIM default to `xhigh`.
- **`rpm`/`tpm` are inert without `optional_pre_call_checks: ["enforce_model_rate_limits"]`.** `enable_pre_call_checks` filters context window and RPM only — it never reads TPM. The enforcement hook (`router_utils/pre_call_checks/model_rate_limit_check.py`) is what raises `Model rate limit exceeded` and cools the deployment.
- The hook counts **one increment per upstream attempt**, so `num_retries: 1` spends 2 counts on a throttled request.
- **Every model group needs a fallback entry.** `gemini-3.5-flash-lite` was a fallback target everywhere but had no target of its own, so a quota 429 went straight to the client. The mesh now covers all 7 groups, including `agnes-2.5-flash` and `qwen3.8-27b` which previously had none.
- `additional_drop_params: ["tools[*].strict"]` on Gemini deployments strips `strict: null`.
- `additional_drop_params: ["reasoning_effort"]` on the three NVIDIA NIM deployments drops Claude Code's `reasoning_effort: high`, which Nemotron rejects (it only accepts `xhigh`/`medium`/`low`). NIM then defaults to `xhigh` thinking, so the thinking stream keeps working.
- Gemini cache writes are attempted for Claude Code traffic and **fail open** (local patch): a rejected or timed-out `cachedContents` write continues uncached. `disable_anthropic_gemini_context_caching_transform` is intentionally _not_ set — it would disable explicit caching on paid keys too.
- `/health/liveliness` probes per call (no `background_health_checks`).
- Master key auth only. No virtual keys, no database.

## Development Commands

```bash
# Start (builds image on first run)
docker compose up -d

# Stop
docker compose down

# Rebuild after config/base-image change
docker compose build --no-cache litellm && docker compose up -d

# Restart after config change (no rebuild)
docker compose down && docker compose up -d

# View logs
docker compose logs -f

# Validate config
python -c "import yaml; yaml.safe_load(open('litellm/config.yaml'))"

# Full proxy test suite (health, models, chat, streaming, fallbacks, timeouts)
python3 patches/proxy_test.py --timeout 15 --stream-timeout 8
```

## Testing

```bash
# Verify proxy
curl http://localhost:4000/v1/models -H "Authorization: Bearer $LITELLM_MASTER_KEY"

# Chat completion
curl -X POST http://localhost:4000/v1/chat/completions \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" -H "Content-Type: application/json" \
  -d '{"model": "claude-opus-5-5", "messages": [{"role": "user", "content": "Hello!"}], "max_tokens": 100}'

# Check deployment IDs
curl -s http://localhost:4000/v1/model/info -H "Authorization: Bearer $LITELLM_MASTER_KEY" | \
  python3 -c "import json,sys; [print(f'{m[\"model_name\"]:30s} {m[\"litellm_params\"].get(\"model\",\"?\"):40s}') for m in json.load(sys.stdin)['data']]"

# Load balance check (x-litellm-model-id varies)
for i in {1..6}; do
  curl -s -D - http://127.0.0.1:4000/v1/chat/completions \
    -H "Authorization: Bearer $LITELLM_MASTER_KEY" -H "Content-Type: application/json" \
    -d '{"model": "claude-sonnet-5", "messages": [{"role": "user", "content": "Hi"}], "max_tokens": 10}' | \
  grep "x-litellm-model-id:"
done
```

## Maintenance

- **API keys**: edit `.env`, restart
- **Routing/providers**: edit `litellm/config.yaml`, restart
- **Bump LiteLLM pin**: see "Image Pin, Streaming & Local Patches" — patch + gate steps decide whether the build ships
- **Monitor**: `docker compose logs -f`

## Image Pin, Streaming & Local Patches

Image pinned to `docker.litellm.ai/berriai/litellm:1.105.1`. Four Nemotron thinking-stream defects fixed upstream as of this pin:

| Defect                                                                      | Fixed by                   |
| --------------------------------------------------------------------------- | -------------------------- |
| First block hardcoded as `text`                                             | #33252                     |
| Combined reasoning+text chunk opens `text` block but emits `thinking_delta` | #34795                     |
| Text in combined chunk silently dropped                                     | #34795                     |
| `IndexError` on empty `choices`                                             | `_handle_choiceless_chunk` |

### Local patches applied at build time

| Patch                        | File                                      | Invariant                                                                                                                                      |
| ---------------------------- | ----------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| Gemini cache-write fail-open | `patches/fix_gemini_context_caching.py`   | A rejected (429) or timed-out `cachedContents` write continues uncached; `tools`/`tool_choice` are restored and the full message list is sent. |
| Build gate for the above     | `patches/verify_gemini_cache_fallback.py` | Positive control (successful write returns a cache name) plus sync 429, sync timeout, async 429 must all fail open.                            |

Both run in the `Dockerfile` (`COPY patches/` → patch → verify). A `SKIP` from the patch script or a `FAIL` from the gate aborts the build — that is the signal that the base image moved and the anchors need re-targeting.

The Nemotron streaming verification step was removed alongside `patches/fix_nemotron_thinking_stream.py` — those four defects are confirmed fixed in 1.105.1. If a base-image bump reintroduces them, re-add the verification gate before shipping.

**On base-image bump:**

```bash
docker compose build --no-cache litellm && docker compose up -d
# Verify the build gates passed: patch prints "PATCHED: 2 cache-write sites",
# the gate prints "PASS: 4/4 cache-write cases" (no SKIP/FAIL lines)
# Re-verify live stream:
curl -sN 'http://localhost:4000/v1/messages?beta=true' \
  -H "Content-Type: application/json" -H "x-api-key: $LITELLM_MASTER_KEY" \
  -H "anthropic-version: 2023-06-01" \
  -d '{"model": "claude-opus-5-5", "max_tokens": 512, "stream": true, "messages": [{"role": "user", "content": "Hi"}]}'
```

Expected: first `content_block_start` opens as `thinking`, `thinking_delta` stays in thinking block, no `IndexError`.

For the Gemini patch, the live check is a `/v1/messages?beta=true` call to `gemini-3.5-flash-lite` with a >1024-token `cache_control` block: expect `200`, a `Gemini context cache write rejected ... continuing uncached` warning in the logs, and no `429`/`All deployments ... in cooldown`.

`patches/fix_nemotron_thinking_stream.py` was removed — the four defects it addressed are fixed upstream in 1.105.1, so that local patch is no longer needed. The Gemini cache patch is still required at this pin.
