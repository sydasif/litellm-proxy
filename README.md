# AI Proxy Gateway

[![LiteLLM](https://img.shields.io/badge/Powered%20by-LiteLLM-blueviolet?style=for-the-badge)](https://github.com/BerriAI/litellm)
[![Claude Code](https://img.shields.io/badge/Claude%20Code-D97757?style=for-the-badge&logo=anthropic&logoColor=white)](https://claude.com/product/claude-code)
[![OpenCode Zen](https://img.shields.io/badge/Backend-OpenCode%20Zen-6C47FF?style=for-the-badge)](https://opencode.ai)
[![Google Gemini](https://img.shields.io/badge/Backend-Google%20Gemini-4285F4?style=for-the-badge&logo=google&logoColor=white)](https://deepmind.google/technologies/gemini/)
[![Docker](https://img.shields.io/badge/docker-%230db7ed.svg?style=for-the-badge&logo=docker&logoColor=white)](https://www.docker.com/)

A proxy gateway that routes Claude Code through **LiteLLM** (Python) to multiple backend providers.

---

## Features

- **Single proxy**: LiteLLM (Python, simple, load-balanced)
- **Multi-provider routing**: OpenCode, Gemini through a single endpoint
- **Load balancing**: Multiple API keys per provider
- **Docker Native**: Official images, compose file ready
- **Secure**: Environment-based API key management

---

## Project Structure

```
litellm-proxy/
├── docker-compose.yml          # Active compose (LiteLLM)
├── .env                        # API keys
├── .env.example
├── .gitignore
├── AGENTS.md
├── README.md
└── litellm/
    └── config.yaml             # LiteLLM provider config
```

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

Required keys: `OPENCODE_API_KEY`, `GEMINI_API_KEY`.

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

### LiteLLM — `litellm/config.yaml`

| Model ID           | Backend                        | Timeout | Keys                                                |
| :----------------- | :----------------------------- | :------ | :-------------------------------------------------- |
| `nemotron-3-ultra` | opencode/nemotron-3-ultra-free | 300s    | OPENCODE_API_KEY                                    |
| `deepseek-v4-flash` | opencode/deepseek-v4-flash-free | 300s    | OPENCODE_API_KEY                                    |
| `gemma-4-31b`      | gemini/gemma-4-31b-it          | 120s    | GEMINI_API_KEY                                      |

**Request format:** Use the Model ID directly (e.g. `gemma-4-31b`, `nemotron-3-ultra`).

---

## Using with Claude Code

Set these in `~/.profile` (or equivalent):

```bash
export ANTHROPIC_BASE_URL=http://localhost:4000
export ANTHROPIC_DEFAULT_OPUS_MODEL=nemotron-3-ultra
export ANTHROPIC_DEFAULT_SONNET_MODEL=gemma-4-31b
export ANTHROPIC_DEFAULT_HAIKU_MODEL=deepseek-v4-flash
```

After editing, `source ~/.profile` or open a new shell before running `claude`.

---

## Reference

| Action           | Command                                       |
| :--------------- | :-------------------------------------------- |
| **Start**        | `docker compose up -d`                        |
| **Stop**         | `docker compose down`                         |
| **View Logs**    | `docker compose logs -f`                      |
| **Restart**      | `docker compose restart`                      |
| **Update**       | `docker compose pull && docker compose up -d` |
| **Models Check** | `curl http://localhost:4000/v1/models`        |

---

## License

MIT
