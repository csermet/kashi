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
| `audit.ear <id> --doc D --whole` | audit image | the same over the whole stem (~2.5 min CPU incl. model load) → `ear/<id>/whole.json`; render cuts each moment's window from it |
| `audit.find <id> --doc L=D,...` | host | ranks the song's suspicious moments (`audit.finder`) → `runs/<id>/moments.json`, prints the ranking, two seeded control moments and the ear-text summary |
| `audit.moment <id> <t> --doc D` | host (`uv run --extra audit`) | prints the overlay strip's player times for a moment |
| `apps/overlay/scripts/audit-strip.mjs` | host Node / Playwright image | what the overlay shows at those times: `sim` runs the renderer's own pure functions, `real` the built renderer in Chromium (PNG per frame) and asserts both agree |
| `audit.render <id> <t> --doc L=D,...` | host | the picture (`moment-<t>.png`, ≤1568 px wide) and its numbers (`moment-<t>.json`) |
| `audit/moment.sh <id> <t> L=D,... [--real]` | host | all of the above for one moment |

`audit.analysis` caches per song: vocal loudness and onsets (the production
functions, on the 16 kHz stem) and **voice onsets** — where the voice returns
after ≥150 ms below line QA's −25 dB silence threshold. On pop, librosa onsets
are dense (one every ~344 ms), so they prove little; voice onsets are the
evidence for "this line starts late".

### The finder

`audit.finder` turns one song into events, clusters them into ≤ 3 s moments
(each tag counts at most three times toward a moment's score) and ranks them.
Tags: `pile` (≥ 2 word starts within 60 ms, weighted per extra word),
`short_run` (≥ 3 words < 60 ms in a row), `zero`, `orphan_voice` (the voice
returns, gets loud — peak ≥ −19 dB — and no word starts within 250 ms),
`line_vs_voice` (a line after ≥ 500 ms of silence starts 250–1500 ms from the
voice's return), `sung_unboxed` / `boxed_silent` (the ear hears letters outside
every box / nothing inside a long quiet box), `qa`, `moved` (> 250 ms between
versions) and `line_offset` (below). Thresholds are pinned as numbers in
`tests/test_audit_finder.py`; they were set on New Rules, Got 2 Luv U and
Crazy In Love (2026-10-31) and are to be revisited on the favourite set.

**Ear-text match** (`audit.textmatch`): each line's letters are slid along the
whole-song ear's letter stream within 8 s of the document's start. A strong,
unambiguous match says where the model hears the line, even in patois; the
time is the first run of 3+ matching letters stepped back by its place in the
line. Lines heard > 1 s away become `line_offset` events, unless the spot
belongs to a look-alike neighbour that is itself heard in place. `audit.find`
prints the song summary (lines placed, median offset, how many > 1 s off):
healthy songs read a median of +20..+90 ms (the model's raw clock against the
documents' offset table); Got 2 Luv U reads −3.8 s for 26 of 27 placed lines,
because lrclib's own stamps for its verses are 3–4 s late.

### Reading a picture

Default theme `kontrast` (grey spectrogram; chosen 2026-10-31 because the agent,
who reads these, separates the role colours from it best). `--theme mor` is the
owner-facing look (purple night, magma spectrogram).

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
| separation, CPU | cnr-intel (4 cores) | 670–980 s (≈ 4.5× real time) |
| whole-song ear, CPU | cnr-intel | ~2.5 min (1 min of it loading the 1B model) |
| one moment (`moment.sh`, sim strip, whole ear cached) | cnr-intel | ~10 s |
| one moment with `--real` frames | cnr-intel | ~40 s |
