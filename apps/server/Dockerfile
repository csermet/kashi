# Kashi server image — ONE image, two commands:
#   api:    uvicorn kashi_server.api.app:app --host 0.0.0.0 --port 8080
#   worker: python -m kashi_server.worker
#
# Build context is the REPO ROOT (the schema lives outside apps/server):
#   docker build -f apps/server/Dockerfile -t kashi-server .

FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
# git: ctc-forced-aligner is a git dependency.
# g++: it ships a C++ extension (forced_align_impl) and has no wheels.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git g++ \
    && rm -rf /var/lib/apt/lists/*
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1
COPY apps/server/pyproject.toml apps/server/uv.lock ./
# --extra align: torch + ctc-forced-aligner. Kept out of the base dependencies
# so day-to-day work (API tests, linting) needs no C++ toolchain.
RUN uv sync --frozen --no-dev --no-install-project --extra align

FROM python:3.12-slim
# Static ffmpeg/ffprobe: avoids the apt ffmpeg dependency tree, and this build
# carries librubberband (needed for Faz 4's nightcore time-stretch).
COPY --from=mwader/static-ffmpeg:8.1.2 /ffmpeg /ffprobe /usr/local/bin/
# Deno is yt-dlp's JS runtime (JS_RUNTIMES prefers it). glibc binary — fine on
# python:slim (Debian), would NOT work on Alpine/musl.
COPY --from=denoland/deno:bin-2.9.1 /deno /usr/local/bin/deno

RUN useradd -m -u 1000 kashi && mkdir -p /scratch /models && chown 1000:1000 /scratch /models
COPY --from=builder /app/.venv /app/.venv
COPY apps/server/src /app/src
COPY apps/server/alembic.ini /app/alembic.ini
COPY apps/server/alembic /app/alembic
COPY packages/schemas/processed-track.v1.schema.json /app/schemas/processed-track.v1.schema.json
# The worker refuses to start unless it can align this clip (warmup smoke).
COPY apps/server/tests/fixtures /app/fixtures

ENV PATH=/app/.venv/bin:$PATH \
    PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1 \
    KASHI_SCHEMA_PATH=/app/schemas/processed-track.v1.schema.json \
    HF_HOME=/models \
    DATA_DIR=/scratch \
    MODEL_CACHE_DIR=/models \
    XDG_CACHE_HOME=/models/.cache
# XDG_CACHE_HOME on the volume: yt-dlp keeps its EJS challenge-solver script
# there — fetched once, survives container restarts.

USER 1000
WORKDIR /app
EXPOSE 8080 9090
CMD ["uvicorn", "kashi_server.api.app:app", "--host", "0.0.0.0", "--port", "8080"]
