# AI Proxy Gateway

[![Bifrost](https://img.shields.io/badge/Powered%20by-Bifrost-FF6B35?style=for-the-badge)](https://github.com/maximhq/bifrost)
[![LiteLLM](https://img.shields.io/badge/Backup-LiteLLM-blueviolet?style=for-the-badge)](https://github.com/BerriAI/litellm)
[![Claude Code](https://img.shields.io/badge/Claude%20Code-D97757?style=for-the-badge&logo=anthropic&logoColor=white)](https://claude.com/product/claude-code)
[![OpenCode Zen](https://img.shields.io/badge/Backend-OpenCode%20Zen-6C47FF?style=for-the-badge)](https://opencode.ai)
[![Google Gemini](https://img.shields.io/badge/Backend-Google%20Gemini-4285F4?style=for-the-badge&logo=google&logoColor=white)](https://deepmind.google/technologies/gemini/)
[![Docker](https://img.shields.io/badge/docker-%230db7ed.svg?style=for-the-badge&logo=docker&logoColor=white)](https://www.docker.com/)

A high-performance proxy gateway that routes Claude Code through Bifrost (Go, 11µs overhead) to multiple backends — OpenCode Zen and Gemini. LiteLLM is available as a backup.

---

## Features

- **Fast**: Bifrost (Go) — 11µs overhead vs ~1-10ms (Python/LiteLLM).
- **Rate Limiting**: Built-in RPM governance per provider.
- **Lightweight Health Checks**: TCP socket — zero API quota burn.
- **Docker Native**: Official images, no build step.
- **Secure**: Environment-based API key management.
- **Backup Ready**: LiteLLM config preserved for instant fallback.

---

## Project Structure

```
litellm-proxy/
├── docker-compose.yml          # Bifrost (primary)
├── docker-compose.litellm.yml  # LiteLLM (backup)
├── .env                        # API keys
├── .env.example
├── .gitignore
├── AGENTS.md
├── README.md
├── bifrost/
│   └── config.json             # Bifrost provider config + rate limits
└── litellm/
    └── config.yaml             # LiteLLM model mappings (backup)
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
# Edit .env and add your API keys (OPENCODE_API_KEY, GEMINI_API_KEY_1, GEMINI_API_KEY_2)
```

### 2. Deploy

```bash
docker compose up -d
```

Proxy is now running at `http://localhost:4000`.

### 3. Verify

```bash
curl http://localhost:4000/health
```

---

## Provider Configuration

All provider and rate limit settings live in `bifrost/config.json`.

| Provider | Models                                                                                                    | RPM |
| :------- | :-------------------------------------------------------------------------------------------------------- | :-- |
| OpenCode | `big-pickle`, `mimo-v2.5-free`, `north-mini-code-free`, `nemotron-3-ultra-free`, `deepseek-v4-flash-free` | 30  |
| Gemini   | `gemma-4-31b-it`                                                                                          | 15  |

---

## Using with Claude Code

Update your global `settings.json`:

```json
{
  "env": {
    "ANTHROPIC_BASE_URL": "http://localhost:4000/anthropic",
    "ANTHROPIC_AUTH_TOKEN": "sk-xxx",
    "ANTHROPIC_DEFAULT_SONNET_MODEL": "opencode/mimo-v2.5-free",
    "ANTHROPIC_DEFAULT_OPUS_MODEL": "gemini/gemma-4-31b-it",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "opencode/nemotron-3-ultra-free"
  }
}
```

Model names must use the `<provider>/<model>` format (e.g. `opencode/mimo-v2.5-free`, `gemini/gemma-4-31b-it`).

---

## Switching to LiteLLM (Backup)

If you need to fall back to LiteLLM:

```bash
mv docker-compose.yml docker-compose.bifrost.yml
mv docker-compose.litellm.yml docker-compose.yml
docker compose down && docker compose up -d
```

To switch back to Bifrost:

```bash
mv docker-compose.yml docker-compose.litellm.yml
mv docker-compose.bifrost.yml docker-compose.yml
docker compose down && docker compose up -d
```

---

## Reference

| Action                | Command                                                  |
| :-------------------- | :------------------------------------------------------- |
| **Start**             | `docker compose up -d`                                   |
| **Stop**              | `docker compose down`                                    |
| **View Logs**         | `docker compose logs -f`                                 |
| **Restart**           | `docker compose restart`                                 |
| **Update**            | `docker compose pull && docker compose up -d`            |
| **Health Check**      | `curl http://localhost:4000/health`                      |
| **Switch to LiteLLM** | See [Switching to LiteLLM](#switching-to-litellm-backup) |

---

## License

MIT
