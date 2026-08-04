# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AI Proxy Gateway that routes **Claude Code** through **LiteLLM** to multiple AI backend providers (NVIDIA NIM, OpenCode Zen, Google Gemini) with load balancing, rate limiting, and order-based failover. Uses Docker Compose to deploy a patched LiteLLM image that fixes Nemotron thinking-stream and empty-choices streaming bugs.

## Architecture

**Model aliasing:** Virtual model names map to real provider models. Clients request the virtual name; LiteLLM load-balances across deployments.

**Backend deployments:**

| Virtual Model               | Deployment 1                                                     | Deployment 2                                                      | Deployment 3 | Deployment 4 |
| --------------------------- | ---------------------------------------------------------------- | ----------------------------------------------------------------- | ------------ | ------------ |
| `claude-opus-5`             | `nvidia_nim/…nemotron-3…` (key 1, RPM 40)                        | `nvidia_nim/…nemotron-3…` (key 2, RPM 40)                         | —            | —            |
| `claude-sonnet-5`           | `openai/deepseek-v4-flash-free` (OpenCode Zen, RPM 40, order 1)  | `openai/mimo-v2.5-free` (OpenCode Zen, RPM 40, order 2)           | —            | —            |
| `claude-haiku-4-5-20251001` | `gemini-3.5-flash-lite` (key 1, RPM 15, TPM 240K)                | `gemini-3.5-flash-lite` (key 2, RPM 15, TPM 240K)                 | —            | —            |
| `gemini`                    | `gemini-3.1-flash-lite` (key 1, RPM 15, TPM 240K)                | `gemini-3.1-flash-lite` (key 2, RPM 15, TPM 240K)                 | —            | —            |

**Fallback chain (when all primary deployments are exhausted):**

| Model                       | Fallback to |
| --------------------------- | ----------- |
| `claude-opus-5`             | (none)      |
| `claude-sonnet-5`           | `gemini`    |
| `claude-haiku-4-5-20251001` | `gemini`    |

**Haiku failover:** `claude-haiku-4-5-20251001` maps to two `gemini-3.5-flash-lite` deployments that LiteLLM load-balances via `simple-shuffle`. When both are exhausted or fail, `router_settings.fallbacks` routes to the separate `gemini` model (`gemini-3.1-flash-lite`, 2 deployments).

**Haiku full failover cascade:** `3.5 KEY_1 → 3.5 KEY_2 → (fallback) 3.1 KEY_1 → 3.1 KEY_2`

**Sonnet order-based failover:** Within the sonnet slot, deployments are prioritized by `order`:

- `order: 1` → `deepseek-v4-flash-free` (primary)
- `order: 2` → `mimo-v2.5-free` (secondary)
- Router exhausts all order-1 deployments before escalating to order-2
- When both are exhausted, `router_settings.fallbacks` routes to the `gemini` model
- Sonnet full cascade: `deepseek-v4-flash-free → mimo-v2.5-free → gemini`

**Rate limits** enforced per-deployment via `enforce_model_rate_limits` — set at 40 RPM on NVIDIA NIM and OpenCode Zen deployments, 15 RPM / 240K TPM on Gemini deployments.

**Load balancing:** LiteLLM default (`simple-shuffle`) distributes requests across deployments per model name.

## Patched Image (Nemotron thinking-stream + empty-choices fix)

The proxy builds a `litellm-proxy:patched` image (`Dockerfile`) that rewrites adapter files at build time via `patches/fix_nemotron_thinking_stream.py`:

1. **transformation.py** — Reorders block-type detection so `reasoning_content` wins over `content`
2. **streaming_iterator.py** — Guards against `thinking_delta` landing in a `text` block (sync + async paths)
3. **streaming_iterator.py** — Adds empty-choices guard to prevent `IndexError: list index out of range` on empty upstream chunks

**To rebuild after config or base-image changes:**

```bash
docker compose build --no-cache litellm && docker compose up -d
```

**To verify the fix (raw SSE capture):**

```bash
curl -sN 'http://localhost:4000/v1/messages?beta=true' \
  -H "x-api-key: $LITELLM_MASTER_KEY" \
  -H "anthropic-version: 2023-06-01" \
  -d '{
    "model": "claude-opus-5",
    "max_tokens": 512,
    "stream": true,
    "messages": [{"role": "user", "content": "Hi"}]
  }'
```

Expected: No `thinking_delta` in text blocks. No empty-choices crashes. Clean streaming.

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

**`litellm_settings`:** `drop_params: true`, `use_chat_completions_url_for_anthropic_messages: true`, `reasoning_auto_summary: true`, `request_timeout: 120`, Redis-backed `cache: true` (600s TTL, 100 max connections)

**`router_settings`:** `enable_weighted_failover: true` (retries within same order tier before escalating), `optional_pre_call_checks: [enforce_model_rate_limits]` — hard-enforces per-deployment RPM/TPM. Fallback chain set via `fallbacks`. Redis connection for distributed rate limiting.

**`additional_drop_params: ["tools[*].strict"]`** — Applied to all Gemini deployments. Strips `strict: null` from tool definitions before sending to backends that reject non-boolean values. Fixes 400 validation errors from sglang-based providers.

**`general_settings`:** `master_key` auth, `database_url` for PostgreSQL usage tracking.

## Testing

```bash
# Verify proxy is running
curl http://localhost:4000/v1/models

# Test chat completion
curl -X POST http://localhost:4000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -d '{"model": "claude-opus-5", "messages": [{"role": "user", "content": "Hello!"}], "max_tokens": 100}'

# Test haiku (Gemini) with tools (validates additional_drop_params fix)
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
- **Update LiteLLM base image**: Bump tag in `Dockerfile`, rebuild, re-verify the patch applies
- **Monitor logs**: `docker compose logs -f`
