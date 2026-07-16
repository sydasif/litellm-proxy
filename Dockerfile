# MIT License - Copyright (c) 2026 Syed Asif
# See LICENSE file for details.
#
# This Dockerfile builds a patched LiteLLM image for the AI Proxy Gateway.
# LiteLLM base image is MIT-licensed (https://github.com/BerriAI/litellm).
# No model weights or proprietary code are included in this build.
FROM docker.litellm.ai/berriai/litellm-database:1.92.0

# Pinned to a specific release for reproducible builds. Bump this tag together
# with re-verifying the patch in patches/fix_nemotron_thinking_stream.py still
# applies (it prints SKIP lines if the target source strings changed).

# Fix "Content block is not a thinking block" when serving NVIDIA NIM /
# Nemotron 3 Ultra through LiteLLM's Anthropic /v1/messages adapter with
# streaming + thinking enabled (litellm 1.92.0).
#
# The patch rewrites the installed litellm adapter files at build time so the
# reasoning_content block-type detection is correct and a thinking_delta is
# never emitted into a block opened as text.
#
# RE-APPLY ON UPGRADE: if you bump the base image tag, verify the patch still
# applies (the patch script prints SKIP lines if the target source changed).
COPY patches/ /app/patches/

RUN python3 /app/patches/fix_nemotron_thinking_stream.py
