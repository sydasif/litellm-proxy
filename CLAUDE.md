# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AI Proxy Gateway that routes **Claude Code** through **LiteLLM** to multiple AI backend providers (NVIDIA NIM, OpenCode Zen, Agnes AI) with load balancing and parameter normalization. Uses Docker Compose to deploy the official LiteLLM image.

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

**Special settings:** `nemotron-3-ultra-550b-a55b` uses `thinking: false` to disable reasoning output.

## Development Commands

```bash
# Start the proxy
docker compose up -d

# Stop the proxy
docker compose down

# Restart after config changes
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
- **Update LiteLLM**: `docker compose pull && docker compose up -d`
- **Monitor logs**: `docker compose logs -f`
