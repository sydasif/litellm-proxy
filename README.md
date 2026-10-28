# AI Proxy Gateway

Routes **Claude Code** through **LiteLLM** to five providers (NVIDIA NIM, Agnes AI, SenseNova, Google Gemini, AMD Radeon) with load balancing and cascading fallbacks. Single Gunicorn worker, no Redis or Postgres. Binds to `127.0.0.1:4000`.

---

## Features

- Multi-provider virtual model routing with `simple-shuffle` load balancing
- Cascading fallbacks: `gemini-3.5-flash-lite` → `sensenova-6.8-flash-lite`; `sensenova-6.8-flash-lite` → `gemini-3.5-flash-lite`
- Anthropic `/v1/messages` → OpenAI `/chat/completions` translation
- `drop_params` + `additional_drop_params` for cross-provider parameter normalization
- Per-deployment timeouts (`timeout`, `stream_timeout`, `keepalive_seconds`) on every backend
- `request_timeout: 60` global cap; `num_retries: 1` for faster fallback escalation
- `http2: false` for outbound provider traffic — required to prevent HTTP/2 mid-stream connection drops on NVIDIA NIM
- Local response cache initialized (enabled but no call types cached — streaming SSE blocks get corrupted, disabled via `supported_call_types: []`)

---

## Model Mapping

| Virtual Model              | Deployment 1                                                       | Deployment 2                                                      |
| -------------------------- | ------------------------------------------------------------------ | ----------------------------------------------------------------- |
| `claude-opus-5-5`          | `nvidia_nim/nvidia/nemotron-3-ultra-550b-a55b` (NVIDIA key 1)      | `openai/Qwen3.8-27B` (AMD Radeon)                                 |
| `claude-sonnet-5`          | `nvidia_nim/nvidia/nemotron-3.5-super-vl-120b-a12b` (NVIDIA key 2) | `openai/agnes-2.5-flash` (Agnes AI)                               |
| `claude-haiku-5-5`         | `nvidia_nim/nvidia/nemotron-3.5-lightning-30b-a3b` (NVIDIA key 1)  | `nvidia_nim/nvidia/nemotron-3.5-lightning-30b-a3b` (NVIDIA key 2) |
| `gemini-3.5-flash-lite`    | `gemini/gemini-3.5-flash-lite` (key 1)                             | `gemini/gemini-3.5-flash-lite` (key 2)                            |
| `sensenova-6.8-flash-lite` | `openai/sensenova-6.8-flash-lite` (SenseNova)                      | —                                                                 |

> `nemotron-3-ultra-550b-a55b` answers in ~300-800ms with no stall observed — the historical 700s zero-byte hang no longer reproduces.

---

## Setup

```bash
git clone <repository-url> && cd litellm-proxy
cp .env.example .env
# Edit .env: fill in NVIDIA_API_KEY_1, NVIDIA_API_KEY_2, AGNES_API_KEY,
# SENSENOVA_API_KEY, GEMINI_API_KEY_1, GEMINI_API_KEY_2, AMD_API_KEY,
# and generate LITELLM_MASTER_KEY="sk-$(openssl rand -hex 24)"

docker compose up -d
curl http://localhost:4000/health/liveliness
```

---

## Usage

### Claude Code

```bash
export ANTHROPIC_AUTH_TOKEN=$LITELLM_MASTER_KEY
export ANTHROPIC_BASE_URL=http://localhost:4000
```

### curl

```bash
curl -H "Authorization: Bearer $LITELLM_MASTER_KEY" http://localhost:4000/v1/models
```

```bash
curl -X POST http://localhost:4000/v1/chat/completions \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model": "claude-opus-5-5", "messages": [{"role": "user", "content": "Hi"}], "max_tokens": 100}'
```

---

## Project Structure

```
litellm-proxy/
├── docker-compose.yml
├── Dockerfile
├── patches/
│   └── proxy_test.py                 # test harness — validates all endpoints and config
├── litellm/config.yaml
└── .env.example
```

---

## Maintenance

| Task                        | Command                                                           |
| --------------------------- | ----------------------------------------------------------------- |
| Restart after config change | `docker compose down && docker compose up -d`                     |
| Rebuild after image change  | `docker compose build --no-cache litellm && docker compose up -d` |
| View logs                   | `docker compose logs -f`                                          |

---

## License

MIT — see [LICENSE](LICENSE).
