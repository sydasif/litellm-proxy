# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AI Proxy Gateway that routes **Claude Code** through **LiteLLM** to multiple AI backend providers (NVIDIA NIM, Agnes AI, SenseNova, Google Gemini, AMD Radeon) with load balancing and cascading fallbacks. Runs as a **single-worker** instance (`--num_workers 1`, supervised by Gunicorn via `--run_gunicorn`), so the router's cooldown/usage counters are shared by every request — no Postgres/Redis is needed to make them coherent. Sized to leave room on the host: 1 vCPU / 3 GB.

**Single-user by design.** One Claude Code client shares this gateway, so every scaling knob is deliberately absent: no Redis (cooldowns are already coherent in the one worker), no worker pool to size, no admission control, no autoscaling, and no per-deployment `rpm`/`tpm` keys (the current `config.yaml` sets none). Had any deployment set `rpm`/`tpm`, litellm would read and enforce them pre-call (the deployment's `tpm`/`rpm` feed the usage counters and the pre-call check that raises when a deployment is over its limit) — for a single user that effect is that a burst can trip one deployment's ceiling and become a retry or a fallback rather than a rejection. Capacity is a non-goal; the routing config earns its keep on **failover and provider-latency hygiene** — that the one user's request never hangs and always ends on a model that answers. Tuning decisions here should be judged on the user's own perceived latency and on not wasting tokens on attempts that are going to be discarded, never on throughput.

Binds on `0.0.0.0:4000` (host network must isolate this port). Uses Docker Compose to deploy a pinned LiteLLM image (`1.105.1`) whose Anthropic streaming adapter is verified at build time against the Nemotron thinking-stream defects that previously required a local source patch.

## Architecture

**Model aliasing:** Virtual model names map to real provider models. Clients request the virtual name; LiteLLM routes across deployments per the pool's strategy below. Only the models listed in the table are currently wired — `claude-fable-5-1` and `gemini-3.1-flash-lite` are referenced in this file's prose for historical context but have no deployments in `config.yaml` and cannot be requested.

**Backend deployments:**

| Virtual Model           | Deployment 1                                                | Deployment 2                                                | Deployment 3 |
| ----------------------- | ----------------------------------------------------------- | ----------------------------------------------------------- | ------------ |
| `claude-opus-5-5`       | `nvidia_nim/nvidia/nemotron-3.5-super-vl-120b-a12b` (key 1) | `nvidia_nim/nvidia/nemotron-3.5-super-vl-120b-a12b` (key 2) | —            |
| `claude-sonnet-5`       | `openai/agnes-2.5-flash` (Agnes AI, `AGNES_API_KEY`)        | `openai/Qwen3.8-27B` (AMD Radeon, `AMD_API_KEY`)            | —            |
| `claude-haiku-5-5`      | `nvidia_nim/nvidia/nemotron-3.5-lightning-30b-a3b` (key 1)  | `nvidia_nim/nvidia/nemotron-3.5-lightning-30b-a3b` (key 2)  | —            |
| `gemini-3.5-flash-lite` | `gemini/gemini-3.5-flash-lite` (key 1)                      | `gemini/gemini-3.5-flash-lite` (key 2)                      | —            |

> **NIM's `nemotron-3-ultra-550b-a55b` never answers:** NIM lists it but the request is accepted and the socket then stays silent (0 bytes in 90s on a raw TLS connection; httpx `ReadTimeout` at 20s, 3/3; OpenAI SDK `APITimeoutError` at 30s), while the smaller nemotron models answer the same call in ~1s. Through this proxy that stall surfaced as an unbounded Claude Code hang: **700s with zero response bytes**, on both `/v1/messages` and `/v1/chat/completions`, streaming and non-streaming, and with **no error ever raised** — so no fallback could fire. No alias is on an ultra model now, but if you repoint one at one, test it first (see Testing).

**Fallback chain (when primary deployments fail):** the three Claude aliases share the same ordered cascade:

1. `gemini-3.5-flash-lite`
2. `sensenova-6.8-flash-lite`

**Full failover cascade:** `claude-opus-5-5 / claude-sonnet-5 / claude-haiku-5-5 → gemini-3.5-flash-lite → sensenova-6.8-flash-lite`

**Load balancing & failover:** `routing_strategy: simple-shuffle` routes to the least-utilized deployment; `num_retries: 2` adds per-call retries within a model's deployment pool before escalating to the fallback model; `cooldown_time: 50` marks a failing deployment unhealthy for 50s. `allowed_fails: 3` states LiteLLM's documented default explicitly (fails per minute before a deployment is cooled down for `cooldown_time`); there is no `retry_after` key. All Claude pools hold at least two deployments — `claude-opus-5-5` and `claude-haiku-5-5` each hold two NVIDIA keys, `claude-sonnet-5` holds two (Agnes AI and AMD Radeon's Qwen), and `gemini-3.5-flash-lite` holds two keys — so `simple-shuffle` spreads load across every deployment of each (verified against a live `litellm.Router` and against the running proxy: both keys of `claude-opus-5-5` and of `claude-haiku-5-5`, and more than one provider of `claude-sonnet-5`, are returned across successive picks).

> **`rpm` vs `order` placement (both used to sit at the top level of a deployment):** litellm promotes a top-level `rpm` (and `tpm`) into `litellm_params` at router init, so `rpm: 15` at deployment level works. It does **not** do that for `order`: `litellm.utils._get_deployment_order` reads `litellm_params.order` then `model_info.order` only, so a top-level `order` was silently inert and has been removed from the config. To hold a key as a hot standby instead of a load-balanced peer, put `order` **inside** `litellm_params` of that deployment.

> Note: the proxy runs a **single** worker, so a cooldown or usage decision applies to the whole gateway rather than to one process in two. If the worker count is ever raised (for throughput), that coherence is lost: cooldowns and usage counters become per-worker again unless Redis is added, roughly halving effective failover coverage under concurrent load.

**Resilience:** `request_timeout: 120` in `litellm_settings` is the only timeout in play — **no deployment currently sets a per-deployment `timeout`** (an older pair of `claude-opus-5` deployments used to tighten it to `timeout: 60` in `litellm_params`; that was dropped in commit `00be96f` and has not come back). Two things to keep in mind:

1. **The client waits out every attempt.** `router_settings.num_retries: 2` means up to **3 attempts at the effective timeout** before the fallback chain is even reached. litellm disables the OpenAI SDK's own retries on router calls — `main.py` sets `max_retries = 0` as soon as it sees a `model_group` in the request metadata, so the SDK layer does not multiply the wait. Worst-case silence is therefore ≈ `(num_retries + 1) × timeout + fallback latency`; with the 120s global and no per-deployment cap that is **~6 minutes**. Re-adding a per-deployment `timeout` is the lever if a pool starts hanging — the cap binds linearly.
2. **`fallbacks` only fire on an error.** A provider that stalls without ever erroring is never rescued — the 700s zero-byte hang described in the Architecture note happened with no error to trigger the cascade. A per-deployment `timeout` converts a stall into a fast, fallback-able failure; without one, only the global cap eventually ends the wait.

## Image pin & streaming verification (Nemotron thinking-stream)

The image is pinned to `docker.litellm.ai/berriai/litellm:1.105.1`. **The four defects below are fixed upstream as of this pin, so no local patch is applied.** The build still gates on the behavior: `Dockerfile` runs `patches/verify_upstream_anthropic_streaming.py`, which drives the installed adapter with the exact Nemotron chunk shape that used to break Claude Code and exits non-zero (failing the build) if any invariant regressed.

The four defects that litellm 1.92.0 had, and where they were fixed:

| #   | Defect (1.92.0)                                                                                                                                                                                                | Fixed upstream by                                                            |
| --- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------- |
| 1   | First content block hardcoded as `{"type": "text"}` before any upstream chunk was read, so a stream leading with reasoning opened a phantom empty text block                                                   | #33252 — block opened from the classified type                               |
| 2   | A chunk carrying both `reasoning_content` and `content` opened a `text` block (content checked first) while the emitted delta was a `thinking_delta` → Claude Code's `"Content block is not a thinking block"` | #34795 — combined chunks split per payload kind (thinking → text → tool_use) |
| 3   | The text in such a chunk was silently dropped                                                                                                                                                                  | #34795 (same split)                                                          |
| 4   | `_should_start_new_content_block` indexed `chunk.choices[0]` unguarded → `IndexError` on an empty upstream chunk                                                                                               | choiceless chunks now skipped via `_handle_choiceless_chunk`                 |

Until the 1.105.1 bump, defects 1–4 were rewritten into the installed adapter sources at build time by `patches/fix_nemotron_thinking_stream.py`. That script is **kept** (it still applies to the `llms/anthropic/experimental_pass_through/adapters` layout used by ≤1.92.x) but is **no longer run**, and its anchor strings are version-specific.

**Upstream layout note:** the adapter moved from `litellm/llms/anthropic/experimental_pass_through/adapters/` to `litellm/llms/anthropic/pass_through/adapters/` in the 1.10x line. Both the verification script and any future re-target of the patch have to account for that.

**On a base-image bump:**

```bash
# 1. Bump the FROM tag in Dockerfile, then rebuild (the verify step fails the build on drift)
docker compose build --no-cache litellm && docker compose up -d
# 2. Check the verify step passed and no SKIP/FAIL lines appeared
# 3. Re-verify the live stream with the raw SSE capture below
```

A failing verify step means the pin reintroduced one of the four defects: re-target `patches/fix_nemotron_thinking_stream.py` to the new adapter sources, apply it in the Dockerfile before the verify step, and re-run the capture.

**To verify the fix (raw SSE capture):**

```bash
curl -sN 'http://localhost:4000/v1/messages?beta=true' \
  -H "Content-Type: application/json" \
  -H "x-api-key: $LITELLM_MASTER_KEY" \
  -H "anthropic-version: 2023-06-01" \
  -d '{
    "model": "claude-opus-5-5",
    "max_tokens": 512,
    "stream": true,
    "messages": [{"role": "user", "content": "Hi"}]
  }'
```

Expected: First `content_block_start` opens as `thinking` (not a phantom empty text block), `thinking_delta`s stay in the thinking block, `text_delta`s in the text block. No `thinking_delta` in text blocks. No empty-choices crashes. Clean streaming.

## Development Commands

```bash
# Start the proxy (builds the image on first run)
docker compose up -d

# Stop the proxy
docker compose down

# Rebuild the image after config/base-image change
docker compose build --no-cache litellm && docker compose up -d

# Restart after config changes (no rebuild needed)
docker compose down && docker compose up -d

# View logs
docker compose logs -f

# Run the streaming verification gate locally (needs litellm importable)
python3 patches/verify_upstream_anthropic_streaming.py

# Validate config syntax before restart
python -c "import yaml; yaml.safe_load(open('litellm/config.yaml'))"
```

## Router Configuration (`litellm/config.yaml`)

Source of truth for routing and provider settings. Key behaviors:

**`litellm_settings`:** `drop_params: true`, `use_chat_completions_url_for_anthropic_messages: true`, `request_timeout: 120` (aborts hung upstream requests). No Redis.

**`router_settings`:** `routing_strategy: simple-shuffle` (least-utilized deployment), `num_retries: 2` (per-call retries within a deployment pool before escalating), `cooldown_time: 50` (seconds a failed deployment stays unhealthy), `allowed_fails: 3` (fails per minute before that cooldown applies), and `fallbacks` (identical two-model cascade for the Claude aliases). No Redis, and no `retry_after` key.

> **`fallbacks` placement — keep it under `router_settings`.** `proxy_server.load_config()` builds the router from the **`router_settings` block only**: it iterates `config["router_settings"]` and forwards the keys that are valid `litellm.Router` args. `fallbacks` is such an arg, so a _top-level_ `fallbacks:` parses fine, passes YAML validation, and is then **silently dropped** — the router gets `fallbacks=None` and every upstream failure reaches the client as "... failed with the error above. No fallback was attempted." Verified by reproducing the loader's router assembly: top-level → `router.fallbacks is None`; nested → `claude-opus-5-5` resolves to `['gemini-3.5-flash-lite', 'sensenova-6.8-flash-lite']`. This is the same failure mode as the top-level `order` keys in the note above.

> **`rpm`/`tpm` keys are not set on any deployment** in the current config. The per-deployment `rpm`/`tpm` mechanism exists in litellm (top-level `rpm` on a deployment is promoted into `litellm_params`) but no key uses it here. **No `order` keys** either — a top-level `order` is silently ignored by litellm.

**Per-deployment extra params:** the two `claude-sonnet-5` deployments (Agnes AI and AMD Radeon's Qwen) set `use_chat_completions_api: true` with an explicit `api_base` and their own keys (`AGNES_API_KEY`, `AMD_API_KEY`); the standalone `sensenova-6.8-flash-lite` fallback alias is configured the same way with `SENSENOVA_API_KEY`. No deployment currently sets a per-deployment `timeout` (see Resilience); the Gemini and NVIDIA NIM deployments rely on provider defaults.

**`additional_drop_params: ["tools[*].strict"]`** — Applied to all Gemini deployments. Strips `strict: null` from tool definitions before sending to backends that reject non-boolean values (fixes 400 validation errors from providers that apply strict type validation).

**`general_settings`:** `master_key` auth only. The `trusted_proxy_ranges: []` key (which silenced the login-throttle warning at startup) and the `background_health_checks: true` / `health_check_interval: 300` keys (which made `/health` serve a cached snapshot instead of probing every deployment per call) have been **removed from the config** — `/health` therefore probes per call again. No `database_url` — single-instance, no PostgreSQL. Virtual keys/budgets/spend reports are in-memory only.

## Testing

```bash
# Verify proxy is running
curl http://localhost:4000/v1/models

# Test chat completion
curl -X POST http://localhost:4000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -d '{"model": "claude-opus-5-5", "messages": [{"role": "user", "content": "Hello!"}], "max_tokens": 100}'

# Test tools carrying strict: null (NIM accepts it as-is; the Gemini fallback
# deployments strip it via additional_drop_params)
curl -X POST http://localhost:4000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -d '{
    "model": "claude-haiku-5-5",
    "messages": [{"role": "user", "content": "Hello"}],
    "max_tokens": 50,
    "tools": [{"type": "function", "function": {"name": "test", "strict": null, "parameters": {"type": "object"}}}]
  }'

# Check deployment IDs
curl -s http://localhost:4000/v1/model/info \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" | \
  python3 -c "import json,sys; [print(f'{m[\"model_name\"]:30s} {m[\"litellm_params\"].get(\"model\",\"?\"):40s}') for m in json.load(sys.stdin)['data']]"

# Check load balancing (different x-litellm-model-id headers across requests).
# Note: pools use simple-shuffle. `claude-opus-5-5`, `claude-haiku-5-5` and
# `gemini-3.5-flash-lite` each have two deployments and `claude-sonnet-5`
# has two, so all should vary.
# Counters live in the single worker process, so these picks are coordinated across the
# whole gateway rather than per process.
for i in {1..6}; do
  curl -s -D - http://0.0.0.0:4000/v1/chat/completions \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
    -d '{"model": "claude-sonnet-5", "messages": [{"role": "user", "content": "Hi"}], "max_tokens": 10}' | \
    grep "x-litellm-model-id:"
done
```

## Maintenance

- **Update API keys**: Edit `.env` file
- **Modify routing/providers**: Update `litellm/config.yaml`, then restart
- **Rebuild image**: `docker compose build --no-cache litellm && docker compose up -d` (after config or base-image change)
- **Bump the LiteLLM pin**: see "Image pin & streaming verification" — the verify step gates the build
- **Monitor logs**: `docker compose logs -f`
