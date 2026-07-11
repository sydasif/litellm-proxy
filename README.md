# AI Proxy Gateway

[![LiteLLM](https://img.shields.io/badge/Powered%20by-LiteLLM-blueviolet?style=for-the-badge)](https://github.com/BerriAI/litellm)
[![Claude Code](https://img.shields.io/badge/Claude%20Code-D97757?style=for-the-badge&logo=anthropic&logoColor=white)](https://claude.com/product/claude-code)
[![OpenCode Zen](https://img.shields.io/badge/Backend-OpenCode%20Zen-6C47FF?style=for-the-badge)](https://opencode.ai)
[![Docker](https://img.shields.io/badge/docker-%230db7ed.svg?style=for-the-badge&logo=docker&logoColor=white)](https://www.docker.com/)

A proxy gateway that routes **Claude Code** through **LiteLLM** (Python) to multiple AI backend providers with load balancing and parameter normalization.

---

## Features

- **Single proxy**: LiteLLM with official Docker image, load-balanced routing
- **Multi-provider routing**: NVIDIA NIM, OpenCode Zen, and Agnes AI through a single `localhost:4000` endpoint
- **Load balancing**: Two NVIDIA API keys with `simple-shuffle` strategy and cross-key retry
- **Fallbacks**: `mimo-v2.5` falls back to `agnes-2.0-flash` if it fails after retries
- **Parameter normalization**: `drop_params: true` drops unsupported params for cross-provider compatibility
- **Anthropic message routing**: `use_chat_completions_url_for_anthropic_messages: true` routes all providers via `/v1/chat/completions`
- **Docker Native**: Official LiteLLM image, compose file ready
- **Secure**: Environment-based API key management

---

## Project Structure

```
litellm-proxy/
├── docker-compose.yml          # Active compose (LiteLLM)
├── .env                        # API keys (gitignored)
├── .env.example                # Template
├── .gitignore
├── AGENTS.md                   # Operator runbook
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
# Edit .env with your real API keys
```

Required keys:

| Key                | Purpose                                   |
| ------------------ | ----------------------------------------- |
| `NVIDIA_API_KEY_1` | NVIDIA NIM backend authentication (key 1) |
| `NVIDIA_API_KEY_2` | NVIDIA NIM backend authentication (key 2) |
| `OPENCODE_API_KEY` | OpenCode Zen backend authentication       |
| `AGNES_API_KEY`    | Agnes AI backend (fallback for mimo-v2.5) |

### 2. Deploy

```bash
docker compose up -d
```

The LiteLLM proxy starts on port **4000**.

### 3. Verify

```bash
curl http://localhost:4000/v1/models
```

---

## Provider Configuration

Config lives in `litellm/config.yaml`.

### Models

| Model ID              | LiteLLM Model                                  | Base URL                              | RPM     | Keys                 |
| :-------------------- | :--------------------------------------------- | :------------------------------------ | :------ | :------------------- |
| `gpt-oss-120b`        | `nvidia_nim/openai/gpt-oss-120b`               | `https://integrate.api.nvidia.com/v1` | ~80 rpm | `NVIDIA_API_KEY_1/2` |
| `mimo-v2.5`           | `openai/mimo-v2.5-free`                        | `https://opencode.ai/zen/v1`          | 30 rpm  | `OPENCODE_API_KEY`   |
| `nemotron-ultra-550b` | `nvidia_nim/nvidia/nemotron-3-ultra-550b-a55b` | `https://integrate.api.nvidia.com/v1` | ~80 rpm | `NVIDIA_API_KEY_1/2` |
| `agnes-2.0-flash`     | `openai/agnes-2.0-flash`                       | `https://apihub.agnes-ai.com/v1`      | —       | `AGNES_API_KEY`      |

**Request format:** Use the Model ID directly (e.g. `gpt-oss-120b`, `mimo-v2.5`, `nemotron-ultra-550b`).

### LiteLLM Settings

| Setting                                           | Value                         | Purpose                                                   |
| :------------------------------------------------ | :---------------------------- | :-------------------------------------------------------- |
| `drop_params`                                     | `true`                        | Drops unsupported params for cross-provider compatibility |
| `use_chat_completions_url_for_anthropic_messages` | `true`                        | Routes all providers via `/v1/chat/completions`           |
| `routing_strategy`                                | `simple-shuffle`              | Random distribution across the 2 NVIDIA key deployments   |
| `num_retries`                                     | `1`                           | Retries failed calls on the other NVIDIA key              |
| `timeout`                                         | `120`                         | Caps each request at 120s                                 |
| `fallbacks`                                       | `mimo-v2.5 → agnes-2.0-flash` | Sonnet falls back to Agnes after retries                  |

---

## Using with Claude Code

Set these in `~/.profile` (or equivalent):

```bash
export ANTHROPIC_BASE_URL=http://localhost:4000
export ANTHROPIC_DEFAULT_OPUS_MODEL=nemotron-ultra-550b
export ANTHROPIC_DEFAULT_SONNET_MODEL=mimo-v2.5
export ANTHROPIC_DEFAULT_HAIKU_MODEL=gpt-oss-120b
```

After editing, `source ~/.profile` or open a new shell before running `claude`.

---

## Managing the Stack

| Action           | Command                                       |
| :--------------- | :-------------------------------------------- |
| **Start**        | `docker compose up -d`                        |
| **Stop**         | `docker compose down`                         |
| **View Logs**    | `docker compose logs -f`                      |
| **Restart**      | `docker compose restart`                      |
| **Update**       | `docker compose pull && docker compose up -d` |
| **Models Check** | `curl http://localhost:4000/v1/models`        |

### Config Changes

The config file is mounted **read-only** (`:ro`). Changes require a full container restart — `docker compose restart` alone will **not** pick up new config:

```bash
docker compose down && docker compose up -d
```

---

## License

MIT
