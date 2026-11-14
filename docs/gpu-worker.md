# GPU helper worker (phase 9.3)

A second worker on a desktop with an NVIDIA GPU, pulling jobs from the same
Postgres queue as the server's own worker. On an RTX 5070 Ti a track takes
about a minute against ~12 on the CPU worker, so a full archive refresh (the
archive refresh policy in `docs/roadmap.md`) takes hours instead of days, and
first listens are faster while the desktop is on. When it is off nothing
changes: the CPU worker keeps the queue.

## What makes two workers safe

- **Claims** use `FOR UPDATE SKIP LOCKED`: two workers never take the same job.
- **Leases are owned by the claim.** Every claim bumps the job's `attempts`;
  heartbeats and stage checkpoints renew the lease only while that claim
  still owns the row. A worker that slept or lost its network for longer than
  the lease (`LEASE_TTL_S`, 600 s) finds the job reclaimed at its next
  checkpoint — there is one right before the document is written — and drops
  its run without touching the row (`lease lost` in its log). The other worker
  re-runs the job from the start.
- **Expiry is judged on the database clock**, the clock that wrote the lease,
  so a desktop whose clock runs fast cannot reclaim live jobs.
- **Same image tag, same pipeline.** Build the helper at the `server-v<version>`
  tag the cluster runs. Documents record the pipeline version that made them,
  and since 2.33.0 a stored document from a newer version is never replaced by
  an older worker, so a stale helper wastes its runs. Each worker logs
  `worker <name> ready: pipeline <version>, <configuration> …` at startup; both
  lines must show the same version, and since server 0.37.0 the configuration
  must read `code-default configuration`. `env overrides <fields> (not what the
  version names)` means an env value changes a pipeline setting: remove it.
- **Schema first.** Only the API deployment migrates the database (its
  initContainer). Since 0.37.0 a worker whose database is behind its code does
  not claim: before its ready line it logs `not claiming jobs: database schema
  behind this worker: expected Alembic revision <head>, found <revision> …` and
  checks again with backoff up to a minute. Start the helper after the API of
  the same release has rolled out (it would wait until then anyway).
- **Publishing stays on the server's worker**: set `LRCLIB_PUBLISH_ENABLED=false`
  on the helper.

## Build (on the GPU machine, repo root, at the server's release tag)

```bat
git fetch --tags
git checkout server-v<version>
docker build -f apps/server/Dockerfile --build-arg TORCH_CUDA=cu130 -t kashi-worker-gpu .
```

`TORCH_CUDA` swaps the CPU torch for CUDA builds (the pins
`benchmarks/Dockerfile.gpu` verified on Blackwell). The image is never pushed.

## Environment

Since server 0.37.0 (pipeline 2.33.0) the production pipeline is the code
default — the aligner table with its offsets, the separator, the structure
sections, the pinned aligner revisions — so the helper needs **no pipeline
settings**. Do not copy `ALIGN_MODELS`, `SEPARATION_MODEL_FILENAME` or
`STRUCTURE_SECTIONS` any more; an older env file that still carries them works
as long as their values equal the defaults (the ready line then still reads
`code-default configuration`), but delete them. A helper built at a tag before
`server-v0.37.0` still needs them, copied verbatim from the server worker.
A `kashi-worker.env` file (keep it out of git):

```ini
DATABASE_URL=postgresql+psycopg://<user>:<password>@<host>:<port>/kashi
WORKER_NAME=gpu-desktop
KASHI_ALIGN_DEVICE=cuda
LRCLIB_PUBLISH_ENABLED=false
LRCLIB_FALLBACK_BASE_URL=<the lrclib mirror, as the desktop reaches it>
```

The database has to be reachable from the desktop; a `kubectl port-forward` to
the Postgres service is enough (from Docker Desktop the container reaches the
host as `host.docker.internal`). If that route drops, the worker waits with
backoff and the lease runs out like any lost worker's.

## Run

```bat
docker run --rm --gpus all --ipc=host --env-file kashi-worker.env -v kashi-bench-models:/models -v kashi-worker-scratch:/scratch kashi-worker-gpu python -m kashi_server.worker
```

The models volume can be the benchmark one (same Hugging Face cache layout);
missing models download on the first start. The worker refuses jobs until its
warmup alignment passes (`loading alignment model … (cuda)` in the log).
Ctrl+C finishes the current job, then exits.

## Checks after the first job

0. At start: `worker gpu-desktop ready: pipeline <the server's version>,
   code-default configuration …`, and the aligner load lines name a 40-hex
   commit (`loading alignment model <checkpoint> @ <commit> (cuda)`), never
   `UNPINNED`.
1. `claimed job … attempt N on gpu-desktop`, then `job … completed` about a
   minute later.
2. The document's `pipeline_version` equals the server's.
3. Word timings agree with a CPU-processed copy of the same track to within a
   frame or two (GPU and CPU kernels differ in the last digits).
