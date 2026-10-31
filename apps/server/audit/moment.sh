#!/usr/bin/env bash
# One audit moment end to end: strip times -> overlay strip -> ear -> picture.
#   audit/moment.sh <video id> <t_ms> <label=dump>[,<label=dump>...] [--real]
# The LAST label is the version the overlay strip shows. Run from anywhere on
# cnr-intel; needs the kashi-audit image (README) and, for --real, the
# Playwright image. Output: scratch/audit/runs/<id>/moment-<t>.{png,json}.
set -euo pipefail
ID=$1 T=$2 DOCS=$3 REAL=${4:-}
REPO=$(cd "$(dirname "$0")/../../.." && pwd)
SERVER=$REPO/apps/server
OUT=$REPO/scratch/audit/runs/$ID
mkdir -p "$OUT"
LAST=${DOCS##*,}
DOC=${LAST#*=}
case $DOC in /*) ;; *) DOC=$PWD/$DOC ;; esac
ABS_DOCS=$(python3 -c 'import os,sys;print(",".join(l+"="+os.path.abspath(p) for l,p in (x.split("=",1) for x in sys.argv[1].split(","))))' "$DOCS")
cd "$SERVER"
export PYTHONPATH="$SERVER/src:$SERVER"
TIMES=$(uv run -q --extra audit python -m audit.moment "$ID" "$T" --doc "$DOC")
node "$REPO/apps/overlay/scripts/audit-strip.mjs" --doc "$DOC" --id "$ID" --times "$TIMES" \
  --out "$OUT/strip-$T.json"
REAL_ARGS=()
if [ "$REAL" = "--real" ]; then
  docker run --rm --init --user "$(id -u):$(id -g)" --shm-size=1g -e HOME=/tmp \
    -v "$REPO:$REPO" -w "$REPO/apps/overlay" mcr.microsoft.com/playwright:v1.63.0-noble \
    node scripts/audit-strip.mjs --doc "$DOC" --id "$ID" --times "$TIMES" --mode real \
    --png-dir "$OUT/frames-$T" --out "$OUT/strip-real-$T.json"
  REAL_ARGS=(--real-dir "$OUT/frames-$T")
fi
if [ ! -f "$REPO/scratch/audit/ear/$ID/$T.json" ]; then
  docker run --rm --user "$(id -u):$(id -g)" -v "$REPO:/repo" -w /repo/apps/server \
    -e PYTHONPATH=/repo/apps/server/src:/repo/apps/server \
    -v "$SERVER/benchmarks/data/hf:/models" kashi-audit \
    python -m audit.ear "$ID" --doc "${DOC/#$REPO//repo}" --times "$T"
fi
uv run -q --extra audit python -m audit.render "$ID" "$T" --doc "$ABS_DOCS" \
  --strip "$OUT/strip-$T.json" "${REAL_ARGS[@]}"
