# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AI Proxy Gateway that routes **Claude Code** through **LiteLLM** to multiple AI backend providers (NVIDIA NIM, Agnes AI, SenseNova, Google Gemini) with load balancing and cascading fallbacks. Runs as a multi-worker instance (`--num_workers 2`) with **per-worker** in-memory state — the router's cooldown/usage counters are not shared across workers (no Postgres/Redis). Binds on `0.0.0.0:4000` (host network must isolate this port). Uses Docker Compose to deploy a pinned LiteLLM image (`1.105.1`) whose Anthropic streaming adapter is verified at build time against the Nemotron thinking-stream defects that previously required a local source patch.

## Architecture

**Model aliasing:** Virtual model names map to real provider models. Clients request the virtual name; LiteLLM routes across deployments per the pool's strategy below.

**Backend deployments:**

| Virtual Model               | Deployment 1                                                               | Deployment 2                                                    |
| --------------------------- | -------------------------------------------------------------------------- | --------------------------------------------------------------- |
| `claude-opus-5`             | `nvidia_nim/nvidia/nemotron-3-ultra-550b-a55b` (key 1, RPM 40)             | `nvidia_nim/nvidia/nemotron-3-ultra-550b-a55b` (key 2, RPM 40)  |
| `claude-sonnet-5`           | `openai/agnes-2.5-flash` (Agnes AI, `AGNES_API_KEY`, RPM 20)               | `openai/sensenova-6.8-flash-lite` (SenseNova, `SENSENOVA_API_KEY`, RPM 20) |
| `claude-haiku-4-5-20251001` | `nvidia_nim/nvidia/nemotron-3.5-super-vl-120b-a12b` (key 1, RPM 40)        | `nvidia_nim/nvidia/nemotron-3.5-super-vl-120b-a12b` (key 2, RPM 40) |
| `gemini-3.5-flash-lite`     | `gemini/gemini-3.5-flash-lite` (key 1, RPM 15, TPM 250K)                   | `gemini/gemini-3.5-flash-lite` (key 2, RPM 15, TPM 250K)        |
| `gemini-3.1-flash-lite`     | `gemini/gemini-3.1-flash-lite` (key 1, RPM 15, TPM 250K)                   | `gemini/gemini-3.1-flash-lite` (key 2, RPM 15, TPM 250K)        |

**Fallback chain (when primary deployments fail):** all three Claude aliases share the same ordered cascade:

1. `gemini-3.5-flash-lite`
2. `gemini-3.1-flash-lite`

**Full failover cascade:** `opus/sonnet/haiku → gemini-3.5-flash-lite → gemini-3.1-flash-lite`

**Load balancing & failover:** `routing_strategy: simple-shuffle` routes to the least-utilized deployment per worker; `num_retries: 2` adds per-call retries within a model's deployment pool before escalating to the fallback model; `cooldown_time: 60` marks a failing deployment unhealthy for 60s. There is no `allowed_fails` or `retry_after` key in the config, so LiteLLM's defaults apply for those. All five pools hold two deployments, so `simple-shuffle` spreads load across both endpoints of each (verified against a live `litellm.Router`: both keys of `claude-opus-5` and of `claude-haiku-4-5-20251001`, and both providers of `claude-sonnet-5`, are returned across successive picks).

> **`rpm` vs `order` placement (both used to sit at the top level of a deployment):** litellm promotes a top-level `rpm` (and `tpm`) into `litellm_params` at router init, so `rpm: 40` at deployment level works. It does **not** do that for `order`: `litellm.utils._get_deployment_order` reads `litellm_params.order` then `model_info.order` only, so a top-level `order` was silently inert and has been removed from the config. To hold a key as a hot standby instead of a load-balanced peer, put `order` **inside** `litellm_params` of that deployment.

> Note: because the proxy runs `--num_workers 2`, the cooldown/usage counters are tracked **per worker**, so under concurrent load the effective failover coverage is roughly halved.

**Resilience:** `request_timeout: 180` in `litellm_settings` aborts upstream calls that hang past 180 seconds, preventing cascading stalls. No per-deployment `timeout` overrides are set in `litellm/config.yaml`, so the 180s global cap applies to every deployment.

## Image pin & streaming verification (Nemotron thinking-stream)

The image is pinned to `docker.litellm.ai/berriai/litellm:1.105.1`. **The four defects below are fixed upstream as of this pin, so no local patch is applied.** The build still gates on the behavior: `Dockerfile` runs `patches/verify_upstream_anthropic_streaming.py`, which drives the installed adapter with the exact Nemotron chunk shape that used to break Claude Code and exits non-zero (failing the build) if any invariant regressed.

The four defects that litellm 1.92.0 had, and where they were fixed:

| # | Defect (1.92.0) | Fixed upstream by |
| - | --------------- | ----------------- |
| 1 | First content block hardcoded as `{"type": "text"}` before any upstream chunk was read, so a stream leading with reasoning opened a phantom empty text block | #33252 — block opened from the classified type |
| 2 | A chunk carrying both `reasoning_content` and `content` opened a `text` block (content checked first) while the emitted delta was a `thinking_delta` → Claude Code's `"Content block is not a thinking block"` | #34795 — combined chunks split per payload kind (thinking → text → tool_use) |
| 3 | The text in such a chunk was silently dropped | #34795 (same split) |
| 4 | `_should_start_new_content_block` indexed `chunk.choices[0]` unguarded → `IndexError` on an empty upstream chunk | choiceless chunks now skipped via `_handle_choiceless_chunk` |

Until the 1.105.1 bump, defects 1–4 were rewritten into the installed adapter sources at build time by `patches/fix_nemotron_thinking_stream.py`. That script is **kept** (it still applies to the `llms/anthropic/experimental_pass_through/adapters` layout used by ≤1.92.x) but is **no longer run**, and its anchor strings are version-specific.

**Upstream layout note:** the adapter moved from `litellm/llms/anthropic/experimental_pass_through/adapters/` to `litellm/llms/anthropic/pass_through/adapters/` in the 1.10x line. Both the verification script and any future re-target of the patch have to account for that.

**On a base-image bump:**

```bash
# 1. Bump the FROM tag in Dockerfile, then rebuild (the verify step fails the build on drift)
docker compose build --no-cache litellm && docker compose up -d
# 2. Check the verify step passed and no SKIP/FAIL lines appeared
# 3. Re-verify the live stream with the raw SSE capture below
```

A failing verify step means the pin reintroduced one of the four defects: re-target `patches/fix_nemotron_thinking_stream.py` to the new adapter sources, apply it in the Dockerfile before the verify step, and re-run the capture.

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
# Start the proxy (builds the image on first run)
docker compose up -d

# Stop the proxy
docker compose down

# Rebuild the image after config/base-image change
docker compose build --no-cache litellm && docker compose up -d

# Restart after config changes (no rebuild needed)
docker compose down && docker compose up -d

# View logs
docker compose logs -f

# Run the streaming verification gate locally (needs litellm importable)
python3 patches/verify_upstream_anthropic_streaming.py

# Validate config syntax before restart
python -c "import yaml; yaml.safe_load(open('litellm/config.yaml'))"
```

## Router Configuration (`litellm/config.yaml`)

Source of truth for routing and provider settings. Key behaviors:

**`litellm_settings`:** `drop_params: true`, `use_chat_completions_url_for_anthropic_messages: true`, `request_timeout: 180` (aborts hung upstream requests). No Redis.

**`router_settings`:** `routing_strategy: simple-shuffle` (least-utilized deployment per worker), `num_retries: 2` (per-call retries within a deployment pool before escalating), `cooldown_time: 60` (seconds a failed deployment stays unhealthy). Fallback chain set via `fallbacks` (identical two-model cascade for all Claude aliases). No Redis, and no `allowed_fails`, `retry_after`, or per-deployment `timeout` keys.

**`rpm` placement:** top-level on each deployment (the router promotes it into `litellm_params`). **No `order` keys** — see the note in Architecture; a top-level `order` is ignored by litellm.

**Per-deployment extra params:** the `claude-sonnet-5` deployments (Agnes AI, SenseNova) set `use_chat_completions_api: true` with an explicit `api_base` and their own keys (`AGNES_API_KEY`, `SENSENOVA_API_KEY`); the Gemini and NVIDIA NIM deployments rely on provider defaults.

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
# Note: pools now use simple-shuffle. `claude-opus-5`, `claude-sonnet-5`,
# `claude-haiku-4-5-20251001`, `gemini-3.5-flash-lite` and
# `gemini-3.1-flash-lite` each have two deployments, so all five should vary.
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
- **Rebuild image**: `docker compose build --no-cache litellm && docker compose up -d` (after config or base-image change)
- **Bump the LiteLLM pin**: see "Image pin & streaming verification" — the verify step gates the build
- **Monitor logs**: `docker compose logs -f`
