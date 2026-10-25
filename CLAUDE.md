# CLAUDE.md

Guidance for working with this repository.

## Project Overview

AI Proxy Gateway routing **Claude Code** through **LiteLLM** to NVIDIA NIM, Agnes AI, SenseNova, Google Gemini, AMD Radeon. Single Gunicorn worker (`--num_workers 1`), in-memory router state (no Redis/Postgres). Binds `127.0.0.1:4000` on host (container `0.0.0.0:4000`). Pinned `litellm:1.105.1`. Four Nemotron thinking-stream defects fixed upstream — no local patch, no build-time verification step.

**Single-user.** No worker pool, no autoscaling, no per-deployment `rpm`/`tpm`. Routing exists for failover/latency hygiene, not throughput.

## Architecture

**Model aliases → provider deployments:**

| Virtual Model              | Deployment 1                                                | Deployment 2                                               |
| -------------------------- | ----------------------------------------------------------- | ---------------------------------------------------------- |
| `claude-opus-5-5`          | `nvidia_nim/nvidia/nemotron-3-ultra-550b-a55b` (key 1)      | `openai/Qwen3.8-27B` (AMD Radeon)                          |
| `claude-sonnet-5`          | `nvidia_nim/nvidia/nemotron-3.5-super-vl-120b-a12b` (key 2) | `openai/agnes-2.5-flash` (Agnes AI)                        |
| `claude-haiku-5-5`         | `nvidia_nim/nvidia/nemotron-3.5-lightning-30b-a3b` (key 1)  | `nvidia_nim/nvidia/nemotron-3.5-lightning-30b-a3b` (key 2) |
| `gemini-3.5-flash-lite`    | `gemini/gemini-3.5-flash-lite` (key 1)                      | `gemini/gemini-3.5-flash-lite` (key 2)                     |
| `sensenova-6.8-flash-lite` | `openai/sensenova-6.8-flash-lite` (SenseNova)               | —                                                          |

**Fallback chain (identical for all Claude aliases):**

1. `gemini-3.5-flash-lite`
2. `sensenova-6.8-flash-lite`

**Router settings:** `routing_strategy: simple-shuffle`, `num_retries: 1`, `cooldown_time: 50`, `allowed_fails: 3`, `fallbacks` nested under `router_settings` (top-level is silently dropped).

**Timeouts:** `request_timeout: 60` is the global cap. Per-deployment `timeout` (20–30s) and `stream_timeout` (8–10s) are set on every deployment, so a stalled provider is cut off before the global cap binds. `keepalive_seconds: 15` on NIM deployments prevents idle-drop during thinking gaps. Local response cache is initialized (`cache: True`, `type: local`, `ttl: 300`) but no call types are cached (`supported_call_types: []`) because caching streaming SSE responses corrupts `content_block` structure (text instead of thinking). Theoretical worst case before fallback: one attempt at per-deployment `timeout` (~30s) + fallback latency ≈ 30–90s (down from ~6 min at the old 120s × 3 attempts).

## Key Behaviors

- `fallbacks` **must be under `router_settings`** — top-level is parsed but silently dropped by `proxy_server.load_config()`.
- `order` **must be inside `litellm_params`** — top-level `order` is ignored by `litellm.utils._get_deployment_order`.
- `rpm`/`tpm` at deployment top-level are promoted into `litellm_params`; not used here.
- `additional_drop_params: ["tools[*].strict"]` on Gemini deployments strips `strict: null`.
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
- **Bump LiteLLM pin**: see "Image pin & streaming verification" — verify step gates the build
- **Monitor**: `docker compose logs -f`

## Image Pin & Streaming Verification

Image pinned to `docker.litellm.ai/berriai/litellm:1.105.1`. Four Nemotron thinking-stream defects fixed upstream as of this pin:

| Defect                                                                      | Fixed by                   |
| --------------------------------------------------------------------------- | -------------------------- |
| First block hardcoded as `text`                                             | #33252                     |
| Combined reasoning+text chunk opens `text` block but emits `thinking_delta` | #34795                     |
| Text in combined chunk silently dropped                                     | #34795                     |
| `IndexError` on empty `choices`                                             | `_handle_choiceless_chunk` |

The streaming verification step was removed alongside the patch scripts — the four defects are confirmed fixed in 1.105.1. If a base-image bump reintroduces them, re-add the verification gate before shipping.

**On base-image bump:**

```bash
docker compose build --no-cache litellm && docker compose up -d
# Verify build gate passed (no SKIP/FAIL lines)
# Re-verify live stream:
curl -sN 'http://localhost:4000/v1/messages?beta=true' \
  -H "Content-Type: application/json" -H "x-api-key: $LITELLM_MASTER_KEY" \
  -H "anthropic-version: 2023-06-01" \
  -d '{"model": "claude-opus-5-5", "max_tokens": 512, "stream": true, "messages": [{"role": "user", "content": "Hi"}]}'
```

Expected: first `content_block_start` opens as `thinking`, `thinking_delta` stays in thinking block, no `IndexError`.

`patches/fix_nemotron_thinking_stream.py` was removed — the four defects it addressed are fixed upstream in 1.105.1, so the local patch is no longer needed.
