# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AI Proxy Gateway that routes **Claude Code** through **LiteLLM** to multiple AI backend providers (NVIDIA NIM, OpenCode Zen, Agnes AI) with load balancing and parameter normalization. Uses Docker Compose to deploy a patched LiteLLM image that fixes the Nemotron thinking-stream bug.

## Architecture

**Model aliasing:** Virtual model names (e.g., `claude-opus-4-8`) map to real provider models. Clients request the virtual name; LiteLLM routes to the backend.

**Backend deployments:**

| Virtual Model               | Backend Model                       | Provider     |
| --------------------------- | ----------------------------------- | ------------ |
| `claude-opus-4-8`           | `nvidia/nemotron-3-ultra-550b-a55b` | NVIDIA NIM   |
| `claude-sonnet-5`           | `mimo-v2.5-free`                    | OpenCode Zen |
| `claude-sonnet-5`           | `hy3-free`                          | OpenCode Zen |
| `claude-haiku-4-5-20251001` | `gpt-oss-120b`                      | NVIDIA NIM   |
| `agnes-2.0-flash`           | `agnes-2.0-flash`                   | Agnes AI     |

**Special settings:** `nemotron-3-ultra-550b-a55b` uses `thinking: true` to enable reasoning output (chain-of-thought).

## Patched Image (Nemotron thinking-stream fix)

The official LiteLLM image emits a malformed Anthropic SSE stream when Nemotron 3 Ultra is served with streaming + thinking enabled: a chunk carrying both `reasoning_content` and text opens the block as `text` but receives a `thinking_delta`, which Claude Code rejects with `Content block is not a thinking block`.

The proxy builds a `litellm-proxy:patched` image (`Dockerfile`) that rewrites the two adapter files at build time via `patches/fix_nemotron_thinking_stream.py`:

- `transformation.py` — reorders block-type detection so `reasoning_content` wins over `content`
- `streaming_iterator.py` — guards against `thinking_delta` landing in a `text` block (sync + async paths)

**To rebuild after config or base-image changes:**

```bash
docker compose build --no-cache litellm && docker compose up -d
```

**To verify the fix (raw SSE capture, no Claude Code in path):**

```bash
curl -sN 'http://localhost:4000/v1/messages?beta=true' \
  -H "x-api-key: $LITELLM_MASTER_KEY" \
  -H "anthropic-version: 2023-06-01" \
  -d '{
    "model": "claude-opus-4-8",
    "max_tokens": 512,
    "stream": true,
    "thinking": {"type": "enabled", "budget_tokens": 1024},
    "messages": [{"role": "user", "content": "Hi"}]
  }'
```

Expected: thinking deltas stay in the thinking block (`index: 1`), text deltas in the text block (`index: 2`). No `thinking_delta` should ever appear in a block declared as `text`.

**Upgrade note:** Bumping the base image tag in `Dockerfile` requires re-verifying the patch still applies — the script prints `SKIP` if the target source strings changed.

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

**Explicitly configured settings:**

| Setting                                           | Value            | Description                                                                 |
| ------------------------------------------------- | ---------------- | --------------------------------------------------------------------------- |
| `routing_strategy`                                | `simple-shuffle` | Randomly distributes requests across deployments                            |
| `drop_params`                                     | `true`           | Strips unsupported parameters for cross-provider compatibility              |
| `use_chat_completions_url_for_anthropic_messages` | `true`           | Routes all providers via `/v1/chat/completions` for Anthropic compatibility |

**LiteLLM built-in defaults (not explicitly set in config.yaml):**

| Setting                  | Default | Description                                              |
| ------------------------ | ------- | -------------------------------------------------------- |
| `allowed_fails`          | `2`     | Deployment marked unhealthy after 2 consecutive failures |
| `cooldown_time`          | `30`    | Seconds before retrying failed deployment                |
| `enable_pre_call_checks` | `true`  | Health checks before routing                             |
| `num_retries`            | `0`     | No retries by default (was previously set to 3)          |
| `fallbacks`              | `[]`    | No default fallback (was previously `claude-sonnet-5`)   |

## Testing

```bash
# Verify proxy is running
curl http://localhost:4000/v1/models

# Test chat completion
curl -X POST http://localhost:4000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-opus-4-8",
    "messages": [{"role": "user", "content": "Hello!"}],
    "max_tokens": 100
  }'
```

## Maintenance

- **Update API keys**: Edit `.env` file
- **Modify routing/providers**: Update `litellm/config.yaml`, then restart
- **Rebuild patched image**: `docker compose build --no-cache litellm && docker compose up -d` (after config or base-image change)
- **Update LiteLLM base image**: Bump tag in `Dockerfile`, rebuild, re-verify the patch applies
- **Monitor logs**: `docker compose logs -f`
