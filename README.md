# AI Proxy Gateway

An AI Proxy Gateway that routes **Claude Code** and other clients through **LiteLLM** to multiple AI backend providers (NVIDIA NIM, Agnes AI, SenseNova, Google Gemini, AMD Radeon) with load balancing and cascading fallbacks. Runs as a **single-worker** instance (`--num_workers 1`, supervised by Gunicorn) so the router's cooldown/usage counters apply to every request — no Postgres/Redis required. Sized at 1 vCPU / 3 GB to leave room on the host for other services.

It is a **single-user** gateway: one Claude Code client, so there is no worker pool to size, no Redis, no admission control and no autoscaling. The point of the routing config here is that a request never hangs and always lands on a model that answers — not throughput.

Binds on `0.0.0.0:4000`; host network must isolate this port. Runs on the pinned `litellm 1.105.1` base image, whose Anthropic streaming adapter is verified at build time against the Nemotron thinking-stream defects that previously required a local source patch.

---

## Features

- **Multi-Provider Routing**: Access NVIDIA NIM, Agnes AI, SenseNova, Google Gemini, and AMD Radeon endpoints through unified virtual model names with cascading fallbacks and usage-based load balancing.
- **Load Balancing**: `simple-shuffle` (`routing_strategy` in `litellm/config.yaml`) routes to the least-utilized deployment per worker. Every Claude and Gemini alias holds two or more deployments — the two NVIDIA NIM aliases use two API keys each, `claude-sonnet-5` pairs Agnes AI and SenseNova with AMD Radeon's Qwen (three), and both Gemini aliases use two keys. `claude-fable-5-1` is the exception: a single AMD Radeon deployment with nothing to spread across.
- **Cascading Fallbacks**: every Claude alias follows the same ordered cascade —
  1. `gemini-3.5-flash-lite`
  2. `gemini-3.1-flash-lite`
- **AMD Radeon Model**: `claude-fable-5-1` exposes `Qwen3.8-Flash-Next` (AMD Radeon, `AMD_API_KEY`) as a single deployment — it cascades to Gemini on failure, unlike the old standalone `Qwen3.8` alias. AMD's `Qwen3.8-27B` is also a third `claude-sonnet-5` deployment, where it inherits the sonnet cascade.
- **Parameter Normalization**: Drops unsupported parameters (`drop_params: true`) for cross-provider compatibility.
- **Tool Compatibility**: Automatically strips `strict: null` from tool definitions (`additional_drop_params` on every Gemini deployment) for backends that reject non-boolean values.
- **Lean Health Check**: Container liveness uses a stdlib `urllib` probe (no `curl`/`requests` dependency), with a 180s start period (a cold start measured 129s) and 30s interval.
- **Resource-Tuned Container**: Pinned to 1 CPU / 3 GB RAM — measured at 1.05 GB idle and 1.40 GB under mixed traffic, since LiteLLM's 4 GB-per-worker floor is driven by its database engine and this deployment runs none — with 10 MB × 3 log rotation.
- **Gunicorn-Supervised Worker**: `--run_gunicorn`, because uvicorn's own multiprocess manager pings each child with a hard-coded 5s timeout and kills workers that are still importing LiteLLM (measured: a 2-worker uvicorn boot killed 14 workers and took 129s to first liveliness, against 29s under Gunicorn).
- **Build-Gated Streaming Image**: Builds `litellm-proxy:patched` on the pinned `litellm 1.105.1` base image, where the Nemotron thinking-stream defects that used to need a local source patch are fixed upstream. The build replays the failing chunk shape against the installed adapter and aborts if the behavior regresses.
- **Single-Worker Design**: One worker process (`--num_workers 1`) under Gunicorn. In-memory state (router cooldowns, usage counters, request spend) is shared by every request, which the multi-worker setup could not do without Redis. No external database, Redis, or UI required.
- **Health Checks**: Liveness (`/health/liveliness`) and readiness (`/health/readiness`) endpoints.
- **Lean Build Context**: `.dockerignore` excludes `.env`, `.env.*`, `.git/`, `.claude/`, all markdown (`*.md`), images (`logo.png`), docker compose files (`docker-compose*.yml`), and caches from the Docker build context.

---

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/) & Docker Compose

---

## Setup & Installation

1. **Clone and configure environment:**

   ```bash
   git clone <repository-url>
   cd litellm-proxy
   cp .env.example .env
   ```

2. **Generate master key and populate `.env` with your provider API keys:**

   ```bash
   cat >> .env <<EOF
   LITELLM_MASTER_KEY="sk-$(openssl rand -hex 24)"
   EOF
   ```

   _(Be sure to edit `.env` and add your `NVIDIA_API_KEY_1`, `NVIDIA_API_KEY_2`, `AGNES_API_KEY`, `SENSENOVA_API_KEY`, `GEMINI_API_KEY_1`, `GEMINI_API_KEY_2`, and `AMD_API_KEY`)_

3. **Start the proxy stack** (builds the patched image on first run — `up` alone reuses an existing image, so run `docker compose build --no-cache litellm` after a `Dockerfile`/base-image change):

   ```bash
   docker compose up -d
   ```

4. **Verify health:**

   ```bash
   curl http://localhost:4000/health/liveliness
   curl http://localhost:4000/health/readiness
   ```

---

## Usage & API Authentication

All requests to the proxy use your `LITELLM_MASTER_KEY` as a Bearer token.

### Connecting Claude Code

Configure environment variables in your shell profile (`~/.bashrc` or `~/.zshrc`):

```bash
export ANTHROPIC_AUTH_TOKEN=$LITELLM_MASTER_KEY
export ANTHROPIC_BASE_URL=http://localhost:4000
```

### Testing the Proxy

List available models:

```bash
curl -H "Authorization: Bearer $LITELLM_MASTER_KEY" http://localhost:4000/v1/models
```

Test a chat completion:

```bash
curl -X POST http://localhost:4000/v1/chat/completions \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model": "claude-opus-5-5", "messages": [{"role": "user", "content": "Hello!"}], "max_tokens": 100}'
```

---

## Project Structure

```
litellm-proxy/
├── docker-compose.yml          # Docker Compose configuration (1 gunicorn worker, no DB/Redis; binds 0.0.0.0:4000)
├── Dockerfile                  # Builds litellm-proxy:patched with SSE streaming fix
├── .dockerignore               # Keeps .env, .git, docs, caches out of the build context
├── patches/
│   ├── verify_upstream_anthropic_streaming.py  # Build gate: replays the Nemotron stream shape
│   └── fix_nemotron_thinking_stream.py         # Legacy source patch (<=1.92.x layout, not applied)
├── litellm/
│   └── config.yaml             # Router and provider settings
├── .env.example                # Template for environment variables
└── README.md                   # Project documentation
```

---

## Maintenance

| Task                     | Command                                                           |
| :----------------------- | :---------------------------------------------------------------- |
| Update API keys          | Edit `.env` file, then restart                                    |
| Modify routing/providers | Edit `litellm/config.yaml`, then restart                          |
| Restart proxy            | `docker compose down && docker compose up -d`                     |
| Rebuild image            | `docker compose build --no-cache litellm && docker compose up -d` |
| Run the streaming gate   | `python3 patches/verify_upstream_anthropic_streaming.py`          |
| View logs                | `docker compose logs -f`                                          |

---

## Security

- API keys are stored exclusively in environment variables (`.env` file).
- `.env` file is gitignored to prevent accidental exposure of secrets.
- `.dockerignore` keeps `.env` and `.git/` out of the Docker build context, so secrets are never shipped to the Docker daemon.

---

## Contributing

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'feat(proxy): add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

---

## License

MIT License — see [LICENSE](LICENSE) for details.
