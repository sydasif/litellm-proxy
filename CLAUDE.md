# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AI Proxy Gateway that routes **Claude Code** through **LiteLLM** to multiple AI backend providers (NVIDIA NIM, OpenCode Zen, SenseNova, Agnes AI, Google Gemini) with load balancing, rate limiting, and weighted failover. Runs as a single instance with in-memory state (no Postgres/Redis). Uses Docker Compose to deploy a patched LiteLLM image that fixes Nemotron thinking-stream and empty-choices streaming bugs.

## Architecture

**Model aliasing:** Virtual model names map to real provider models. Clients request the virtual name; LiteLLM load-balances across deployments.

**Backend deployments:**

| Virtual Model               | Deployment 1                                          | Deployment 2                                         | Deployment 3 | Deployment 4 |
| --------------------------- | ----------------------------------------------------- | ---------------------------------------------------- | ------------ | ------------ |
| `claude-opus-5`             | `openai/deepseek-v4-flash-free` (OpenCode Zen, RPM 30) | `openai/hy3-free` (OpenCode Zen, RPM 30)       | —            | —            |
| `claude-sonnet-5`           | `openai/sensenova-6.8-flash-lite` (RPM 30)             | `openai/agnes-2.5-flash` (RPM 30)                    | —            | —            |
| `claude-haiku-4-5-20251001` | `nvidia_nim/nvidia/nemotron-3.5-lightning-30b-a3b` (key 1, RPM 40) | `nvidia_nim/nvidia/nemotron-3.5-lightning-30b-a3b` (key 2, RPM 40) | — | — |
| `gemini-3.5`                | `gemini/gemini-3.5-flash-lite` (key 1, RPM 15, TPM 240K) | `gemini/gemini-3.5-flash-lite` (key 2, RPM 15, TPM 240K) | —      | —            |

**Fallback chain (when all primary deployments are exhausted):**

| Model                       | Fallback to                  |
| --------------------------- | ---------------------------- |
| `claude-opus-5`             | `claude-haiku-4-5-20251001`  |
| `claude-sonnet-5`           | `claude-haiku-4-5-20251001`  |
| `claude-haiku-4-5-20251001` | `gemini-3.5`                     |

**Full failover cascade:** `opus/sonnet → haiku (NVIDIA NIM) → gemini`

**Rate limits** enforced per-deployment via `enforce_model_rate_limits` — 30 RPM on OpenCode Zen (opus) and SenseNova/Agnes (sonnet) deployments, 40 RPM on NVIDIA NIM (haiku) deployments, 15 RPM / 240K TPM on Gemini deployments.

**Load balancing:** LiteLLM default (`simple-shuffle`) distributes requests across deployments per model name. `enable_weighted_failover: true` retries within a model's deployment pool before escalating to the fallback model.

## Patched Image (Nemotron thinking-stream + empty-choices fix)

The proxy builds a `litellm-proxy:patched` image (`Dockerfile`) that rewrites adapter files at build time via `patches/fix_nemotron_thinking_stream.py`:

1. **transformation.py** — Reorders block-type detection so `reasoning_content` wins over `content`
2. **streaming_iterator.py** — Guards against `thinking_delta` landing in a `text` block (sync + async paths)
3. **streaming_iterator.py** — Adds empty-choices guard to prevent `IndexError: list index out of range` on empty upstream chunks
4. **streaming_iterator.py** — Peek-first-chunk: opens the initial `content_block` as `thinking`/`text`/`tool_use` to match the first chunk instead of hardcoding an empty `text` block (adapted from upstream PR #33252)

**Upstream tracking:** as of 2026-08-12 the fixes in this patch are **not** merged upstream. Related open PRs: #32664 (guard thinking/signature deltas), #33241 (keep reasoning in thinking block on transitions), #33938 (block/delta type mismatch on combined chunks), #34795 (split combined reasoning+content chunks). When any of these merge, the adapter files change structurally and this patch will print `SKIP` lines at build — re-verify coverage before relying on the image.

**Validated 2026-08-19 (browser):** re-confirmed against the pinned `litellm==1.92.0` adapter sources (raw `streaming_iterator.py` and `transformation.py`) and the live PR pages. All four patch functions apply with no `SKIP` against v1.92.0 — every byte-exact anchor (`sent_content_block_start` text-block open in both `__next__`/`__anext__`, the `# Reset state for new block` async transition, the `chunk.choices[0].finish_reason` empty-choices guard, the `reasoning_content`-before-`content` reorder) is present. PR #33252 still shows `Status: Open`, so the local patch remains required. Note: upstream #33252 targets `litellm_internal_staging` and assumes helpers (`_delta_has_content`, `_restore_tool_name_mapping`) that do not exist in 1.92.0 — this patch inlines those, so it is the correct adaptation for the pinned base image and is already hardened against the tool-name regression upstream reviewers flagged.

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

**`litellm_settings`:** `drop_params: true`, `use_chat_completions_url_for_anthropic_messages: true`, `request_timeout: 120`. No Redis.

**`router_settings`:** `enable_weighted_failover: true` (retries within the same model's deployment pool before escalating), `optional_pre_call_checks: [enforce_model_rate_limits]` — hard-enforces per-deployment RPM/TPM (tracked in-process for single instance). Fallback chain set via `fallbacks`. No Redis.

**`additional_drop_params: ["tools[*].strict"]`** — Applied to all Gemini deployments. Strips `strict: null` from tool definitions before sending to backends that reject non-boolean values. Fixes 400 validation errors from sglang-based providers.

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

# Test haiku (SenseNova/Agnes) with tools (validates additional_drop_params fix)
curl -X POST http://localhost:4000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -d '{
    "model": "claude-haiku-4-5-20251001",
    "messages": [{"role": "user", "content": "Hello"}],
    "max_tokens": 50,
    "tools": [{"type": "function", "function": {"name": "test", "strict": null, "parameters": {"type": "object"}}}]
  }'

# Check deployment IDs and order
curl -s http://localhost:4000/v1/model/info \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" | \
  python3 -c "import json,sys; [print(f'{m[\"model_name\"]:30s} {m[\"litellm_params\"].get(\"model\",\"?\"):40s} order:{m[\"litellm_params\"].get(\"order\",\"-\")}') for m in json.load(sys.stdin)['data']]"

# Check load balancing (different x-litellm-model-id headers across requests)
for i in {1..6}; do
  curl -s -D - http://localhost:4000/v1/chat/completions \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
    -d '{"model": "claude-opus-5", "messages": [{"role": "user", "content": "Hi"}], "max_tokens": 10}' | \
    grep "x-litellm-model-id:"
done
```

## Maintenance

- **Update API keys**: Edit `.env` file
- **Modify routing/providers**: Update `litellm/config.yaml`, then restart
- **Rebuild patched image**: `docker compose build --no-cache litellm && docker compose up -d` (after config or base-image change)
- **Monitor logs**: `docker compose logs -f`
