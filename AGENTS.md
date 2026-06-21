# AGENTS.md

**Role:** Proxy Gateway Operator
**Mandate:** Deploy and maintain the LiteLLM proxy gateway for Claude Code.

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

## Provider Configuration

### LiteLLM — `litellm/config.yaml`

| Model ID           | Backend                        | Timeout | Keys                                                |
| :----------------- | :----------------------------- | :------ | :-------------------------------------------------- |
| `nemotron-3-ultra` | opencode/nemotron-3-ultra-free | 300s    | OPENCODE_API_KEY                                    |
| `deepseek-v4-flash` | opencode/deepseek-v4-flash-free | 300s    | OPENCODE_API_KEY                                    |
| `gemma-4-31b`      | gemini/gemma-4-31b-it          | 120s    | GEMINI_API_KEY                                      |

**Request format:** Use the Model ID directly (e.g. `gemma-4-31b`, `nemotron-3-ultra`).

**Features:**

- `drop_params: true` — drops unsupported params for compatibility.
- `use_chat_completions_url_for_anthropic_messages: true` — routes upstream via `/v1/chat/completions` for all providers.
- `num_retries: 0` — no auto-retry.
- `routing_strategy: simple-shuffle` — random distribution across duplicate keys.

---

## .profile Values

| Variable                         | Value                             |
| -------------------------------- | --------------------------------- |
| `ANTHROPIC_BASE_URL`             | `http://localhost:4000`           |
| `ANTHROPIC_DEFAULT_OPUS_MODEL`   | `nemotron-3-ultra`                |
| `ANTHROPIC_DEFAULT_SONNET_MODEL` | `gemma-4-31b`                     |
| `ANTHROPIC_DEFAULT_HAIKU_MODEL`  | `deepseek-v4-flash`               |

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
