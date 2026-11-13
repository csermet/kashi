#!/usr/bin/env bash
# Golden frames + dwell (scripts/golden-frames.mjs) inside the pinned Playwright
# image: its Chromium and its fonts are what the golden pictures were taken
# with. Needs a built renderer first (pnpm --filter kashi-overlay build, or
# electron-vite build); arguments pass through, e.g. --update or --only <run>.
# A renderer built outside the repository needs its directory mounted:
#   GOLDEN_DOCKER_ARGS="-v /tmp/b:/tmp/b" scripts/golden-frames.sh --renderer /tmp/b/renderer
set -euo pipefail
IMAGE='mcr.microsoft.com/playwright:v1.63.0-noble@sha256:eff16c30e6f3f4af0a03fa4b706120d5e9b0891c344a27d64559aff5900a4a27'
HERE=$(cd "$(dirname "$0")" && pwd)
OVERLAY=$(dirname "$HERE")
REPO=$(cd "$OVERLAY/../.." && pwd)
# shellcheck disable=SC2086 # GOLDEN_DOCKER_ARGS is a list of docker flags
exec docker run --rm --init --user "$(id -u):$(id -g)" --shm-size=1g -e HOME=/tmp \
  -v "$REPO:$REPO" ${GOLDEN_DOCKER_ARGS:-} -w "$OVERLAY" "$IMAGE" \
  node scripts/golden-frames.mjs "$@"
