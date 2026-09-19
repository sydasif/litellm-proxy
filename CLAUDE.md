# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AI Proxy Gateway that routes **Claude Code** through **LiteLLM** to multiple AI backend providers (NVIDIA NIM, Agnes AI, SenseNova, Google Gemini, AMD Radeon) with load balancing and cascading fallbacks. Runs as a multi-worker instance (`--num_workers 2`) with **per-worker** in-memory state — the router's cooldown/usage counters are not shared across workers (no Postgres/Redis). Binds on `0.0.0.0:4000` (host network must isolate this port). Uses Docker Compose to deploy a patched LiteLLM image that fixes Nemotron thinking-stream and empty-choices streaming bugs.

## Architecture

**Model aliasing:** Virtual model names map to real provider models. Clients request the virtual name; LiteLLM routes across deployments per the pool's strategy below.

**Backend deployments:**

| Virtual Model               | Deployment 1                                                               | Deployment 2                                             | Deployment 3 |
| --------------------------- | -------------------------------------------------------------------------- | -------------------------------------------------------- | ------------ |
| `claude-opus-5`             | `nvidia_nim/nvidia/nemotron-3-super-120b-a12b` (key 1, RPM 40)             | —                                                        | —            |
| `claude-sonnet-5`           | `openai/agnes-2.5-flash` (Agnes AI, RPM 20)                                | `openai/sensenova-6.8-flash-lite` (SenseNova, RPM 20)    | —            |
| `claude-haiku-4-5-20251001` | `nvidia_nim/nvidia/nemotron-3-nano-omni-30b-a3b-reasoning` (key 2, RPM 40) | —                                                        | —            |
| `gemini-3.5-flash-lite`     | `gemini/gemini-3.5-flash-lite` (key 1, RPM 15, TPM 250K)                   | `gemini/gemini-3.5-flash-lite` (key 2, RPM 15, TPM 250K) | —            |
| `gemini-3.1-flash-lite`     | `gemini/gemini-3.1-flash-lite` (key 1, RPM 15, TPM 250K)                   | `gemini/gemini-3.1-flash-lite` (key 2, RPM 15, TPM 250K) | —            |
| `minicpm5-2b`               | `openai/MiniCPM5-2B` (AMD Radeon)                                          | —                                                        | —            |

**Fallback chain (when primary deployments fail):** all three Claude aliases share the same ordered cascade:

1. `gemini-3.5-flash-lite`
2. `gemini-3.1-flash-lite`
3. `minicpm5-2b`

**Full failover cascade:** `opus/sonnet/haiku → gemini-3.5-flash-lite → gemini-3.1-flash-lite → minicpm5-2b`

**Load balancing & failover:** `routing_strategy: simple-shuffle` routes to the least-utilized deployment per worker; `num_retries: 2` adds per-call retries within a model's deployment pool before escalating to the fallback model; `cooldown_time: 60` marks a failing deployment unhealthy for 60s. There is no `allowed_fails` or `retry_after` key in the config, so LiteLLM's defaults apply for those. Note: because the proxy runs `--num_workers 2`, these counters are tracked **per worker**, so effective failover coverage is roughly halved under concurrent load.

**Resilience:** `request_timeout: 180` in `litellm_settings` aborts upstream calls that hang past 180 seconds, preventing cascading stalls. No per-deployment `timeout` overrides are set in `litellm/config.yaml` (the opus 60 / sonnet 45 / haiku 30 / gemini 25–30 values were removed in commit `3756b4f`), so the 180s global cap applies to every deployment.

## Patched Image (Nemotron thinking-stream + empty-choices fix)

The proxy builds a `litellm-proxy:patched` image (`Dockerfile`) that rewrites adapter files at build time via `patches/fix_nemotron_thinking_stream.py`:

1. **transformation.py** — Reorders block-type detection so `reasoning_content` wins over `content`
2. **streaming_iterator.py** — Guards against `thinking_delta` landing in a `text` block (sync + async paths)
3. **streaming_iterator.py** — Adds empty-choices guard to prevent `IndexError: list index out of range` on empty upstream chunks
4. **streaming_iterator.py** — Peek-first-chunk: opens the initial `content_block` as `thinking`/`text`/`tool_use` to match the first chunk instead of hardcoding an empty `text` block (adapted from upstream PR #33252)

**Upstream tracking:** as of 2026-08-12 the fixes in this patch are **not** merged upstream. Related open PRs: #32664 (guard thinking/signature deltas), #33241 (keep reasoning in thinking block on transitions), #33938 (block/delta type mismatch on combined chunks), #34795 (split combined reasoning+content chunks). When any of these merge, the adapter files change structurally and this patch will print `SKIP` lines at build — re-verify coverage before relying on the image.

**Validated 2026-08-19 (browser):** re-confirmed against the pinned `litellm==1.92.0` adapter sources (raw `streaming_iterator.py` and `transformation.py`) and the live PR pages. All four patch functions apply with no `SKIP` against v1.92.0 — every byte-exact anchor (`sent_content_block_start` text-block open in both `__next__`/`__anext__`, the `# Reset state for new block` async transition, the `chunk.choices[0].finish_reason` empty-choices guard, the `reasoning_content`-before-`content` reorder) is present. PR #33252 still shows `Status: Open`, so the local patch remains required. Note: upstream #33252 targets `litellm_internal_staging` and assumes helpers (`_delta_has_content`, `_restore_tool_name_mapping`) that do not exist in 1.92.0 — this patch inlines those helpers directly.

**To rebuild after config or base-image changes:**

```bash
docker compose build --no-cache litellm && docker compose up -d
```

**To verify the fix (raw SSE capture):**

```bash
curl -sN 'http://localhost:4000/v1/messages?beta=true' \
  -H "Content-Type: application/json" \
  -H "x-api-key: $LITELLM_MASTER_KEY" \
  -H "anthropic-version: 2023-06-01" \
  -d '{
    "model": "claude-opus-5",
    "max_tokens": 512,
    "stream": true,
    "messages": [{"role": "user", "content": "Hi"}]
  }'
```

Expected: First `content_block_start` opens as `thinking` (not a phantom empty text block), `thinking_delta`s stay in the thinking block, `text_delta`s in the text block. No `thinking_delta` in text blocks. No empty-choices crashes. Clean streaming.

## Development Commands

```bash
# Start the proxy (builds patched image on first run)
docker compose up -d

# Stop the proxy
docker compose down

# Rebuild patched image after config/base-image change
docker compose build --no-cache litellm && docker compose up -d

# Restart after config changes (no rebuild needed)
docker compose down && docker compose up -d

# View logs
docker compose logs -f

# Validate config syntax before restart
python -c "import yaml; yaml.safe_load(open('litellm/config.yaml'))"
```

## Router Configuration (`litellm/config.yaml`)

Source of truth for routing and provider settings. Key behaviors:

**`litellm_settings`:** `drop_params: true`, `use_chat_completions_url_for_anthropic_messages: true`, `request_timeout: 180` (aborts hung upstream requests). No Redis.

**`router_settings`:** `routing_strategy: simple-shuffle` (least-utilized deployment per worker), `num_retries: 2` (per-call retries within a deployment pool before escalating), `cooldown_time: 60` (seconds a failed deployment stays unhealthy). Fallback chain set via `fallbacks` (identical three-model cascade for all Claude aliases). No Redis, and — contrary to earlier revisions of this file — **no `allowed_fails`, `retry_after`, or per-deployment `timeout` keys** are present.

**Per-deployment extra params:** the `claude-sonnet-5` (Agnes AI, SenseNova) and `minicpm5-2b` (AMD) deployments set `use_chat_completions_api: true` with an explicit `api_base`; the Gemini and NVIDIA NIM deployments rely on provider defaults.

**`additional_drop_params: ["tools[*].strict"]`** — Applied to all Gemini deployments. Strips `strict: null` from tool definitions before sending to backends that reject non-boolean values (fixes 400 validation errors from providers that apply strict type validation).

**`general_settings`:** `master_key` auth. No `database_url` — single-instance, no PostgreSQL. Virtual keys/budgets/spend reports are in-memory only.

## Testing

```bash
# Verify proxy is running
curl http://localhost:4000/v1/models

# Test chat completion
curl -X POST http://localhost:4000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -d '{"model": "claude-opus-5", "messages": [{"role": "user", "content": "Hello!"}], "max_tokens": 100}'

# Test tools carrying strict: null (NIM accepts it as-is; the Gemini fallback
# deployments strip it via additional_drop_params)
curl -X POST http://localhost:4000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -d '{
    "model": "claude-haiku-4-5-20251001",
    "messages": [{"role": "user", "content": "Hello"}],
    "max_tokens": 50,
    "tools": [{"type": "function", "function": {"name": "test", "strict": null, "parameters": {"type": "object"}}}]
  }'

# Check deployment IDs
curl -s http://localhost:4000/v1/model/info \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" | \
  python3 -c "import json,sys; [print(f'{m[\"model_name\"]:30s} {m[\"litellm_params\"].get(\"model\",\"?\"):40s}') for m in json.load(sys.stdin)['data']]"

# Check load balancing (different x-litellm-model-id headers across requests).
# Note: pools now use simple-shuffle. Only claude-sonnet-5 (Agnes AI + SenseNova) and the two
# Gemini pools have multiple deployments, so only those vary; claude-opus-5,
# claude-haiku-4-5-20251001 and minicpm5-2b are single-deployment and always return one model id.
# However, with --num_workers 2, counters are per-worker, so concurrent requests may not show
# perfect round-robin behavior.
for i in {1..6}; do
  curl -s -D - http://0.0.0.0:4000/v1/chat/completions \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
    -d '{"model": "claude-sonnet-5", "messages": [{"role": "user", "content": "Hi"}], "max_tokens": 10}' | \
    grep "x-litellm-model-id:"
done
```

## Maintenance

- **Update API keys**: Edit `.env` file
- **Modify routing/providers**: Update `litellm/config.yaml`, then restart
- **Rebuild patched image**: `docker compose build --no-cache litellm && docker compose up -d` (after config or base-image change)
- **Monitor logs**: `docker compose logs -f`
