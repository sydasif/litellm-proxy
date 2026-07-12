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

| Model ID              | Backend                                         | Timeout | Keys               |
| :-------------------- | :---------------------------------------------- | :------ | :----------------- |
| `gpt-oss-120b`        | nvidia_nim/openai/gpt-oss-120b                  | 120s    | NVIDIA_API_KEY_1/2 |
| `mimo-v2.5`           | openai/mimo-v2.5-free                           | 120s    | OPENCODE_API_KEY   |
| `nemotron-ultra-550b` | nvidia_nim/nvidia/nemotron-3-ultra-550b-a55b    | 120s    | NVIDIA_API_KEY_1/2 |
| `agnes-2.0-flash`     | openai/agnes-2.0-flash (fallback for mimo-v2.5) | 120s    | AGNES_API_KEY      |

**Request format:** Use the Model ID directly (e.g. `gpt-oss-120b`, `mimo-v2.5`, `nemotron-ultra-550b`).

**Features:**

- `drop_params: true` — drops unsupported params for compatibility.
- `use_chat_completions_url_for_anthropic_messages: true` — routes upstream via `/v1/chat/completions` for all providers.
- `router_settings.routing_strategy: simple-shuffle` — random distribution across the 2 NVIDIA key deployments (combined ~80 rpm).
- `router_settings.num_retries: 1` — retries failed calls (e.g. `429`) on the _other_ NVIDIA key.
- `router_settings.timeout: 120` — caps each request at 120s.
- `litellm_settings.fallbacks: [{"mimo-v2.5": ["agnes-2.0-flash"]}]` — Sonnet (`mimo-v2.5`) falls back to `agnes-2.0-flash` if it fails after retries.
- `nemotron-ultra-550b` has upstream reasoning **disabled** (`extra_body.chat_template_kwargs.enable_thinking: false`). Claude Code must NOT declare it thinking-capable — leave `ANTHROPIC_DEFAULT_SONNET_MODEL_SUPPORTED_CAPABILITIES` unset. `gpt-oss-120b`, `mimo-v2.5`, and `agnes-2.0-flash` keep reasoning enabled (`enable_thinking: true`). NIM's param is `enable_thinking`, **not** `thinking`.

---

## .profile Values

| Variable                         | Value                   |
| -------------------------------- | ----------------------- |
| `ANTHROPIC_BASE_URL`             | `http://localhost:4000` |
| `ANTHROPIC_DEFAULT_OPUS_MODEL`   | `nemotron-ultra-550b`   |
| `ANTHROPIC_DEFAULT_SONNET_MODEL` | `mimo-v2.5`             |
| `ANTHROPIC_DEFAULT_HAIKU_MODEL`  | `gpt-oss-120b`          |

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
