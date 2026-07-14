# AI Proxy Gateway

<p align="center">
  <img src="logo.png" alt="LiteLLM Proxy Logo" width="200" height="200">
</p>

<p align="center">
  <a href="https://hub.docker.com/r/litellm/litellm"><img src="https://img.shields.io/badge/LiteLLM-blueviolet?style=for-the-badge" alt="LiteLLM"></a>
  <a href="https://claude.com/product/claude-code"><img src="https://img.shields.io/badge/Claude%20Code-D97757?style=for-the-badge&logo=anthropic&logoColor=white" alt="Claude Code"></a>
  <a href="https://build.nvidia.com/"><img src="https://img.shields.io/badge/NVIDIA%20NIM-76B900?style=for-the-badge&logo=nvidia&logoColor=white" alt="NVIDIA NIM"></a>
  <a href="https://opencode.ai"><img src="https://img.shields.io/badge/OpenCode%20Zen-6C47FF?style=for-the-badge" alt="OpenCode Zen"></a>
  <a href="https://agnesai.com"><img src="https://img.shields.io/badge/Agnes%20AI-FF6B6B?style=for-the-badge" alt="Agnes AI"></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/Python-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python"></a>
  <a href="https://www.docker.com/"><img src="https://img.shields.io/badge/docker-%230db7ed.svg?style=for-the-badge&logo=docker&logoColor=white" alt="Docker"></a>
  <a href="https://docs.docker.com/compose/"><img src="https://img.shields.io/badge/docker%20compose-2496ED?style=for-the-badge&logo=docker&logoColor=white" alt="Docker Compose"></a>
</p>

A proxy gateway that routes **Claude Code** through **LiteLLM** (Python) to multiple AI backend providers with load balancing and parameter normalization.

## Features

- **Multi-provider routing**: Access NVIDIA NIM, OpenCode Zen, and Agnes AI through a single endpoint
- **Load balancing**: Distributes requests across multiple NVIDIA API keys using `simple-shuffle` strategy
- **Parameter normalization**: Drops unsupported parameters (`drop_params: true`) for cross-provider compatibility
- **Anthropic compatibility**: Routes all providers via `/v1/chat/completions` endpoint for seamless Claude Code integration
- **Dockerized**: Uses official Docker Hub LiteLLM image for consistent deployment
- **Secure**: API keys managed exclusively via environment variables
- **Admin UI**: Web dashboard at `http://localhost:4000/ui` for monitoring, virtual keys, and spend tracking
- **Redis caching**: Caches repeated prompts for cost savings and latency reduction
- **PostgreSQL backend**: Persistent storage for virtual keys, budgets, and spend logs
- **Virtual keys**: Revocable API keys with budgets, rate limits, and model allowlists
- **Automatic fallbacks**: Routes to backup models on failure (sonnet → haiku, opus → sonnet)
- **Health checks**: Liveness (`/health/liveliness`) and readiness (`/health/readiness`) endpoints

## Prerequisites

- [Docker Desktop](https://docs.docker.com/get-docker/) or Docker Engine
- [Docker Compose](https://docs.docker.com/compose/install/)
- API keys for the backends you plan to use (see `.env.example`)

## Quick Start

```bash
# 1. Clone and configure
git clone <repository-url>
cd litellm-proxy
cp .env.example .env
# Edit .env with your API keys (required: NVIDIA_API_KEY_1, NVIDIA_API_KEY_2, OPENCODE_API_KEY, AGNES_API_KEY)

# 2. Generate required keys
# Master key (for admin API access):
openssl rand -hex 24 | sed 's/^/sk-/'
# Salt key (for DB encryption - SAVE THIS SECURELY, CANNOT BE ROTATED):
openssl rand -base64 32

# 3. Start the proxy stack
docker compose up -d

# 4. Verify
curl http://localhost:4000/health/liveliness
# "I'm alive!"

curl http://localhost:4000/health/readiness
# {"status":"healthy","db":"connected"}
```

## Configuration

### Environment Variables (`.env`)

| Variable             | Required | Description                                                                                            |
| -------------------- | -------- | ------------------------------------------------------------------------------------------------------ |
| `LITELLM_MASTER_KEY` | Yes      | Admin API key (must start with `sk-`). Generate: `openssl rand -hex 24 \| sed 's/^/sk-/'`              |
| `LITELLM_SALT_KEY`   | Yes      | DB encryption key (base64). **Cannot be rotated after first use!** Generate: `openssl rand -base64 32` |
| `POSTGRES_PASSWORD`  | Yes      | PostgreSQL password                                                                                    |
| `REDIS_PASSWORD`     | Yes      | Redis password                                                                                         |
| `UI_USERNAME`        | No       | Admin UI basic auth username (default: `admin`)                                                        |
| `UI_PASSWORD`        | No       | Admin UI basic auth password                                                                           |
| `NVIDIA_API_KEY_1`   | Yes*     | Primary NVIDIA NIM API key (Nemotron 3 Ultra, GPT-OSS 120B)                                            |
| `NVIDIA_API_KEY_2`   | Yes*     | Secondary NVIDIA NIM API key for load balancing                                                        |
| `OPENCODE_API_KEY`   | Yes*     | OpenCode Zen API key (mimo-v2.5-free, hy3-free)                                                        |
| `AGNES_API_KEY`      | Yes*     | Agnes AI API key (agnes-2.0-flash)                                                                     |

*At least one provider's keys required. Missing keys = those models return 401.

Copy `.env.example` to `.env` and fill in your keys.

### Model Routing (`litellm/config.yaml`)

| Model Alias                 | Provider     | Backend Model                       | Load Balancing                  |
| --------------------------- | ------------ | ----------------------------------- | ------------------------------- |
| `claude-opus-4-8`           | NVIDIA NIM   | `nvidia/nemotron-3-ultra-550b-a55b` | 2x NVIDIA keys (shuffle)        |
| `claude-sonnet-5`           | OpenCode Zen | `mimo-v2.5-free`, `hy3-free`        | 2x OpenCode endpoints (shuffle) |
| `claude-haiku-4-5-20251001` | NVIDIA NIM   | `openai/gpt-oss-120b`               | 2x NVIDIA keys (shuffle)        |
| `agnes-2.0-flash`           | Agnes AI     | `agnes-2.0-flash`                   | Single endpoint                 |

**Router Settings** (`router_settings`):

- `routing_strategy: simple-shuffle` — round-robin across duplicate model entries
- `num_retries: 2` — retry failed requests up to 2 times
- `timeout: 30` — request timeout in seconds
- `allowed_fails: 2` — mark deployment unhealthy after 2 failures
- `cooldown_time: 30` — seconds before retrying failed deployment
- `enable_pre_call_checks: true` — health check before routing
- `redis_host/port/password` — Redis for cross-worker rate limiting

**Fallbacks** (`router_settings.fallbacks`):

- `claude-sonnet-5` → `claude-haiku-4-5-20251001`
- `claude-opus-4-8` → `claude-sonnet-5`
- Context window fallbacks: sonnet → haiku
- Content policy fallbacks: opus → sonnet

**LiteLLM Settings** (`litellm_settings`):

- `drop_params: true` — drop unsupported parameters for cross-provider compatibility
- `use_chat_completions_url_for_anthropic_messages: true` — route via `/v1/chat/completions`
- `cache: true` + `cache_params.type: redis` — Redis response caching

## Usage

### Configure Claude Code

```bash
export ANTHROPIC_AUTH_TOKEN=sk-12345
export ANTHROPIC_BASE_URL=http://localhost:4000
```

Add to your shell profile (`~/.bashrc`, `~/.zshrc`, `~/.profile`) and restart your terminal.

### Test the Proxy

```bash
# List available models (requires auth)
curl -H "Authorization: Bearer <YOUR_VIRTUAL_KEY>" http://localhost:4000/v1/models

# Chat completion
curl -X POST http://localhost:4000/v1/chat/completions \
  -H "Authorization: Bearer <YOUR_VIRTUAL_KEY>" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-opus-4-8",
    "messages": [{"role": "user", "content": "Hello!"}],
    "max_tokens": 100
  }'
```

### Virtual Key Management

```bash
# Generate a virtual key with budget and rate limits
# (Use master key for admin operations)
curl -X POST http://localhost:4000/key/generate \
  -H "Authorization: Bearer <MASTER_KEY>" \
  -H "Content-Type: application/json" \
  -d '{
    "models": ["claude-opus-4-8", "claude-sonnet-5", "claude-haiku-4-5-20251001", "agnes-2.0-flash"],
    "max_budget": 50,
    "budget_duration": "30d",
    "rpm_limit": 60,
    "metadata": {"team": "engineering", "owner": "user@example.com"}
  }'

# Check key info
curl -H "Authorization: Bearer <MASTER_KEY>" \
  "http://localhost:4000/key/info?key=<VIRTUAL_KEY>"

# List all keys
curl -H "Authorization: Bearer <MASTER_KEY>" \
  http://localhost:4000/key/list

# Revoke a key
curl -X POST http://localhost:4000/key/delete \
  -H "Authorization: Bearer <MASTER_KEY>" \
  -H "Content-Type: application/json" \
  -d '{"key": "<VIRTUAL_KEY>"}'

# Spend report
curl -H "Authorization: Bearer <MASTER_KEY>" \
  "http://localhost:4000/global/spend/report"
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

| Alias                       | Backend                            |
| --------------------------- | ---------------------------------- |
| `claude-opus-4-8`           | NVIDIA Nemotron 3 Ultra 550B       |
| `claude-sonnet-5`           | OpenCode mimo-v2.5-free / hy3-free |
| `claude-haiku-4-5-20251001` | NVIDIA GPT-OSS 120B                |
| `agnes-2.0-flash`           | Agnes 2.0 Flash                    |

## Project Structure

```
litellm-proxy/
├── docker-compose.yml          # Docker Compose (LiteLLM + Postgres + Redis)
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

| Task                         | Command                                        |
| ---------------------------- | ---------------------------------------------- |
| Update API keys              | Edit `.env` file, then restart                 |
| Modify routing/providers     | Edit `litellm/config.yaml`, then restart       |
| Restart after config changes | `docker compose down && docker compose up -d`  |
| Update LiteLLM image         | `docker compose pull && docker compose up -d`  |
| View logs                    | `docker compose logs -f`                       |
| Check container status       | `docker compose ps`                            |
| Admin UI                     | `http://localhost:4000/ui`                     |
| Generate virtual key         | `curl -X POST ... /key/generate`               |
| Health check (liveness)      | `curl http://localhost:4000/health/liveliness` |
| Health check (readiness)     | `curl http://localhost:4000/health/readiness`  |

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

MIT
