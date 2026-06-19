# AGENTS.md

**Role:** Proxy Gateway Operator
**Mandate:** Deploy and maintain the Bifrost proxy gateway for Claude Code (LiteLLM as backup).

---

## Commands

```bash
docker compose up -d                          # Start (detached)
docker compose down                            # Stop
docker compose logs -f                        # Tail logs
docker compose restart                        # Restart services
curl http://localhost:4000/health             # Health check
docker compose pull && docker compose up -d   # Update image + restart
```

---

## Architecture

Single Docker service (Bifrost, Go). Two config files:

- `bifrost/config.json` — providers, rate limits, governance (source of truth)
- `litellm/config.yaml` — LiteLLM backup config

No Python code lives in this repo.

---

## Setup Gotchas

- **DNS fix required**: `dns: [8.8.8.8, 8.8.4.4]` in docker-compose.yml — without it Bifrost can't resolve provider APIs.
- **Three API keys** needed (set in `.env`): `OPENCODE_API_KEY`, `GEMINI_API_KEY_1`, `GEMINI_API_KEY_2`, `AGNES_API_KEY`.
- **Config is mounted read-only** (`:ro`). Edit `bifrost/config.json`, then `docker compose down && docker compose up -d` to apply (full recreate, not restart — config is loaded at startup).
- **OpenCode Zen base_url** must be `https://opencode.ai/zen` (not `/zen/v1`) — Bifrost appends `/v1/chat/completions` internally.
- **Rate limits** are in the `governance` section of `bifrost/config.json`.
- **Health check** is TCP socket (`nc -z`) — no API calls, no quota burn.

---

## Provider Configuration

Read `bifrost/config.json` for the full config. Key details:

| Provider | RPM | Models                        |
| :------- | :-: | :---------------------------- |
| opencode | 30  | `north-mini-code-free`, `nemotron-3-ultra-free`, `deepseek-v4-flash-free` |
| gemini   | 15  | `gemma-4-31b-it`              |
| agnes    | 60  | `agnes-2.0-flash`             |

Request format: `<provider>/<model>` (e.g. `opencode/mimo-v2.5-free`, `agnes/agnes-2.0-flash`).

---

## Failure Handling

| Symptom                   | Fix                                                                        |
| ------------------------- | -------------------------------------------------------------------------- |
| `address already in use`  | `docker ps` — check for leftover containers on port 4000                   |
| `401` / `Invalid API key` | Verify the required key is set in `.env`                                   |
| `503` on `/health`        | `docker compose logs -f` — check Bifrost startup                           |
| `model not found`         | Update model names in `bifrost/config.json`                                |
| Config not applied        | Use `docker compose down && up -d` (not restart) — config loads at startup |

---

## Switching to LiteLLM (Backup)

```bash
mv docker-compose.yml docker-compose.bifrost.yml
mv docker-compose.litellm.yml docker-compose.yml
docker compose down && docker compose up -d
```

Switch back:

```bash
mv docker-compose.yml docker-compose.litellm.yml
mv docker-compose.bifrost.yml docker-compose.yml
docker compose down && docker compose up -d
```

---

## Stop & Ask

Escalate if: (1) backend API quota exceeded (`429`), (2) Docker daemon unavailable, (3) Claude Code still can't see the model after config change.
