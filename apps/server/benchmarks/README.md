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

### Aligner bake-off (Faz 8 P-B1/A2)

`--align-model` selects the checkpoint, so comparing two aligners is a flag
rather than a code change. This is not optional curiosity: the default weights
(`MahmoudAshraf/mms-300m-1130-forced-aligner`, and `facebook/mms-300m` under
it) are **CC-BY-NC-4.0** and cannot ship in a paid product, so a replacement
has to be measurable against the incumbent on the same 79 songs.

```bash
# incumbent
python -m benchmarks.run --dataset jamendo --separation kim-melband --windowed \
  --label mms-baseline
# any other CTC checkpoint
python -m benchmarks.run --dataset jamendo --separation kim-melband --windowed \
  --align-model <hf-id> --label <name>
```

`meta.alignment_model` records the model that actually ran, so two result
files can never claim the same aligner.

**Scope of the flag.** `load_alignment_model` is
`AutoModelForCTC.from_pretrained`, so this swaps **CTC checkpoints** —
wav2vec2/MMS-family — and nothing else. It is not an architecture switch.

Two consequences worth stating plainly:

- **Qwen3-ForcedAligner-0.6B cannot be compared this way.** It is a Qwen3
  slot-filling aligner behind its own `qwen-asr` package, not a CTC head.
  Putting it against the incumbent needs a second align BACKEND, which is
  real work and not this flag. (Its own caveats stand for when that happens:
  80 ms output bins, ±40 ms against our 191 ms MAE, and no Turkish in its
  language list.)
- **The commercially-clean path that IS in scope** is a permissively licensed
  CTC checkpoint. That is the search worth running before building any
  adapter, because it would drop straight in.

### Cross-model word disagreement (Faz 8.1)

The debt behind the arbiter's planned third signal. MMS and Qwen3-FA correlate
**+0.483 per song** — architectural diversity, on paper. But a song-level
number cannot say whether the two are wrong on the same WORDS (signal goes
quiet exactly when it is needed) or on different ones (real evidence of
doubt). Nothing is built on it until that is measured.

```bash
# same songs, same stems, same anchors — four runs, ~30 min on the 5070 Ti
python -m benchmarks.run --dataset jamendo --languages eng --separation kim-melband \
  --windowed --anchor-jitter-ms 400 --dump-words --label wd-mms-j400
python -m benchmarks.qwen_probe --languages eng --separation kim-melband \
  --windowed --anchor-jitter-ms 400 --dump-words --label wd-qwen-j400
# ...and the same pair at --anchor-jitter-ms 0 (see "the trap" below)

python -m benchmarks.word_disagreement \
  --mms results/<date>-wd-mms-j400.json --qwen results/<date>-wd-qwen-j400.json
```

**The trap the tool refuses at.** The two result files that already exist are
*not* comparable: the MMS sweep ran at 400 ms anchor jitter, the first Qwen
probe at 0 (ground-truth anchors, deliberately — it was measuring a ceiling).
Word-level comparison needs both models on one window plan, or a difference
between them is partly one of them having been handed better anchors.
`word_disagreement` checks `anchor_jitter_ms` and refuses on a mismatch.
Running the pair at BOTH 0 and 400 also separates the models' disagreement
from the shared error a bad anchor injects into both.

The verdict is pre-registered in the tool's constants (approved 2026-08-09,
before the first run): P1 independence, P2 diagnostic power, P3 false-alarm
ceiling. They are not to be tuned to a result — the exit code is 0 only when
all three pass, and the printed verdict says what to do in each case.

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
