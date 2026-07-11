# Alignment benchmarks (hizalama-v2 P1)

Manual harness measuring word/line timing accuracy of the alignment pipeline.
Runs on intel by hand; **never in CI, never in the image**. Committed outputs
live in `results/` — they are the evidence behind the separation-model default
(P2) and the windowed-alignment acceptance (P3): targets **word MAE < 0.2 s,
PCO@0.3 s > 90 %** on JamendoLyrics with separation + windowing.

## Datasets

- **JamendoLyrics MultiLang** (ICASSP 2023): 79 songs (en 20 / fr 19 / de 20 /
  es 20), human word-start annotations. Downloaded on first run (~390 MB,
  pinned commit) into `data/` (gitignored). Audio is Jamendo CC, mostly
  **NC/ND — never commit or bake it into an image**.
- **Kashi field cases** (`cases.yaml`, growing list): real YouTube audio vs
  lrclib synced line starts — the production line-QA view. Word ground truth
  yok; median-corrected line report + window pass/fail.

Metrics follow the official `Evaluate.py` conventions: word-start deviations,
per-song aggregation (MIREX style), PCO = fraction of onsets within tolerance
(0.3 s literature standard; we also report 0.1/0.2/0.5). No output delay is
added (stoller.cfg's 0.180 s was model-specific).

## Running (bench container)

The host has no C++ toolchain; run inside the image (same wheels, comparable
wall-clocks). From the repo root:

```bash
docker build -f apps/server/Dockerfile \
  --build-arg SERVER_EXTRAS="--extra align --extra separate --extra bench" \
  -t kashi-bench .

docker run --rm -v "$PWD/apps/server:/repo" -w /repo -e PYTHONPATH=/repo/src \
  -v kashi-models:/models --entrypoint python kashi-bench \
  -m benchmarks.run --dataset jamendo --separation full-mix --label baseline
```

`-v kashi-models:/models` reuses the compose worker's model volume (MMS
weights, separator checkpoints, yt-dlp EJS cache) — first-run downloads land
there and survive. Use the actual volume name from `docker volume ls`.

One invocation = one configuration. The matrix:

| flag | values |
|---|---|
| `--separation` | `full-mix` (baseline), `bs-roformer` (prod target), `htdemucs_ft`, `voc_ft` |
| `--mixback` | `0` / `0.15` (fraction of original mix folded back into the stem) |
| windowed | joins with P3 (`meta.windowed` is already in the report schema) |

Useful scoping flags: `--languages eng,spa`, `--limit 8`, `--songs <stem>...`,
`--dataset cases`.

## Wall-clock expectations (CPU)

Separation dominates: BS-RoFormer ~6–9× realtime on CPU (double-digit minutes
per song — quality-first decision, Caner 2026-07-11), htdemucs_ft similar
(default `shifts=2`), Voc_FT ~1–3 min/song. Stems are cached under
`data/stems/<config>/`, so re-sweeps (e.g. P3 windowed) only pay alignment.
Full-dataset sweeps are for `full-mix`; run separated configs on a
representative subset (`--limit`/`--languages`) and say so in the label.

## GPU sweeps (personal PC, full 79-song matrix)

CPU separation costs double-digit minutes per song; an RTX-class GPU does it
in seconds, so the PC runs the FULL matrix (quality numbers are
host-independent — prod wall-clock budgeting still comes from the ryzen/CPU
runs). One-time flow from the Windows checkout (plain cmd; each run is
~15-40 min on GPU and they queue if pasted together):

```bat
git pull
docker build -f apps/server/benchmarks/Dockerfile.gpu -t kashi-bench-gpu .

docker run --rm --gpus all --ipc=host -v "%cd%:/repo" -v kashi-bench-models:/models kashi-bench-gpu python -m benchmarks.run --dataset jamendo --separation full-mix --label pc-full-mix
docker run --rm --gpus all --ipc=host -v "%cd%:/repo" -v kashi-bench-models:/models kashi-bench-gpu python -m benchmarks.run --dataset jamendo --separation bs-roformer --mixback 0.15 --label pc-bs-roformer-mb0.15
docker run --rm --gpus all --ipc=host -v "%cd%:/repo" -v kashi-bench-models:/models kashi-bench-gpu python -m benchmarks.run --dataset jamendo --separation bs-roformer --mixback 0 --label pc-bs-roformer-mb0
docker run --rm --gpus all --ipc=host -v "%cd%:/repo" -v kashi-bench-models:/models kashi-bench-gpu python -m benchmarks.run --dataset jamendo --separation voc_ft --mixback 0.15 --label pc-voc-ft-mb0.15
docker run --rm --gpus all --ipc=host -v "%cd%:/repo" -v kashi-bench-models:/models kashi-bench-gpu python -m benchmarks.run --dataset jamendo --separation voc_ft --mixback 0 --label pc-voc-ft-mb0
docker run --rm --gpus all --ipc=host -v "%cd%:/repo" -v kashi-bench-models:/models kashi-bench-gpu python -m benchmarks.run --dataset jamendo --separation htdemucs_ft --mixback 0.15 --label pc-htdemucs-mb0.15

git add apps/server/benchmarks/results
git commit -m "bench: GPU sweep results (RTX 5070 Ti)"
git push
```

(PowerShell kullanıyorsan `%cd%` yerine `${PWD}` yaz.)

Notes: `pc-full-mix` doubles as a GPU-vs-CPU parity check against the intel
baseline. Voc_FT's MDX arch runs on CPU inside this image on purpose
(onnxruntime-gpu's Blackwell support is unconfirmed) — on a 9700X that is
still fast. If `nvidia-smi` works in a container but torch reports no CUDA,
the known WSL2 culprit is the driver dir mount (see Dockerfile.gpu header).

## Results

`results/YYYY-MM-DD-<label>.json`: `meta` (config, versions, host), per-song
rows (MAE/MedAE/p95/PCO, align/sep seconds, sync degradations as errors) and
the aggregate (per-song means/medians, per-language split, ×realtime ratios).
Commit them; never overwrite an old result — new run, new date/label.
