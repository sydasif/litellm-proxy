# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AI Proxy Gateway that routes Claude Code through LiteLLM to multiple AI backend providers (NVIDIA NIM, OpenCode Zen, Agnes AI) with load balancing and parameter normalization. Uses Docker Compose to deploy the official LiteLLM image.

## Project Structure

```bash
litellm-proxy/
├── docker-compose.yml          # Docker Compose configuration
├── .env                        # API keys (gitignored)
├── .env.example                # Template for environment variables
├── .gitignore
├── README.md                   # Project overview and setup
└── litellm/
    └── config.yaml             # LiteLLM provider configuration
```

## Development Commands

### Environment Setup

```bash
# Copy environment template and configure API keys
cp .env.example .env
# Edit .env with your API keys:
#   NVIDIA_API_KEY_1, NVIDIA_API_KEY_2, OPENCODE_API_KEY, AGNES_API_KEY

# Start the proxy service
docker compose up -d

# Stop the service
docker compose down

# View logs
docker compose logs -f
```

### Router & Load Balancing

- **Routing Strategy**: `latency-based-routing` – selects the deployment with the lowest recent response time.
- **Retries**: `3` attempts per request.
- **Per‑Model Timeouts** (seconds):
  - `nemotron-3-ultra-550b-a55b`: 180
  - `qwen3.5-397b-a17b`: 120
  - `mimo-v2.5-free`: 45
  - `hy3-free`: 45
  - `nemotron-3-nano-30b-a3b`: 30
  - `gpt-oss-120b`: 30
- **Fallback Mappings**:
  - `claude-opus-4-8` → `claude-sonnet-5`
  - `claude-haiku-4-5-20251001` → `claude-sonnet-5`
- **Allowed Fails**: `2` – a deployment is marked unhealthy after two consecutive failures.
- **Cooldown Time**: `30` s before a failed deployment can be retried.
- **Pre‑call Checks**: enabled – health checks run before routing each request.
- **Parameter Handling**: `drop_params: true` ensures cross‑provider compatibility.
- **Anthropic Routing**: `use_chat_completions_url_for_anthropic_messages: true` routes all providers via `/v1/chat/completions`.

After any change to these settings, restart the proxy (`docker compose restart`) so the new router configuration takes effect.

### Testing

Test the running proxy (localhost:4000):

```bash
curl -X POST http://localhost:4000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-opus-4-8",
    "messages": [{"role": "user", "content": "Hello!"}],
    "max_tokens": 100
  }'
```

## Maintenance

- Update API keys in `.env` as needed
- Modify `litellm/config.yaml` for provider/routing changes
- Update LiteLLM image: `docker compose pull && docker compose up -d`
- Monitor logs: `docker compose logs -f`

## Security

- API keys stored exclusively in `.env` (gitignored)
- No secrets in codebase or configuration files
