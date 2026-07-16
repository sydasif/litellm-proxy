# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AI Proxy Gateway that routes **Claude Code** through **LiteLLM** to multiple AI backend providers (NVIDIA NIM, OpenCode Zen) with load balancing, rate limiting, and fallback chains. Uses Docker Compose to deploy a patched LiteLLM image that fixes Nemotron thinking-stream and empty-choices streaming bugs.

## Architecture

**Model aliasing:** Virtual model names (e.g., `claude-opus-4-8`) map to real provider models. Clients request the virtual name; LiteLLM load-balances across multiple backend deployments per virtual model.

**Backend deployments:**

| Virtual Model               | Deployment 1 (OpenCode Zen, 30 RPM) | Deployment 2 (NVIDIA NIM, 40 RPM)                                                       |
| --------------------------- | ----------------------------------- | --------------------------------------------------------------------------------------- |
| `claude-opus-4-8`           | `openai/mimo-v2.5-free`             | `nvidia/nemotron-3-ultra-550b-a55b` (key 1)                                             |
| `claude-sonnet-5`           | `openai/hy3-free` (295B MoE)        | `nvidia/nemotron-4-340b-instruct` (key 2)                                               |
| `claude-haiku-4-5-20251001` | —                                   | `nvidia/nemotron-3-nano-30b-a3b` (key 1) + `mistralai/mistral-7b-instruct-v0.3` (key 2) |

**Rate limits (enforced via `enforce_model_rate_limits`):**

| Provider     | RPM | TPM     | Scope                      |
| ------------ | --- | ------- | -------------------------- |
| NVIDIA NIM   | 40  | 500,000 | Per API key (published)    |
| OpenCode Zen | 30  | 100,000 | Per API key (conservative) |

**Fallback chain:**

```
claude-opus-4-8 → claude-sonnet-5 → claude-haiku-4-5-20251001
```

**NVIDIA NIM worker limits:** 32 concurrent requests per worker. Different models = different worker pools = independent limits. Using multiple API keys across different models doubles effective throughput.

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
    "model": "claude-opus-4-8",
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

| Setting                                           | Value                       | Description                                                                 |
| ------------------------------------------------- | --------------------------- | --------------------------------------------------------------------------- |
| `routing_strategy`                                | `simple-shuffle`            | Randomly distributes requests across deployments with RPM-aware weighting   |
| `num_retries`                                     | `2`                         | Retry each deployment 2x before fallback                                    |
| `timeout`                                         | `90`                        | Request timeout in seconds                                                  |
| `allowed_fails`                                   | `2`                         | Mark deployment unhealthy after 2 consecutive failures                      |
| `cooldown_time`                                   | `30`                        | Seconds before retrying failed deployment                                   |
| `enable_pre_call_checks`                          | `true`                      | Health checks before routing                                                |
| `optional_pre_call_checks`                        | `enforce_model_rate_limits` | Hard-enforce RPM/TPM per deployment                                         |
| `drop_params`                                     | `true`                      | Strips unsupported parameters for cross-provider compatibility              |
| `use_chat_completions_url_for_anthropic_messages` | `true`                      | Routes all providers via `/v1/chat/completions` for Anthropic compatibility |
| `fallbacks`                                       | `opus→sonnet→haiku`         | Automatic failover chain across model groups                                |

## Testing

```bash
# Verify proxy is running
curl http://localhost:4000/v1/models

# Test chat completion
curl -X POST http://localhost:4000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -d '{
    "model": "claude-opus-4-8",
    "messages": [{"role": "user", "content": "Hello!"}],
    "max_tokens": 100
  }'

# Check load balancing (should show different x-litellm-model-id headers)
for i in {1..6}; do
  curl -s -D - http://localhost:4000/v1/chat/completions \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
    -d '{"model": "claude-opus-4-8", "messages": [{"role": "user", "content": "Hi"}], "max_tokens": 10}' | \
    grep "x-litellm-model-id:"
done
```

## Maintenance

- **Update API keys**: Edit `.env` file
- **Modify routing/providers**: Update `litellm/config.yaml`, then restart
- **Rebuild patched image**: `docker compose build --no-cache litellm && docker compose up -d` (after config or base-image change)
- **Update LiteLLM base image**: Bump tag in `Dockerfile`, rebuild, re-verify the patch applies
- **Monitor logs**: `docker compose logs -f`
