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
| `audit.run_local <id> --lyrics R --out D` | audit image, worker env | one song through the worker's alignment and document builder on the cached stem: no database, no download, no separation (below) |
| `audit.compare <before> <after> --verdicts review.jsonl` | host (`--audio`: audit image) | a timing change against the verdicts: per song and per reviewed moment, did the document change and which way (below) |

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

### Before / after a timing change

```bash
python -m audit.compare before.json after.json --verdicts scratch/audit/review.jsonl \
  [--audio] [--json out.json] [--same-within-ms 20]
```

Both inputs are `[{id, doc}]` dumps or single documents (keyed by `track.source.id`, which is
what `audit.run_local` writes). Per song, before → after: pipeline version, quality score,
worded lines, seam piles (`benchmarks.seam_piles`), line-QA counters
(flagged/density_dropped/nudged/uncertain), 0 ms and < 60 ms word shares and bunched starts
(`benchmarks.metrics.duration_hygiene`), and how many words moved by more than one frame (20 ms);
totals at the end.

Per verdict, inside the moment's ±2.5 s: word and line start shifts, piles (the finder's rule),
0 ms words, wordless lines. Classes — every `?` is a **candidate**, decided without audio:

| verdict | class |
|---|---|
| pile / late / early / missing / wrong_text | `bad:fixed?` (piled or 0 ms words fewer; the moved words' median went earlier for `late`, later for `early`; more lit words; the text changed), `bad:worse?` (the other way), `bad:changed` (no clear direction), `bad:unchanged` |
| ok | `good:broken?` (a word moved past +220 / −330 ms, more piled / 0 ms words, fewer lit words, words or lines lost, text changed), `good:moved` (smaller moves), `good:kept` |
| unsure | `unsure:changed` / `unsure:unchanged` |

The owner's listening verdict (`caner`, when it is a verdict word) wins over the agent's.
Verdicts are taken as read on 2.29 (`--verdicts-describe`); when the BEFORE document is another
version only moments whose versions agreed (`better_version` none/either) count, the rest are
`n/a:version`. `--audio` adds `audit.score` per song and, per moment, whether a placed line
crossed the noticeable threshold (`ear: better/worse/same`) — where the stem and whole-song ear
exist. `--same-within-ms N` exits 1 unless every song has the same lines, texts and words with
every start and end within N ms (the `audit.run_local` acceptance check).

### One song through the worker, locally

```bash
python -m audit.run_local <id> --lyrics record.json --out local.json \
  [--no-anchors] [--record-id N] [--mix <full mix>] [--title T --artist A] [--extras x.json]
```

For timing experiments (v3, 2.29.1, plain lyrics) without the production worker, a GPU or the
database. It runs the worker's own `_align_with_ctc` (nightcore plan, aligner stage, line QA on the
stem's onsets and vocal energy) and `_assemble_document` (fx, palette, `build_document`), then the
schema gate `persist_processed_track` applies, and writes the document instead of storing it. The
job is an in-memory `Job`, the session a no-op. The separator call returns the cached
`stems/<id>.16k.wav` (make it with `audit.stems`; nothing is separated here); decoding it again at
16 kHz is sample-identical, so the aligner hears exactly what prod's aligner heard.

- **Settings come from the environment, as in the worker.** Pass the worker's env (`ALIGN_MODELS`
  above all, from the gitops `deployment-worker.yaml`) with `--env-file`, or the document comes
  from a different pipeline. `--extras` records what was in force (settings, the resolved aligner
  and its offsets) and what prod computes but never stores: the onsets and vocal energy line QA
  read, each word's prob / lateness shift / placing pass (`edges`, `segment`, `whole`), line QA's
  per-line lists.
- **Lyrics:** an lrclib record (`/api/get` JSON, or a search list with `--record-id`) is anchored as
  in prod; `--no-anchors` keeps its lines and drops the stamps (whole-audio alignment, line QA
  without references); a text file is plain lyrics, exactly like `options.lyrics_text` (needs
  `--title`/`--artist`: the document requires them).
- **Not reproduced:** nightcore (r = 1 only); the lyricsfile fast path (a warning says when prod
  would have skipped CTC); separation modes other than the live `always` (refused); beats, energy
  and sections unless `--mix` is given (they never touch word timings; fx selection reads sections).
- Tests (`tests/test_audit_run_local.py`) replace `ctc_forced_aligner` with a deterministic
  stand-in and prove the local document equal to the one `process_job` persists for the same
  inputs, with and without the database.

**Acceptance on a real song (owner's host, not CI):** the local document must equal the prod 2.29
one word for word within one frame. The record must be the one prod aligned
(`alignment.lyrics_source_id`), and the stored document must be a fresh 2.29 run — a document the
worker refused as a regression (`kept the stored ... document` in its log) is an older pipeline's.

```bash
# repo root; <id> has a stem (audit.stems); worker.env holds the live worker's pipeline env
mkdir -p scratch/audit/runs/<id>
curl -s -H "Authorization: Bearer $KASHI_KEY" "$KASHI_URL/v1/lyrics/youtube/<id>" \
  > scratch/audit/runs/<id>/prod.json
curl -s "https://lrclib.net/api/get/$(jq .alignment.lyrics_source_id scratch/audit/runs/<id>/prod.json)" \
  > scratch/audit/runs/<id>/record.json
docker run --rm --user 1000:1000 -v "$PWD:/repo" -w /repo/apps/server \
  -e PYTHONPATH=/repo/apps/server/src:/repo/apps/server --env-file worker.env \
  -v "$PWD/apps/server/benchmarks/data/hf:/models" \
  kashi-audit python -m audit.run_local <id> --lyrics ../../scratch/audit/runs/<id>/record.json \
    --out ../../scratch/audit/runs/<id>/local.json --extras ../../scratch/audit/runs/<id>/extras.json
cd apps/server && uv run python -m audit.compare ../../scratch/audit/runs/<id>/prod.json \
  ../../scratch/audit/runs/<id>/local.json --same-within-ms 20
```

`--same-within-ms 20` exits 0 only when lines, texts and words are the same and every start and
end is within 20 ms. A miss names the song; `--json` lists every moved word. Check
`pipeline_version` and `alignment.method` on both sides first (a different checkpoint means a
different env).

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

## Known limits (pilot-set review, 2026-11)

- For Turkish and the MMS languages the ear is the aligner's own checkpoint: its letter times equal
  the boxes by construction. Trust the letters (what is heard, in which order), not that agreement.
- The ear-text match only looks ±8 s around a line; a song-level shift beyond that (one lrclib
  record was 17-19 s late) shows up in `audit.score` and in a control, not in the ranking.
- Japanese lyrics never match: the MMS ear is romanised, the lyrics are in script. CJK glyphs draw
  as boxes in the pictures.
- Most finder false positives are a voice "return" with no ear letters at all: wordless outros,
  instrument figures leaking into the stem, breaths.

Findings and numbers: `docs/research/perceptual-audit-2026-11.md`.
