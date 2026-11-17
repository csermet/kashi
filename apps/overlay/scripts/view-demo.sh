#!/usr/bin/env bash
# The shared view's browser demo (scripts/view-demo.mjs): built with the
# overlay's vite and checked in the pinned Playwright image's Chromium.
set -euo pipefail
IMAGE='mcr.microsoft.com/playwright:v1.63.0-noble@sha256:eff16c30e6f3f4af0a03fa4b706120d5e9b0891c344a27d64559aff5900a4a27'
HERE=$(cd "$(dirname "$0")" && pwd)
OVERLAY=$(dirname "$HERE")
REPO=$(cd "$OVERLAY/../.." && pwd)
exec docker run --rm --init --user "$(id -u):$(id -g)" --shm-size=1g -e HOME=/tmp \
  -v "$REPO:$REPO" ${VIEW_DEMO_DOCKER_ARGS:-} -w "$OVERLAY" "$IMAGE" \
  node scripts/view-demo.mjs "$@"
