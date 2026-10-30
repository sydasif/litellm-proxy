# MIT License - Copyright (c) 2026 Syed Asif
# See LICENSE file for details.
#
# This Dockerfile builds the LiteLLM image for the AI Proxy Gateway.
# LiteLLM base image is MIT-licensed (https://github.com/BerriAI/litellm).
# No model weights or proprietary code are included in this build.
FROM docker.litellm.ai/berriai/litellm:1.105.1

# Pinned to a specific release for reproducible builds. Bump this tag only
# together with re-reading CLAUDE.md -> "Image Pin, Streaming & Local Patches".
#
# HISTORY: litellm 1.92.0 shipped four defects in its Anthropic /v1/messages
# streaming adapter that broke Claude Code with "Content block is not a thinking
# block" when NVIDIA NIM / Nemotron was served with streaming + thinking on.
# Those four defects were fixed upstream as of 1.105.1:
#
# 1. first content block opened from the classified block type (#33252);
# 2. combined reasoning+content chunks split per payload kind (#34795);
# 3. text in combined chunks no longer silently dropped (#34795);
# 4. empty choices chunks skipped instead of raising IndexError.
#
# LOCAL PATCH (still needed at 1.105.1): a rejected or timed-out Gemini
# context-cache write used to raise, so a free-tier key (explicit-cache storage
# quota = 0) turned every Claude Code /v1/messages request carrying
# `cache_control` markers into a 429 that also cooled both Gemini deployments.
# A cache write is an optimization and now continues uncached. Remove this patch
# once upstream stops raising from ContextCachingEndpoints.check_and_create_cache.
COPY patches/ /app/patches/
RUN python3 /app/patches/fix_gemini_context_caching.py
RUN python3 /app/patches/verify_gemini_cache_fallback.py
