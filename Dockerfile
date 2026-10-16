# MIT License - Copyright (c) 2026 Syed Asif
# See LICENSE file for details.
#
# This Dockerfile builds the LiteLLM image for the AI Proxy Gateway.
# LiteLLM base image is MIT-licensed (https://github.com/BerriAI/litellm).
# No model weights or proprietary code are included in this build.
FROM docker.litellm.ai/berriai/litellm:1.105.1

# Pinned to a specific release for reproducible builds. Bump this tag only
# together with re-running the verification step below (it exits non-zero on
# drift) and re-reading CLAUDE.md -> "Patched Image".
#
# HISTORY: litellm 1.92.0 shipped four defects in its Anthropic /v1/messages
# streaming adapter that broke Claude Code with "Content block is not a thinking
# block" when NVIDIA NIM / Nemotron was served with streaming + thinking on.
# This image used to rewrite the installed adapter sources at build time to fix
# them (patches/fix_nemotron_thinking_stream.py, kept for anyone who pins back
# to <= 1.92.x).
#
# As of 1.105.1 all four are fixed upstream — combined reasoning+content chunks
# are split per payload kind (BerriAI/litellm#34795), the first content block is
# opened from the classified block type (BerriAI/litellm#33252), and chunks with
# an empty choices list are skipped instead of indexed — so the local patch is
# no longer applied.
COPY patches/ /app/patches/

# Gate the build on the behavior this image depends on: feed the installed
# adapter the exact Nemotron chunk shape that used to fail and abort if any of
# the four invariants regressed. A failing build here means the base image has
# to be re-verified and patches/fix_nemotron_thinking_stream.py re-targeted to
# the new adapter sources.
RUN python3 /app/patches/verify_upstream_anthropic_streaming.py
