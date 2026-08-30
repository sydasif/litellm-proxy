# AI Proxy Gateway

<p align="center">
  <img src="logo.png" alt="LiteLLM Proxy Logo" width="200" height="200">
</p>

<p align="center">
  <a href="https://hub.docker.com/r/litellm/litellm"><img src="https://img.shields.io/badge/LiteLLM-blueviolet?style=for-the-badge" alt="LiteLLM"></a>
  <a href="https://claude.com/product/claude-code"><img src="https://img.shields.io/badge/Claude%20Code-D97757?style=for-the-badge&logo=anthropic&logoColor=white" alt="Claude Code"></a>
  <a href="https://build.nvidia.com/"><img src="https://img.shields.io/badge/NVIDIA%20NIM-76B900?style=for-the-badge&logo=nvidia&logoColor=white" alt="NVIDIA NIM"></a>
  <a href="https://opencode.ai"><img src="https://img.shields.io/badge/OpenCode%20Zen-6C47FF?style=for-the-badge" alt="OpenCode Zen"></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/Python-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python"></a>
  <a href="https://www.docker.com/"><img src="https://img.shields.io/badge/docker-%230db7ed.svg?style=for-the-badge&logo=docker&logoColor=white" alt="Docker"></a>
  <a href="https://docs.docker.com/compose/"><img src="https://img.shields.io/badge/docker%20compose-2496ED?style=for-the-badge&logo=docker&logoColor=white" alt="Docker Compose"></a>
</p>

An AI Proxy Gateway that routes **Claude Code** and other clients through **LiteLLM** to multiple AI backend providers (NVIDIA NIM, OpenCode Zen, SenseNova, Agnes AI, Google Gemini) with load balancing, ordered failover, and cascading fallbacks. Runs as a single instance with in-memory state (no database or UI).

---

## Features

- **Multi-Provider Routing**: Access OpenCode Zen, SenseNova, Agnes AI, NVIDIA NIM, and Google Gemini through unified virtual model names with ordered failover and shuffle load balancing.
- **Load Balancing**: `simple-shuffle` distributes requests across the NVIDIA NIM (haiku) and Gemini deployment pools.
- **Ordered Failover**: Opus and Sonnet pools run sequentially — the first-listed deployment handles all traffic until it fails, then traffic shifts to the second.
- **Cascading Fallbacks**: 
  - `claude-opus-5` → `claude-haiku-4-5-20251001`
  - `claude-sonnet-5` → `claude-haiku-4-5-20251001`
  - `claude-haiku-4-5-20251001` → `gemini-3.5`
- **Parameter Normalization**: Drops unsupported parameters (`drop_params: true`) for cross-provider compatibility.
- **Tool Compatibility**: Automatically strips `strict: null` from tool definitions (`additional_drop_params`) for sglang-based backends.
- **Weighted Failover**: Retries within a model's deployment pool before escalating to fallback models (`num_retries: 2`).
- **Request Resilience**: `request_timeout: 120` aborts hung upstream calls; `cooldown_time: 60` marks failing deployments unhealthy so traffic is rerouted fast.
- **Lean Health Check**: Container liveness uses a stdlib `urllib` probe (no `curl`/`requests` dependency), with a 30s start period.
- **Resource-Tuned Container**: Pinned to 1.5 CPUs / 2 GB RAM (proxy is I/O-bound) with 10 MB × 3 log rotation.
- **Patched Streaming Image**: Builds a custom LiteLLM image (`litellm-proxy:patched`) fixing upstream thinking-stream adapter bugs and empty-choices crashes.
- **Single-Instance Design**: No external database, Redis, or UI required — runs entirely via API.
- **Health Checks**: Liveness (`/health/liveliness`) and readiness (`/health/readiness`) endpoints.
- **Lean Build Context**: `.dockerignore` excludes `.env`, `.git/`, docs, and caches from the Docker build context.

---

## Architecture & Model Mapping

| Virtual Model Alias | Backend Deployments | Routing & Limits |
| :--- | :--- | :--- |
| `claude-opus-5` | • `openai/mimo-v2.5-free` (OpenCode Zen, 1st)<br>• `openai/hy3-free` (OpenCode Zen, 2nd) | Ordered failover: 1st deployment until it fails, then 2nd |
| `claude-sonnet-5` | • `openai/hy3-free` (OpenCode Zen, 1st)<br>• `openai/sensenova-6.8-flash-lite` (SenseNova, 2nd)<br>• `openai/agnes-2.5-flash` (Agnes AI, 3rd) | Ordered failover: 1st deployment until it fails, then 2nd |
| `claude-haiku-4-5-20251001` | • `nvidia_nim/nvidia/nemotron-3.5-lightning-30b-a3b` (Key 1)<br>• `nvidia_nim/nvidia/nemotron-3.5-lightning-30b-a3b` (Key 2) | Load-balanced (`simple-shuffle`) across both keys |
| `gemini-3.5` | • `gemini/gemini-3.5-flash-lite` (Key 1)<br>• `gemini/gemini-3.5-flash-lite` (Key 2) | Load-balanced; declarative limit of 15 RPM / 240K TPM per deployment |
| `gemini-3.1` | • `gemini/gemini-3.1-flash-lite` (Key 1)<br>• `gemini/gemini-3.1-flash-lite` (Key 2) | Load-balanced; declarative limit of 15 RPM / 240K TPM per deployment |

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
   *(Be sure to edit `.env` and add your `OPENCODE_API_KEY`, `SENSENOVA_API_KEY`, `AGNES_API_KEY`, `NVIDIA_API_KEY_1`, `NVIDIA_API_KEY_2`, `GEMINI_API_KEY_1`, and `GEMINI_API_KEY_2`)*

3. **Start the proxy stack (builds patched image automatically):**
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
  -d '{"model": "claude-opus-5", "messages": [{"role": "user", "content": "Hello!"}], "max_tokens": 100}'
```

---

```bash
litellm-proxy/
├── docker-compose.yml          # Docker Compose configuration (single instance, no DB/Redis)
├── Dockerfile                  # Builds litellm-proxy:patched with SSE streaming fix
├── .dockerignore               # Keeps .env, .git, docs, caches out of the build context
├── patches/
│   └── fix_nemotron_thinking_stream.py  # LiteLLM SSE thinking/streaming adapter patch
├── litellm/
│   └── config.yaml             # Router and provider settings
├── .env.example                # Template for environment variables
└── README.md                   # Project documentation
```

---

## Maintenance

| Task | Command |
| :--- | :--- |
| Update API keys | Edit `.env` file, then restart |
| Modify routing/providers | Edit `litellm/config.yaml`, then restart |
| Restart proxy | `docker compose down && docker compose up -d` |
| Rebuild patched image | `docker compose build --no-cache litellm && docker compose up -d` |
| View logs | `docker compose logs -f` |

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
