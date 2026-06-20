# AI Proxy Gateway

[![Bifrost](https://img.shields.io/badge/Powered%20by-Bifrost-FF6B35?style=for-the-badge)](https://github.com/maximhq/bifrost)
[![LiteLLM](https://img.shields.io/badge/Powered%20by-LiteLLM-blueviolet?style=for-the-badge)](https://github.com/BerriAI/litellm)
[![Claude Code](https://img.shields.io/badge/Claude%20Code-D97757?style=for-the-badge&logo=anthropic&logoColor=white)](https://claude.com/product/claude-code)
[![OpenCode Zen](https://img.shields.io/badge/Backend-OpenCode%20Zen-6C47FF?style=for-the-badge)](https://opencode.ai)
[![Google Gemini](https://img.shields.io/badge/Backend-Google%20Gemini-4285F4?style=for-the-badge&logo=google&logoColor=white)](https://deepmind.google/technologies/gemini/)
[![Docker](https://img.shields.io/badge/docker-%230db7ed.svg?style=for-the-badge&logo=docker&logoColor=white)](https://www.docker.com/)

A proxy gateway that routes Claude Code through either **Bifrost** (Go, ~11µs overhead) or **LiteLLM** (Python) to multiple backend providers.

---

## Features

- **Two proxy options**: Bifrost (Go, fast, Redis caching) or LiteLLM (Python, simple, load-balanced)
- **Multi-provider routing**: OpenCode, Gemini, Agnes through a single endpoint
- **Load balancing**: Multiple API keys per provider (e.g. 2 Gemini keys in both proxies)
- **Docker Native**: Official images, compose files ready
- **Secure**: Environment-based API key management

---

## Project Structure

```
litellm-proxy/
├── docker-compose.yml          # Active compose (Bifrost or LiteLLM)
├── docker-compose.bifrost.yml  # Bifrost compose (inactive)
├── docker-compose.litellm.yml  # LiteLLM compose (inactive)
├── .env                        # API keys
├── .env.example
├── .gitignore
├── AGENTS.md
├── README.md
├── bifrost/
│   └── config.json             # Bifrost provider config
└── litellm/
    └── config.yaml             # LiteLLM provider config
```

> **Note:** Only one compose file is active at a time. The `docker-compose.yml` is symlinked/moved depending on which proxy is running. When Bifrost is active, `docker-compose.bifrost.yml` doesn't exist (it is `docker-compose.yml`), and vice versa.

---

## Prerequisites

- [Docker Desktop](https://docs.docker.com/get-docker/) or Docker Engine
- [Docker Compose](https://docs.docker.com/compose/install/)
- API keys for the backends you plan to use (see `.env.example`)

---

## Quick Start

### 1. Configure Environment

```bash
cp .env.example .env
# Edit .env and add your API keys
```

Required keys: `OPENCODE_API_KEY`, `GEMINI_API_KEY_1`, `GEMINI_API_KEY_2`, `AGNES_API_KEY`.

### 2. Deploy

```bash
docker compose up -d
```

Proxy is now running at `http://localhost:4000`.

### 3. Verify

```bash
curl http://localhost:4000/v1/models
```

---

## Provider Configuration

### Bifrost — `bifrost/config.json`

| Provider | Timeout | Models                  | Keys                             |
| :------- | :------ | :---------------------- | :------------------------------- |
| opencode | 300s    | `nemotron-3-ultra-free` | 1 key, weight 1.0                |
| gemini   | 120s    | `gemma-4-31b-it`        | 2 keys, load-balanced (0.5 each) |
| agnes    | 180s    | `agnes-2.0-flash`       | 1 key, weight 1.0                |

**Request format:** `<provider>/<model>` (e.g. `opencode/nemotron-3-ultra-free`).

### LiteLLM — `litellm/config.yaml`

| Model ID           | Backend                        | Timeout | Keys                                                |
| :----------------- | :----------------------------- | :------ | :-------------------------------------------------- |
| `nemotron-3-ultra` | opencode/nemotron-3-ultra-free | 300s    | OPENCODE_API_KEY                                    |
| `gemma4-31b`       | gemini/gemma-4-31b-it          | 120s    | GEMINI_API_KEY_1 + GEMINI_API_KEY_2 (load-balanced) |
| `agnes-2.0-flash`  | openai/agnes-2.0-flash         | 180s    | AGNES_API_KEY                                       |

**Request format:** Use the Model ID directly (e.g. `gemma4-31b`, `nemotron-3-ultra`).

---

## Using with Claude Code

The proxy URL and model names differ depending on which proxy is active. Set these in `~/.profile` (or equivalent):

### Bifrost

```bash
export ANTHROPIC_BASE_URL=http://localhost:4000/anthropic
export ANTHROPIC_DEFAULT_OPUS_MODEL=opencode/nemotron-3-ultra-free
export ANTHROPIC_DEFAULT_SONNET_MODEL=gemini/gemma-4-31b-it
export ANTHROPIC_DEFAULT_HAIKU_MODEL=agnes/agnes-2.0-flash
```

### LiteLLM

```bash
export ANTHROPIC_BASE_URL=http://localhost:4000
export ANTHROPIC_DEFAULT_OPUS_MODEL=nemotron-3-ultra
export ANTHROPIC_DEFAULT_SONNET_MODEL=gemma4-31b
export ANTHROPIC_DEFAULT_HAIKU_MODEL=agnes-2.0-flash
```

---

## Switching Between Proxies

Each switch requires **both** a compose file swap and a `.profile` update.

### Bifrost → LiteLLM

```bash
# 1. Stop current stack
docker compose down

# 2. Swap compose files
mv docker-compose.yml docker-compose.bifrost.yml
mv docker-compose.litellm.yml docker-compose.yml

# 3. Update ~/.profile (LiteLLM values)
sed -i 's|ANTHROPIC_BASE_URL=http://localhost:4000/anthropic|ANTHROPIC_BASE_URL=http://localhost:4000|' ~/.profile
sed -i 's|ANTHROPIC_DEFAULT_OPUS_MODEL=.*|ANTHROPIC_DEFAULT_OPUS_MODEL=nemotron-3-ultra|' ~/.profile
sed -i 's|ANTHROPIC_DEFAULT_SONNET_MODEL=.*|ANTHROPIC_DEFAULT_SONNET_MODEL=gemma4-31b|' ~/.profile
sed -i 's|ANTHROPIC_DEFAULT_HAIKU_MODEL=.*|ANTHROPIC_DEFAULT_HAIKU_MODEL=agnes-2.0-flash|' ~/.profile

# 4. Start new stack
docker compose up -d
```

### LiteLLM → Bifrost

```bash
# 1. Stop current stack
docker compose down

# 2. Swap compose files
mv docker-compose.yml docker-compose.litellm.yml
mv docker-compose.bifrost.yml docker-compose.yml

# 3. Update ~/.profile (Bifrost values)
sed -i 's|ANTHROPIC_BASE_URL=http://localhost:4000|ANTHROPIC_BASE_URL=http://localhost:4000/anthropic|' ~/.profile
sed -i 's|ANTHROPIC_DEFAULT_OPUS_MODEL=.*|ANTHROPIC_DEFAULT_OPUS_MODEL=opencode/nemotron-3-ultra-free|' ~/.profile
sed -i 's|ANTHROPIC_DEFAULT_SONNET_MODEL=.*|ANTHROPIC_DEFAULT_SONNET_MODEL=gemini/gemma-4-31b-it|' ~/.profile
sed -i 's|ANTHROPIC_DEFAULT_HAIKU_MODEL=.*|ANTHROPIC_DEFAULT_HAIKU_MODEL=agnes/agnes-2.0-flash|' ~/.profile

# 4. Start new stack
docker compose up -d
```

After switching, `source ~/.profile` or open a new shell before running `claude`.

---

## Reference

| Action                | Command                                                     |
| :-------------------- | :---------------------------------------------------------- |
| **Start**             | `docker compose up -d`                                      |
| **Stop**              | `docker compose down`                                       |
| **View Logs**         | `docker compose logs -f`                                    |
| **Restart**           | `docker compose restart`                                    |
| **Update**            | `docker compose pull && docker compose up -d`               |
| **Models Check**      | `curl http://localhost:4000/v1/models`                      |
| **Switch to LiteLLM** | See [Switching Between Proxies](#switching-between-proxies) |
| **Switch to Bifrost** | See [Switching Between Proxies](#switching-between-proxies) |

---

## License

MIT
