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

| Model ID           | Backend                                      | Timeout | Keys             |
| :----------------- | :------------------------------------------- | :------ | :--------------- |
| `nemotron-3-super` | nvidia_nim/nvidia/nemotron-3-super-120b-a12b | 300s    | NVIDIA_API_KEY   |
| `agnes-2.0-flash`  | openai/agnes-2.0-flash                       | 120s    | AGNES_API_KEY    |
| `mimo-v2.5`        | openai/mimo-v2.5-free                        | 120s    | OPENCODE_API_KEY |

**Request format:** Use the Model ID directly (e.g. `nemotron-3-super`, `agnes-2.0-flash`).

**Features:**

- `drop_params: true` — drops unsupported params for compatibility.
- `use_chat_completions_url_for_anthropic_messages: true` — routes upstream via `/v1/chat/completions` for all providers.
- `num_retries: 0` — no auto-retry.
- `routing_strategy: simple-shuffle` — random distribution across duplicate keys.

---

## .profile Values

| Variable                         | Value                   |
| -------------------------------- | ----------------------- |
| `ANTHROPIC_BASE_URL`             | `http://localhost:4000` |
| `ANTHROPIC_DEFAULT_OPUS_MODEL`   | `nemotron-3-super`      |
| `ANTHROPIC_DEFAULT_SONNET_MODEL` | `agnes-2.0-flash`       |
| `ANTHROPIC_DEFAULT_HAIKU_MODEL`  | `mimo-v2.5`             |

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
