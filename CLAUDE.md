# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AI Proxy Gateway that routes Claude Code through LiteLLM to multiple AI backend providers (NVIDIA NIM, OpenCode Zen, Agnes AI) with load balancing and parameter normalization. Uses Docker Compose to deploy the official LiteLLM image.

## Project Structure

```
litellm-proxy/
├── docker-compose.yml          # Docker Compose configuration
├── .env                        # API keys (gitignored)
├── .env.example                # Template for environment variables
├── .gitignore
├── README.md                   # Project overview and setup
└── litellm/
    └── config.yaml             # LiteLLM provider configuration
```

## Development Commands

### Environment Setup

```bash
# Copy environment template and configure API keys
cp .env.example .env
# Edit .env with your API keys:
#   NVIDIA_API_KEY_1, NVIDIA_API_KEY_2, OPENCODE_API_KEY, AGNES_API_KEY

# Start the proxy service
docker compose up -d

# Stop the service
docker compose down

# View logs
docker compose logs -f
```

### Configuration

Modify `litellm/config.yaml` to adjust providers, load balancing, fallbacks, or parameter normalization. Key settings:

- Load balancing: `simple-shuffle` across NVIDIA API keys
- Fallbacks: `mimo-v2.5` → `agnes-2.0-flash`
- Parameter normalization: `drop_params: true`
- Anthropic routing: `use_chat_completions_url_for_anthropic_messages: true`

After config changes: `docker compose restart`

### Testing

Test the running proxy (localhost:4000):

```bash
curl -X POST http://localhost:4000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "nemotron-3-super",
    "messages": [{"role": "user", "content": "Hello!"}],
    "max_tokens": 100
  }'
```

## Maintenance

- Update API keys in `.env` as needed
- Modify `litellm/config.yaml` for provider/routing changes
- Update LiteLLM image: `docker compose pull && docker compose up -d`
- Monitor logs: `docker compose logs -f`

## Security

- API keys stored exclusively in `.env` (gitignored)
- No secrets in codebase or configuration files
