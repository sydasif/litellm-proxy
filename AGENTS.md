# AGENTS.md

**Role:** Proxy Gateway Operator
**Mandate:** Deploy and maintain either Bifrost or LiteLLM proxy gateway for Claude Code.

---

## Quick Reference

```bash
docker compose up -d                # Start (detached)
docker compose down                  # Stop
docker compose logs -f              # Tail logs
docker compose restart               # Restart services
curl http://localhost:4000/         # Health / models check
docker compose pull && docker compose up -d  # Update image + restart
```

---

## Architecture

Two compose files, mutually exclusive (only one active at a time):

| Aspect      | Bifrost (`docker-compose.bifrost.yml`) | LiteLLM (`docker-compose.litellm.yml`) |
| ----------- | -------------------------------------- | -------------------------------------- |
| Runtime     | Go binary                              | Python                                 |
| Containers  | Bifrost + Redis (2)                    | LiteLLM (1)                            |
| Port        | `4000:8080`                            | `4000:4000`                            |
| Cache       | Redis semantic cache (10m TTL)         | None                                   |
| DNS         | Explicit `8.8.8.8, 8.8.4.4`            | None                                   |
| Healthcheck | TCP `nc -z localhost 8080`             | None                                   |
| Config      | `bifrost/config.json`                  | `litellm/config.yaml`                  |

---

## Provider Configuration

### Bifrost — `bifrost/config.json`

| Provider | Timeout | Models                  | Keys                             |
| :------- | :------ | :---------------------- | :------------------------------- |
| opencode | 300s    | `nemotron-3-ultra-free` | 1 key, weight 1.0                |
| gemini   | 120s    | `gemma-4-31b-it`        | 2 keys, load-balanced (0.5 each) |
| agnes    | 180s    | `agnes-2.0-flash`       | 1 key, weight 1.0                |

**Request format:** `<provider>/<model>` (e.g. `opencode/nemotron-3-ultra-free`, `agnes/agnes-2.0-flash`).

**Gotchas:**

- Config is mounted read-only (`:ro`). Edit then `docker compose down && up -d` (not restart).
- OpenCode base_url: `https://opencode.ai/zen` (no `/v1` — Bifrost appends it internally).
- Redis must be healthy before Bifrost starts — crashes immediately if Redis isn't ready.
- Semantic caching: exact-match only, opt-in via `x-bf-cache-key` header.
- Health check is TCP socket (`nc -z`) — no quota burn.

### LiteLLM — `litellm/config.yaml`

| Model ID            | Backend                         | Timeout | Keys                                                |
| :------------------ | :------------------------------ | :------ | :-------------------------------------------------- |
| `nemotron-3-ultra`  | opencode/nemotron-3-ultra-free  | 300s    | OPENCODE_API_KEY                                    |
| `gemma-4-31b`       | gemini/gemma-4-31b-it           | 120s    | GEMINI_API_KEY_1 + GEMINI_API_KEY_2 (load-balanced) |
| `deepseek-v4-flash` | opencode/deepseek-v4-flash-free | 300s    | OPENCODE_API_KEY                                    |

**Request format:** Use the Model ID directly (e.g. `gemma-4-31b`, `nemotron-3-ultra`).

**Features:**

- `drop_params: true` — drops unsupported params for compatibility.
- `use_chat_completions_url_for_anthropic_messages: true` — routes upstream via `/v1/chat/completions` for all providers.
- `num_retries: 0` — no auto-retry.
- `routing_strategy: simple-shuffle` — random distribution across duplicate keys.

---

## Switching Between Proxies

Each switch requires **both** a compose file swap and a `.profile` update.

### `.profile` Values by Proxy

| Variable                         | Bifrost                           | LiteLLM                 |
| -------------------------------- | --------------------------------- | ----------------------- |
| `ANTHROPIC_BASE_URL`             | `http://localhost:4000/anthropic` | `http://localhost:4000` |
| `ANTHROPIC_DEFAULT_OPUS_MODEL`   | `opencode/nemotron-3-ultra-free`  | `nemotron-3-ultra`      |
| `ANTHROPIC_DEFAULT_SONNET_MODEL` | `gemini/gemma-4-31b-it`           | `gemma-4-31b`           |
| `ANTHROPIC_DEFAULT_HAIKU_MODEL`  | `agnes/agnes-2.0-flash`           | `deepseek-v4-flash`     |

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
sed -i 's|ANTHROPIC_DEFAULT_SONNET_MODEL=.*|ANTHROPIC_DEFAULT_SONNET_MODEL=gemma-4-31b|' ~/.profile
sed -i 's|ANTHROPIC_DEFAULT_HAIKU_MODEL=.*|ANTHROPIC_DEFAULT_HAIKU_MODEL=deepseek-v4-flash|' ~/.profile

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

## Failure Handling

| Symptom                   | Fix                                                                    |
| ------------------------- | ---------------------------------------------------------------------- |
| `address already in use`  | `docker ps` — leftover containers on port 4000                         |
| `401` / `Invalid API key` | Verify the required keys are set in `.env`                             |
| Service won't start       | `docker compose logs -f` — check startup logs                          |
| `model not found`         | Update model names in the active config file                           |
| Config not applied        | `docker compose down && up -d` (not restart) — config loads at startup |

---

## Stop & Ask

Escalate if: (1) backend API quota exceeded (`429`), (2) Docker daemon unavailable, (3) Claude Code still can't see the model after config change.
