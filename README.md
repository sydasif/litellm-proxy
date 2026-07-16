# AI Proxy Gateway

<p align="center">
  <img src="logo.png" alt="LiteLLM Proxy Logo" width="200" height="200">
</p>

<p align="center">
  <a href="https://hub.docker.com/r/litellm/litellm"><img src="https://img.shields.io/badge/LiteLLM-blueviolet?style=for-the-badge" alt="LiteLLM"></a>
  <a href="https://claude.com/product/claude-code"><img src="https://img.shields.io/badge/Claude%20Code-D97757?style=for-the-badge&logo=anthropic&logoColor=white" alt="Claude Code"></a>
  <a href="https://build.nvidia.com/"><img src="https://img.shields.io/badge/NVIDIA%20NIM-76B900?style=for-the-badge&logo=nvidia&logoColor=white" alt="NVIDIA NIM"></a>
  <a href="https://opencode.ai"><img src="https://img.shields.io/badge/OpenCode%20Zen-6C47FF?style=for-the-badge" alt="OpenCode Zen"></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/Python-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python"></a>
  <a href="https://www.docker.com/"><img src="https://img.shields.io/badge/docker-%230db7ed.svg?style=for-the-badge&logo=docker&logoColor=white" alt="Docker"></a>
  <a href="https://docs.docker.com/compose/"><img src="https://img.shields.io/badge/docker%20compose-2496ED?style=for-the-badge&logo=docker&logoColor=white" alt="Docker Compose"></a>
</p>

A proxy gateway that routes **Claude Code** through **LiteLLM** to multiple AI backend providers (NVIDIA NIM, OpenCode Zen, Agnes AI) with load balancing and rate limiting.

## Features

- **Multi-provider**: Access NVIDIA NIM and OpenCode Zen through a single endpoint
- **Load balancing**: `simple-shuffle` distributes requests across multiple model deployments
- **Rate limiting**: RPM/TPM enforcement via `enforce_model_rate_limits`
- **Parameter normalization**: Drops unsupported parameters (`drop_params: true`) for cross-provider compatibility
- **Anthropic compatibility**: Routes all providers via `/v1/chat/completions` for Claude Code integration
- **Dockerized**: Builds a patched LiteLLM image (`litellm-proxy:patched`) with Nemotron streaming fixes
- **Redis caching**: Caches repeated prompts for cost savings and latency reduction
- **PostgreSQL backend**: Persistent storage for virtual keys, budgets, and spend logs
- **Virtual keys**: Revocable API keys with budgets, rate limits, and model allowlists
- **Reasoning auto-summary**: Auto-summarizes extended reasoning streams (`reasoning_auto_summary: true`)
- **Health checks**: Liveness (`/health/liveliness`) and readiness (`/health/readiness`) endpoints

## Architecture

### Model Routing

Each claude model maps to **multiple backend deployments** across different providers. LiteLLM's `simple-shuffle` router distributes requests, and `enforce_model_rate_limits` blocks requests before hitting provider limits.

```bash
claude-opus-4-8   → 2 deployments: NVIDIA NIM nemotron-3-ultra (key 1, 40 RPM) + openai nemotron-3-ultra (key 2, 30 RPM)
claude-sonnet-5   → 2 deployments: OpenCode Zen hy3-free (40 RPM) + OpenCode Zen mimo-v2.5-free (30 RPM)
claude-haiku-4-5  → 2 deployments: NVIDIA NIM gpt-oss-120b (key 1, 40 RPM) + openai gpt-oss-120b (key 2, 30 RPM)
agnes-2.0-flash   → 1 deployment: Agnes AI agnes-2.0-flash (30 RPM)
```

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/)

## Quick Start

```bash
# 1. Clone and configure
git clone <repository-url>
cd litellm-proxy
cp .env.example .env

# 2. Generate required keys and populate .env
cat > .env <<EOF
LITELLM_MASTER_KEY="sk-$(openssl rand -hex 24)"
LITELLM_SALT_KEY="$(openssl rand -base64 32)"
POSTGRES_PASSWORD="$(openssl rand -base64 16)"
REDIS_PASSWORD="$(openssl rand -base64 16)"
UI_USERNAME=admin
UI_PASSWORD=changeme123
EOF

# 3. Add YOUR provider API keys to .env (edit the file)
# Required: NVIDIA_API_KEY_1, NVIDIA_API_KEY_2, OPENCODE_API_KEY

# 5. Start the proxy stack
docker compose up -d

# 6. Verify
curl http://localhost:4000/health/liveliness   # "I'm alive!"
curl http://localhost:4000/health/readiness   # {"status":"healthy","db":"connected"}

# 7. Create a virtual key (use for apps, NOT master key)
VKEY=$(curl -s -X POST http://localhost:4000/key/generate \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"models": ["claude-opus-4-8", "claude-sonnet-5", "claude-haiku-4-5-20251001"], "max_budget": 50, "budget_duration": "30d", "rpm_limit": 30}' | jq -r .key)

# 8. Test with virtual key
curl -H "Authorization: Bearer $VKEY" http://localhost:4000/v1/models
curl -X POST http://localhost:4000/v1/chat/completions \
  -H "Authorization: Bearer $VKEY" \
  -H "Content-Type: application/json" \
  -d '{"model": "claude-opus-4-8", "messages": [{"role": "user", "content": "Hello!"}], "max_tokens": 50}'

# 9. Open Admin UI
# http://localhost:4000/ui  (user: admin, pass: changeme123)
```

### Configure Claude Code

```bash
export ANTHROPIC_AUTH_TOKEN=$VKEY
export ANTHROPIC_BASE_URL=http://localhost:4000
```

Add to your shell profile (`~/.bashrc`, `~/.zshrc`) or in claude code `settings.json` and restart your terminal.

### Test the Proxy

```bash
# List available models (requires auth)
curl -H "Authorization: Bearer <YOUR_KEY>" http://localhost:4000/v1/models

# Chat completion
curl -X POST http://localhost:4000/v1/chat/completions \
  -H "Authorization: Bearer <YOUR_KEY>" \
  -H "Content-Type: application/json" \
  -d '{"model": "claude-opus-4-8", "messages": [{"role": "user", "content": "Hello!"}], "max_tokens": 100}'
```

### Virtual Key Management

```bash
# Generate a virtual key with budget and rate limits
curl -X POST http://localhost:4000/key/generate \
  -H "Authorization: Bearer <MASTER_KEY>" \
  -H "Content-Type: application/json" \
  -d '{
    "models": ["claude-opus-4-8", "claude-sonnet-5", "claude-haiku-4-5-20251001"],
    "max_budget": 50,
    "budget_duration": "30d",
    "rpm_limit": 30,
    "metadata": {"team": "engineering", "owner": "user@example.com"}
  }'

# Check key info
curl -H "Authorization: Bearer <MASTER_KEY>" "http://localhost:4000/key/info?key=<VIRTUAL_KEY>"

# List all keys
curl -H "Authorization: Bearer <MASTER_KEY>" http://localhost:4000/key/list

# Revoke a key
curl -X POST http://localhost:4000/key/delete \
  -H "Authorization: Bearer <MASTER_KEY>" \
  -H "Content-Type: application/json" \
  -d '{"key": "<VIRTUAL_KEY>"}'

# Spend report
curl -H "Authorization: Bearer <MASTER_KEY>" "http://localhost:4000/global/spend/report"
```

### Admin UI

Open `http://localhost:4000/ui` in your browser (basic auth: `admin` / `changeme123` by default).

Features:

- **Dashboard**: Overview of spend, requests, latency
- **Virtual Keys**: Create, revoke, view usage per key
- **Teams**: Organize keys by team with shared budgets
- **Models**: View registered models and their status
- **Logs**: Search request logs with filters
- **Spend Reports**: Per-team, per-model cost breakdown

### Available Models

| Alias                       | Backend                                                                |
| --------------------------- | ---------------------------------------------------------------------- |
| `claude-opus-4-8`           | NVIDIA Nemotron 3 Ultra 550B (key 1) / NVIDIA Nemotron 3 Ultra (key 2) |
| `claude-sonnet-5`           | OpenCode Zen hy3-free / OpenCode Zen mimo-v2.5-free                    |
| `claude-haiku-4-5-20251001` | NVIDIA NIM gpt-oss-120b (key 1) / NVIDIA gpt-oss-120b (key 2)          |
| `agnes-2.0-flash`           | Agnes 2.0 Flash                                                        |

## Project Structure

```bash
litellm-proxy/
├── docker-compose.yml          # Docker Compose (LiteLLM + Postgres + Redis)
├── Dockerfile                  # Builds litellm-proxy:patched from official image
├── patches/
│   └── fix_nemotron_thinking_stream.py  # Patches LiteLLM SSE streaming bug
├── .env                        # API keys (gitignored)
├── .env.example                # Template for environment variables
├── .gitignore
├── README.md                   # This file
├── CLAUDE.md                   # Detailed configuration reference
└── litellm/
    └── config.yaml             # LiteLLM provider configuration
```

## Services

| Service    | Port | Description                       |
| ---------- | ---- | --------------------------------- |
| LiteLLM    | 4000 | Main proxy API, Admin UI          |
| PostgreSQL | 5432 | Virtual keys, spend logs, budgets |
| Redis      | 6379 | Response caching, rate limiting   |

## Maintenance

| Task                         | Command                                                           |
| ---------------------------- | ----------------------------------------------------------------- |
| Update API keys              | Edit `.env` file, then restart                                    |
| Modify routing/providers     | Edit `litellm/config.yaml`, then restart                          |
| Restart after config changes | `docker compose down && docker compose up -d`                     |
| Rebuild patched image        | `docker compose build --no-cache litellm && docker compose up -d` |
| Update LiteLLM base image    | Bump tag in `Dockerfile`, rebuild, re-verify patch applies        |
| View logs                    | `docker compose logs -f`                                          |
| Check container status       | `docker compose ps`                                               |
| Admin UI                     | `http://localhost:4000/ui`                                        |
| Generate virtual key         | `curl -X POST ... /key/generate`                                  |
| Health check (liveness)      | `curl http://localhost:4000/health/liveliness`                    |
| Health check (readiness)     | `curl http://localhost:4000/health/readiness`                     |

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add some amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

Please ensure any changes maintain the security principle of keeping API keys in environment variables only.

## Security

- API keys stored exclusively in environment variables (`.env` file)
- `.env` file is gitignored to prevent accidental commitment of secrets
- No secrets should ever be added to the codebase or configuration files
- Regularly rotate your API keys following provider best practices

## License

MIT License — Copyright (c) 2026 Syed Asif

See [LICENSE](LICENSE) for full terms.

This project is a **LiteLLM proxy configuration** that routes requests to external AI APIs. Users must provide their own API keys. No model weights, binaries, or proprietary code are distributed with this repository.

LiteLLM (the base image) is licensed under MIT: [https://github.com/BerriAI/litellm](https://github.com/BerriAI/litellm)
