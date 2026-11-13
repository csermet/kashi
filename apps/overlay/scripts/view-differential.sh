#!/usr/bin/env bash
# The old-vs-new renderer differential (scripts/view-differential.mjs) inside
# the pinned Playwright image, like the golden harness. Builds outside the
# repository need their directories mounted, e.g.:
#   VIEW_DIFF_DOCKER_ARGS="-v /tmp/old:/tmp/old" \
#     scripts/view-differential.sh --old /tmp/old/renderer --new out/renderer
set -euo pipefail
IMAGE='mcr.microsoft.com/playwright:v1.63.0-noble@sha256:eff16c30e6f3f4af0a03fa4b706120d5e9b0891c344a27d64559aff5900a4a27'
HERE=$(cd "$(dirname "$0")" && pwd)
OVERLAY=$(dirname "$HERE")
REPO=$(cd "$OVERLAY/../.." && pwd)
REPORT=/tmp/kashi-view-diff
mkdir -p "$REPORT"
# shellcheck disable=SC2086 # VIEW_DIFF_DOCKER_ARGS is a list of docker flags
exec docker run --rm --init --user "$(id -u):$(id -g)" --shm-size=1g -e HOME=/tmp \
  -v "$REPO:$REPO" -v "$REPORT:$REPORT" ${VIEW_DIFF_DOCKER_ARGS:-} -w "$OVERLAY" "$IMAGE" \
  node scripts/view-differential.mjs "$@"
