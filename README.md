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

A proxy gateway that routes **Claude Code** through **LiteLLM** to multiple AI backend providers with load balancing, rate limiting, and fallback chains.

## Features

- **Multi-provider routing**: Access NVIDIA NIM and OpenCode Zen through a single endpoint
- **Load balancing**: `simple-shuffle` distributes requests across multiple model deployments per virtual model
- **Rate limiting**: Per-deployment RPM/TPM enforcement via `enforce_model_rate_limits`
- **Automatic fallbacks**: `opus → sonnet → haiku` chain across providers
- **Parameter normalization**: Drops unsupported parameters (`drop_params: true`) for cross-provider compatibility
- **Anthropic compatibility**: Routes all providers via `/v1/chat/completions` for Claude Code integration
- **Dockerized**: Builds a patched LiteLLM image (`litellm-proxy:patched`) with Nemotron streaming fixes
- **Redis caching**: Caches repeated prompts for cost savings and latency reduction
- **PostgreSQL backend**: Persistent storage for virtual keys, budgets, and spend logs
- **Virtual keys**: Revocable API keys with budgets, rate limits, and model allowlists
- **Cost tracking**: Per-model, per-key, per-user spend logging to PostgreSQL
- **Health checks**: Liveness (`/health/liveliness`) and readiness (`/health/readiness`) endpoints

## Architecture

### Model Routing

Each virtual model (`claude-opus-4-8`, `claude-sonnet-5`, etc.) maps to **multiple backend deployments** across different providers. LiteLLM's `simple-shuffle` router distributes requests, and `enforce_model_rate_limits` blocks requests before hitting provider limits.

```
claude-opus-4-8 → 2 pools: OpenCode Zen (30 RPM) + NVIDIA NIM (40 RPM)
claude-sonnet-5 → 2 pools: OpenCode Zen (30 RPM) + NVIDIA NIM (40 RPM)
claude-haiku-4-5 → 2 pools: NVIDIA NIM key 1 (40 RPM) + NVIDIA NIM key 2 (40 RPM)
```

### Fallback Chain

```
claude-opus-4-8 → claude-sonnet-5 → claude-haiku-4-5-20251001
```

When a model group fails after `num_retries`, LiteLLM automatically tries the next model in the chain.

### Rate Limits (Documented Free Tiers)

| Provider     | RPM | TPM           | Scope                      |
| ------------ | --- | ------------- | -------------------------- |
| NVIDIA NIM   | 40  | 500,000       | Per API key                |
| OpenCode Zen | 30  | Not published | Per API key (conservative) |
| Agnes AI     | 20  | Not published | Per API key                |

### NVIDIA NIM Worker Limits

NVIDIA NIM enforces **32 concurrent requests per worker** (separate from RPM). The `ResourceExhausted: Worker local total request limit reached` error occurs when concurrent requests exceed this limit. Using different models on NIM provides **separate worker pools** (each model = own 32-slot pool).

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

# 2. Generate required keys and populate .env
LITELLM_MASTER_KEY="sk-$(openssl rand -hex 24)"
LITELLM_SALT_KEY="$(openssl rand -base64 32)"
POSTGRES_PASSWORD="$(openssl rand -base64 16)"
REDIS_PASSWORD="$(openssl rand -base64 16)"

# Write to .env
cat > .env <<EOF
LITELLM_MASTER_KEY=$LITELLM_MASTER_KEY
LITELLM_SALT_KEY=$LITELLM_SALT_KEY
POSTGRES_PASSWORD=$POSTGRES_PASSWORD
REDIS_PASSWORD=$REDIS_PASSWORD
UI_USERNAME=admin
UI_PASSWORD=changeme123
EOF

# 3. Add YOUR provider API keys to .env (edit the file)
# Required: NVIDIA_API_KEY_1, NVIDIA_API_KEY_2, OPENCODE_API_KEY

# 4. Start the proxy stack
docker compose up -d

# 5. Verify
curl http://localhost:4000/health/liveliness   # "I'm alive!"
curl http://localhost:4000/health/readiness   # {"status":"healthy","db":"connected"}

# 6. Create a virtual key (use for apps, NOT master key)
VKEY=$(curl -s -X POST http://localhost:4000/key/generate \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"models": ["claude-opus-4-8", "claude-sonnet-5", "claude-haiku-4-5-20251001"], "max_budget": 50, "budget_duration": "30d", "rpm_limit": 60}' | jq -r .key)

# 7. Test with virtual key
curl -H "Authorization: Bearer $VKEY" http://localhost:4000/v1/models
curl -X POST http://localhost:4000/v1/chat/completions \
  -H "Authorization: Bearer $VKEY" \
  -H "Content-Type: application/json" \
  -d '{"model": "claude-opus-4-8", "messages": [{"role": "user", "content": "Hello!"}], "max_tokens": 50}'

# 8. Open Admin UI
# http://localhost:4000/ui  (user: admin, pass: changeme123)
```

### Configure Claude Code

```bash
export ANTHROPIC_AUTH_TOKEN=$VKEY
export ANTHROPIC_BASE_URL=http://localhost:4000
```

Add to your shell profile (`~/.bashrc`, `~/.zshrc`, `~/.profile`) and restart your terminal.

## Configuration

### Environment Variables (`.env`)

| Variable             | Required | Description                                                                                            |
| -------------------- | -------- | ------------------------------------------------------------------------------------------------------ |
| `LITELLM_MASTER_KEY` | Yes      | Admin API key (must start with `sk-`). Generate: `openssl rand -hex 24                                 | sed 's/^/sk-/'` |
| `LITELLM_SALT_KEY`   | Yes      | DB encryption key (base64). **Cannot be rotated after first use!** Generate: `openssl rand -base64 32` |
| `POSTGRES_PASSWORD`  | Yes      | PostgreSQL password                                                                                    |
| `REDIS_PASSWORD`     | Yes      | Redis password                                                                                         |
| `UI_USERNAME`        | No       | Admin UI basic auth username (default: `admin`)                                                        |
| `UI_PASSWORD`        | No       | Admin UI basic auth password                                                                           |
| `NVIDIA_API_KEY_1`   | Yes*     | Primary NVIDIA NIM API key                                                                             |
| `NVIDIA_API_KEY_2`   | Yes*     | Secondary NVIDIA NIM API key                                                                           |
| `OPENCODE_API_KEY`   | Yes*     | OpenCode Zen API key                                                                                   |

*At least one provider's keys required. Missing keys = those models return 401.

### Model Routing (`litellm/config.yaml`)

| Model Alias                 | Deployment 1 (OpenCode Zen, 30 RPM) | Deployment 2 (NVIDIA NIM, 40 RPM)                        |
| --------------------------- | ----------------------------------- | -------------------------------------------------------- |
| `claude-opus-4-8`           | `mimo-v2.5-free`                    | `nemotron-3-ultra-550b-a55b` (key 1)                     |
| `claude-sonnet-5`           | `hy3-free` (295B MoE)               | `nemotron-4-340b-instruct` (key 2)                       |
| `claude-haiku-4-5-20251001` | —                                   | `nemotron-3-nano-30b-a3b` (key 1) + `mistral-7b` (key 2) |

**Rate Limits** (enforced via `enforce_model_rate_limits`):

| Provider     | Models                        | RPM | TPM     |
| ------------ | ----------------------------- | --- | ------- |
| NVIDIA NIM   | nemotron-3-ultra, nemotron-4, | 40  | 100,000 |
|              | nemotron-3-nano, mistral-7b   |     |         |
| OpenCode Zen | mimo-v2.5-free, hy3-free      | 30  | 100,000 |

**Router Settings** (`router_settings`):

- `routing_strategy: simple-shuffle` — random pick across deployments with RPM-aware weighting
- `num_retries: 2` — retry failed requests up to 2 times before fallback
- `timeout: 90` — request timeout in seconds
- `allowed_fails: 2` — mark deployment unhealthy after 2 failures
- `cooldown_time: 30` — seconds before retrying failed deployment
- `enable_pre_call_checks: true` — health check before routing
- `optional_pre_call_checks: [enforce_model_rate_limits]` — hard-enforce RPM/TPM per deployment
- `redis_host/port/password` — Redis for cross-worker rate limiting

**Fallbacks** (`router_settings.fallbacks`):

- `claude-opus-4-8` → `claude-sonnet-5`
- `claude-sonnet-5` → `claude-haiku-4-5-20251001`

**LiteLLM Settings** (`litellm_settings`):

- `drop_params: true` — drop unsupported parameters for cross-provider compatibility
- `use_chat_completions_url_for_anthropic_messages: true` — route via `/v1/chat/completions`
- `cache: true` + `cache_params.type: redis` — Redis response caching
- `track_costs: true` / `track_costs_by_model: true` — cost tracking to PostgreSQL

## Usage

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
curl -X POST http://localhost:4000/key/generate \
  -H "Authorization: Bearer <MASTER_KEY>" \
  -H "Content-Type: application/json" \
  -d '{
    "models": ["claude-opus-4-8", "claude-sonnet-5", "claude-haiku-4-5-20251001"],
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

| Alias                       | Backend                                                |
| --------------------------- | ------------------------------------------------------ |
| `claude-opus-4-8`           | NVIDIA Nemotron 3 Ultra 550B + OpenCode mimo-v2.5-free |
| `claude-sonnet-5`           | NVIDIA Nemotron 4 340B + OpenCode hy3-free             |
| `claude-haiku-4-5-20251001` | NVIDIA Nemotron 3 Nano 30B + Mistral 7B                |

## Project Structure

```
litellm-proxy/
├── docker-compose.yml          # Docker Compose (LiteLLM + Postgres + Redis)
├── Dockerfile                  # Builds litellm-proxy:patched from official image
├── patches/
│   ├── fix_nemotron_thinking_stream.py  # Patches LiteLLM SSE streaming bugs
│   └── fix_empty_choices.py            # Empty-choices streaming guard
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

MIT
