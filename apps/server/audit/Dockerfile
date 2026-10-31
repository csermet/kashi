# Perceptual audit image: a released server image plus the `audit` extra
# (matplotlib). The pipeline code is NOT taken from the image: the repo is
# mounted and PYTHONPATH points at its src/, so the audit always runs the
# checked-out code. Build from the repo root:
#
#   docker build -f apps/server/audit/Dockerfile -t kashi-audit .
#
# The base only has to carry the same locked dependencies as the checkout
# (the align + separate extras); `uv pip install` below leaves those alone
# and adds what the lock's audit extra needs.
ARG BASE=ghcr.io/csermet/kashi-server:0.32.0
FROM ${BASE}
COPY --from=ghcr.io/astral-sh/uv:0.11.27 /uv /usr/local/bin/uv
USER root
COPY apps/server/pyproject.toml apps/server/uv.lock /tmp/audit-lock/
RUN cd /tmp/audit-lock \
    && uv export --frozen --no-dev --no-hashes --no-emit-project --extra audit \
         -o requirements.txt \
    && uv pip install --python /app/.venv -r requirements.txt \
    && rm -rf /tmp/audit-lock /root/.cache
USER 1000
