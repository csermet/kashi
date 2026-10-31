# Perceptual audit

Pictures and numbers of a lyrics document against its own audio, made for the
agent to read: Claude cannot hear, so each suspicious moment becomes one image
(separated-vocal spectrogram, what the aligner's model hears, the word boxes,
what the overlay shows at that instant) plus a JSON of the same quantities.
The owner only listens where the agent is unsure. Why it exists:
`docs/research/faz-9-pencere-saati-2026-10.md`, "Field finding".

Manual tooling, like `benchmarks/`: never in CI, never in the shipped image.

## Data rules

Everything lives under `<repo>/scratch/audit/` (git-excluded; override with
`KASHI_AUDIT_ROOT`). **Nothing from there is committed**: stems are separated
copyrighted audio, and every picture and moment file carries lyric text.

- `stems/<id>.wav` — the separator's output, kept for the duration of the
  project and deleted at its close. `<id>.16k.wav` is the same stem decoded
  the way the worker decodes it for alignment; onsets and vocal energy are
  measured on it, as in production.
- `raw/` — downloads exist only inside a temp dir there and are deleted as soon
  as the stem exists (also on failure). If YouTube refuses the download, drop a
  file named `<id>.<ext>` into `raw/`; it is used instead and deleted after.
- Review verdicts are expensive to redo: back them up to
  `/mnt/storage/arsiv/kashi-audit/` (the host disk is not in Velero).

## Environment

The audit image is a released server image plus the `audit` extra
(matplotlib); the code always comes from the mounted checkout.

```bash
# once, from the repo root
docker build -f apps/server/audit/Dockerfile -t kashi-audit .

# every audit.* module runs like this (repo root):
docker run --rm --user 1000:1000 -v "$PWD:/repo" -w /repo/apps/server \
  -e PYTHONPATH=/repo/apps/server/src:/repo/apps/server \
  -v "$PWD/apps/server/benchmarks/data/hf:/models" \
  kashi-audit python -m audit.stems <video id> ...
```

`/models` is the bench's model cache: the live separator checkpoint
(`vocals_mel_band_roformer.ckpt`) is already there, and yt-dlp keeps its EJS
solver under `/models/.cache`. For linting on the host: `uv sync --extra audit`
(a plain `uv sync` removes matplotlib again).

## Modules

| module | where | what it does |
|---|---|---|
| `audit.stems <id>...` | audit image | download → separate with the live model (mixback 0) → keep the stem and its 16 kHz decode; the download is deleted |
| `audit.ear <id> --doc D --times T` | audit image | the aligner checkpoint's emissions over ±2.5 s of the stem: greedy letters with times + P(some letter) per frame → `ear/<id>/<t>.json` |
| `audit.moment <id> <t> --doc D` | host (`uv run --extra audit`) | prints the overlay strip's player times for a moment |
| `apps/overlay/scripts/audit-strip.mjs` | host Node / Playwright image | what the overlay shows at those times: `sim` runs the renderer's own pure functions, `real` the built renderer in Chromium (PNG per frame) and asserts both agree |
| `audit.render <id> <t> --doc L=D,...` | host | the picture (`moment-<t>.png`, ≤1568 px wide) and its numbers (`moment-<t>.json`) |
| `audit/moment.sh <id> <t> L=D,... [--real]` | host | all of the above for one moment |

`audit.analysis` caches per song: vocal loudness and onsets (the production
functions, on the 16 kHz stem) and **voice onsets** — where the voice returns
after ≥150 ms below line QA's −25 dB silence threshold. On pop, librosa onsets
are dense (one every ~344 ms), so they prove little; voice onsets are the
evidence for "this line starts late".

### Reading a picture

One song-clock axis (= player position) for every panel. White dashed: the
moment. Teal verticals: voice onsets. Purple hairlines + numbers: strip frames.

1. Separated-vocal mel spectrogram; loudness (dB re the song's loudest frame)
   with the −25 dB silence line; librosa onsets as ticks along the top.
2. The model's ear: P(some letter) (teal) and its greedy letters, on the
   model's RAW clock — documents add the language's offset table on top
   (English −50 ms base, −20..−80 by initial class).
3. One lane per document version: word boxes (red = 0 ms, amber = < 60 ms,
   red label = pile), grey verticals = line starts, and under the boxes when the
   SCREEN lights each word (start − offset; a red tick = lit for 0 ms).
4. The strip: one row per frame — the line on screen, purple = the active word,
   white = sung, grey = not yet; a gold box = an fx word held lit (hype).

`moment-<t>.json` carries the same numbers: per line the first word's start,
when the screen lights it, the nearest voice onset and both differences
(`doc_minus_voice_ms`, `screen_minus_voice_ms`), per word its duration and
flags, the model's letters over the line, and the strip rows.

### Review records

`scratch/audit/review.jsonl`, one object per moment, written BEFORE the owner
listens: `{id, t_ms, tags, verdict, better_version, claim, note}` with
`verdict` one of `ok | late | early | pile | missing | wrong_text | render_bug |
unsure` and `claim` a number the picture supports (for example "screen lights
the line 940 ms after the voice returns"). `unsure` moments and taste questions
go to `caner-dinle.md` (song, mm:ss, one question).

## Timings

| step | host | per track |
|---|---|---|
| separation, CPU | cnr-intel (4 cores) | (first run pending) |
