# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## Repository Overview

This is an **AI proxy gateway** that routes **Claude Code** through **LiteLLM** (Python) to multiple AI backend providers with load balancing and parameter normalization. The repo is intentionally minimal — there is no application source code to build or test. It exists to deploy and operate a single Docker service.

---

## Architecture

**Single-service deployment:** One container running the official LiteLLM Docker image, mounted with a read-only config file and an `.env` of API keys. The container exposes port **4000** on the host.

```
~/.profile env vars         Claude Code CLI
        │
        ▼
localhost:4000  ──►  LiteLLM container  ──►  NVIDIA NIM
                                          ──►  OpenCode Zen
                                          ──►  Agnes AI (fallback)
```

**Key files:**

| Path                  | Role                                                                                                |
| --------------------- | --------------------------------------------------------------------------------------------------- |
| `docker-compose.yml`  | Defines the single `litellm` service, mounts, port, env_file                                        |
| `litellm/config.yaml` | Model list (3 models) + LiteLLM routing/normalization settings                                      |
| `.env`                | API keys (gitignored) — `NVIDIA_API_KEY_1`, `NVIDIA_API_KEY_2`, `OPENCODE_API_KEY`, `AGNES_API_KEY` |
| `.env.example`        | Template for `.env`                                                                                 |
| `AGENTS.md`           | Operator runbook — quick reference, failure handling                                                |
| `README.md`           | Setup, provider table, .profile values, stack management                                            |

**Provider model map** (`litellm/config.yaml`):

- `gpt-oss-120b` → `nvidia_nim/openai/gpt-oss-120b` via `https://integrate.api.nvidia.com/v1` (2-key deployment)
- `mimo-v2.5` → `openai/mimo-v2.5-free` via `https://opencode.ai/zen/v1`
- `nemotron-ultra-550b` → `nvidia_nim/nvidia/nemotron-3-ultra-550b-a55b` via `https://integrate.api.nvidia.com/v1` (2-key deployment)
- `agnes-2.0-flash` → `openai/agnes-2.0-flash` via `https://apihub.agnes-ai.com/v1` (fallback for `mimo-v2.5`)

**LiteLLM settings:**

- `drop_params: true` — drops unsupported params for cross-provider compatibility.
- `use_chat_completions_url_for_anthropic_messages: true` — routes Anthropic-style messages via `/v1/chat/completions` upstream.
- `fallbacks: [{"mimo-v2.5": ["agnes-2.0-flash"]}]` — `mimo-v2.5` falls back to `agnes-2.0-flash` after retries.
- `nemotron-ultra-550b` keeps upstream reasoning **enabled** (`extra_body.chat_template_kwargs.enable_thinking: true` + `force_nonempty_content: true` for tool use). Claude Code must be told the model supports thinking via `ANTHROPIC_DEFAULT_SONNET_MODEL_SUPPORTED_CAPABILITIES='thinking,interleaved_thinking'` in `~/.profile` — without it, the proxy's thinking blocks are rejected with "Content block is not a thinking block". Note: NIM's param is `enable_thinking`, **not** `thinking`; the previously documented `thinking: false` was incorrect and also disabled reasoning.

**Router settings:**

- `routing_strategy: simple-shuffle` — random distribution across the 2 NVIDIA key deployments (~80 rpm combined).
- `num_retries: 1` — retries failed calls (e.g. `429`) on the other NVIDIA key.
- `timeout: 120` — caps each request at 120s.

---

## Common Commands

The codebase is configuration-only — there is no build, lint, or test step. All workflows are Docker Compose operations on a single container.

```bash
# Deploy / start
docker compose up -d

# Stop
docker compose down

# Tail logs
docker compose logs -f

# Restart (does NOT reload config — see below)
docker compose restart

# Update image + restart
docker compose pull && docker compose up -d

# Health check / list models
curl http://localhost:4000/v1/models
```

**Config changes require a full container restart** — the config file is mounted `:ro`, so `docker compose restart` alone will **not** pick up changes to `litellm/config.yaml`:

```bash
docker compose down && docker compose up -d
```

---

## Client Configuration

Claude Code is configured to hit the proxy via environment variables (typically set in `~/.profile`):

```bash
export ANTHROPIC_BASE_URL=http://localhost:4000
export ANTHROPIC_DEFAULT_OPUS_MODEL=nemotron-ultra-550b
export ANTHROPIC_DEFAULT_SONNET_MODEL=mimo-v2.5
export ANTHROPIC_DEFAULT_HAIKU_MODEL=gpt-oss-120b
```

After editing, `source ~/.profile` or open a new shell before running `claude`.

---

## Failure Handling (from AGENTS.md)

| Symptom                   | Fix                                                                   |
| ------------------------- | --------------------------------------------------------------------- |
| `address already in use`  | `docker ps` — leftover containers on port 4000                        |
| `401` / `Invalid API key` | Verify the required keys are set in `.env`                            |
| Service won't start       | `docker compose logs -f` — check startup logs                         |
| `model not found`         | Update model names in `litellm/config.yaml`                           |
| Config not applied        | `docker compose down && up -d` (not `restart`) — config loads at boot |

**Escalate if:** (1) backend API quota exceeded (`429`), (2) Docker daemon unavailable, (3) Claude Code still can't see the model after a config change.

---

## Project Conventions

- **Config file:** `litellm/config.yaml` is the source of truth for routing and normalization. Every model entry needs an `api_key: os.environ/<NAME>` reference and the matching var in `.env`.
- **API keys:** Environment variables only. `.env` is gitignored; never commit secrets.
- **No app code:** Resist the urge to add scripts, tests, or framing that this repo doesn't already own. Changes here should be limited to `litellm/config.yaml`, `docker-compose.yml`, `README.md`, and `AGENTS.md`.
