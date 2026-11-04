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
| `audit.anchors <id> --doc D [--lyrics R]` | host | is the lyrics sheet where the singing is? Whole-song anchors (A1), activity correlation (A2), coverage (A3) → one flag per song (below) |
| `audit.inject manifest\|run ...` | host | seeded errors injected into correct records: the anchor check's bench gate (below) |
| `audit.ext_ear <id> --backend qwen3-asr\|faster-whisper [--mix M]` | desktop GPU (Qwen) / host CPU (Whisper) | a second ear that never sees the lyrics: the stem cut at the finder's silences, each piece transcribed and stamped → `ear/<id>/whole-<backend>.json` (below) |
| `audit.ear_gates <id> --doc D --ear E --ext X` / `--manifest M` | host | the second ear's step-2 gates against the current ear (below) |
| `audit.summarize pack\|run\|count\|score ...` | host (+ desktop llama-server / Message Batches) | the summarizer trial: one text pack per song, two requests per song, M1-M5 against the verdicts (below) |

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

Verdicts are taken as read on 2.29 (`--verdicts-describe`; the claims read "2.29 puts…"). When the
BEFORE document is another version, only moments whose versions agreed count — `better_version`
`either` (equivalent: both fine, or the same flaw in both) or `none` (both bad, neither better,
mostly a lyrics-source flaw); `mixed` and a version name are `n/a:version`. The owner's listen
enters through `agreement` alone (`caner` is the owner's free-text answer and is never parsed):

| `agreement` | the verdict |
|---|---|
| `agree` | used |
| `partial` | used, labelled `owner-partial` |
| `disagree` | not used: `n/a:owner-disputed`, listed apart with its diff |
| `screen-right, not-perceived` | used (the flaw is on screen, the owner did not notice it), labelled `not-perceived` |

Any other value stops the load. The report counts the four states (and "no listen") and the
verdicts taken from the numbers alone (`picture not read` in the note), overall and per class. `--audio` adds `audit.score` per song and, per moment, whether a placed line
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
16 kHz is sample-identical, so the aligner hears exactly what prod's aligner heard. The stem's
meta (`stems/<id>.json`) must name the settings' separator: a model or mixback other than
`SEPARATION_MODEL_FILENAME` / `SEPARATION_MIXBACK` stops the run (exit 2); a stem without that
record gets a warning.

- **Settings come from the environment, as in the worker.** Pass the live worker's env with
  `--env-file apps/server/audit/worker.env.example` (`ALIGN_MODELS` above all), or the document
  comes from a different pipeline. **Sync that file with gitops
  `apps/kashi-server/deployment-worker.yaml` before an acceptance run**; docker takes its values
  literally (no quotes around the JSON). `--extras` records what was in force (settings, the
  resolved aligner and its offsets) and what prod computes but never stores: the onsets and vocal
  energy line QA read, each word's prob / lateness shift / placing pass (`edges`, `segment`,
  `whole`), line QA's per-line lists.
- **Lyrics:** an lrclib record (`/api/get` JSON, or a search list with `--record-id`) is anchored as
  in prod; `--no-anchors` keeps its lines and drops the stamps (whole-audio alignment, line QA
  without references); a text file is plain lyrics, exactly like `options.lyrics_text` (needs
  `--title`/`--artist`: the document requires them).
- **lrclib is never asked**, whatever the environment says (prod runs with
  `LRCLIB_PUBLISH_ENABLED=true`): for the run publishing is off, the worker's lrclib gateway,
  lookups and `publish_document` refuse, and httpx refuses lrclib.net, its subdomains and the
  configured primary and mirror. Lyrics come from `--lyrics` only; an attempt fails the run (exit 2)
  even when a caller swallows the error. Other hosts (the model hub) stay reachable.
- **Not reproduced:** nightcore (r = 1 only); the lyricsfile fast path (a warning says when prod
  would have skipped CTC); separation modes other than the live `always` (refused); beats, energy
  and sections unless `--mix` is given (they never touch word timings; fx selection reads sections).
  `--mix` is the full mix as played; `stems/<id>.wav` is the vocal stem and is refused (audit.stems
  deletes the raw download, so the mix usually has to be fetched again).
- Tests (`tests/test_audit_run_local.py`) replace `ctc_forced_aligner` with a deterministic
  stand-in and prove the local document equal to the one `process_job` persists for the same
  inputs, with and without the database.

**Acceptance on a real song (owner's host, not CI):** the local document must equal the prod 2.29
one word for word within one frame. The record must be the one prod aligned
(`alignment.lyrics_source_id`), and the stored document must be a fresh 2.29 run — a document the
worker refused as a regression (`kept the stored ... document` in its log) is an older pipeline's.

```bash
# repo root; <id> has a stem (audit.stems); worker.env.example is synced with gitops
# apps/kashi-server/deployment-worker.yaml
mkdir -p scratch/audit/runs/<id>
curl -s -H "Authorization: Bearer $KASHI_KEY" "$KASHI_URL/v1/lyrics/youtube/<id>" \
  > scratch/audit/runs/<id>/prod.json
curl -sf -A "kashi-audit (https://github.com/csermet/kashi)" \
  "https://lrclib.net/api/get/$(jq .alignment.lyrics_source_id scratch/audit/runs/<id>/prod.json)" \
  > scratch/audit/runs/<id>/record.json
docker run --rm --user 1000:1000 -v "$PWD:/repo" -w /repo/apps/server \
  -e PYTHONPATH=/repo/apps/server/src:/repo/apps/server --env-file apps/server/audit/worker.env.example \
  -v "$PWD/apps/server/benchmarks/data/hf:/models" \
  kashi-audit python -m audit.run_local <id> --lyrics ../../scratch/audit/runs/<id>/record.json \
    --out ../../scratch/audit/runs/<id>/local.json --extras ../../scratch/audit/runs/<id>/extras.json
cd apps/server && uv run python -m audit.compare ../../scratch/audit/runs/<id>/prod.json \
  ../../scratch/audit/runs/<id>/local.json --same-within-ms 20
```

`--same-within-ms 20` exits 0 only when lines, texts and words are the same and every start and
end is within 20 ms. A miss names the song; `--json` lists every moved word. Check
`pipeline_version` and `alignment.method` on both sides first (a different checkpoint means a
different env). A song in one input only fails the check too. A pass is proof; a miss alone is not:
the stem comes from a fresh download and the record from today's lrclib, and the document keeps no
hash of either, so rule out the inputs before blaming the code.

First run (2026-11-03, Stressed Out, the prod document of 2026-10-28, CPU): 528 of 528 words and all
49 lines identical, max shift 0 ms.

### Lyrics-sheet anchor check (A1-A3)

```bash
cd apps/server
PYTHONPATH=src uv run python -m audit.anchors <id> --doc <prod.json> [--lyrics <record.json>] \
  [--ear <whole.json>] [--json out.json] [--noisy]
```

Reads a sheet against the whole-song ear (`ear/<id>/whole.json` by default, or an `audit.ext_ear`
file). With `--lyrics` the sheet is the lrclib record (`/api/get` JSON; a warning when its id is
not the document's `alignment.lyrics_source_id`), its paragraphs are the sections; without it the
document's own line starts are the sheet. Deltas are **heard minus sheet** (the finder's
`line_offset` sign; a sheet stamped late reads negative), on the model's raw clock (healthy songs
read +20..+90 ms). Why and the sources: `docs/research/yerel-model-denetim-2026-11.md` §8.

- **A1** — anchors are runs of ≥ 2 consecutive heard words (`--noisy`: ≥ 5) and ≥ 8 letters that
  fit exactly one place of the sheet (an exact repeat or a near-spelling the run also fits makes
  it ambiguous) and one place of the ear within the span earlier anchors leave open (the whole
  song for the first). Searched over the whole song, longest run first, each
  anchor splitting the search on its two sides (DSAlign), so anchors stay in order; within an
  anchor no gaps (Smith-Waterman with indels forbidden). Words of ≥ 4 letters match at a
  SequenceMatcher ratio ≥ 0.75, shorter ones exactly. A mid-line anchor steps back to the line's
  start at the singer's own rate (two or more of the line's words in the anchor), else at the
  line's. Lines no anchor reaches — repeated refrains above all — are placed afterwards, in order,
  by the letter match of `audit.textmatch.locate`, searched only in the span between the line placed
  before them and the next anchored line (never the whole song).
- **Sections and blocks** — per section the median delta; consecutive sections whose medians lie
  within 1 s of the running block median are one block. A block is decided when ≥ 2 and ≥ 60 % of
  its lines lie within 1 s of its median; `shifted` when |median| > 1 s; a median beyond 5 s also
  needs ≥ 5 confirmed lines, else `undecidable`.
- **A2** — the sheet's implied activity (each line from its stamp for its letters at the line's
  rate, capped at the next stamp) against the ear's letter mass (100 ms bins, 500 ms box) over
  offset ±30 s × stretch 0.95-1.05; accepted at NCC ≥ 0.8; `mid_offset_ms` is the shift at the
  sheet's median line, `confirms_a1` within 1 s of A1's song median. A rival peak ≥ 4 s away within
  0.05 of the best is `ambiguous` (the refrain period); on a tie the stretch nearest 1.0 wins. A
  piecewise shift does not fit one offset: A2 is a confirmation, never the flag.
- **A3** — per line of ≥ 8 letters, the capped character error of its letters anywhere inside its
  corrected window (block median if the block's lines agree, else the song median; ±2 s) — heard at
  CER ≤ 0.5; a 3-gram TF-IDF cosine of sheet and ear (IDF over the sheet's lines; reported); sung
  activity = every heard letter ±200 ms, covered by a heard line's span ±500 ms. Unmatched sung
  pieces at most 1 s apart are one block, blocks ≥ 3 s are kept; a block **reads** when it has ≥ 4
  letters per second and ≥ 60 % of its heard words have two letters and a vowel.
- **Flags** (pre-registered): `shifted` — any block shifted; `wrong-version candidate` — unmatched
  sung ≥ 25 % of sung **and** ≥ 25 % of the counted lines unmatched (the Enemy rule) **and** readable
  blocks ≥ half the block time; else `ok` when a block is decided ok and the coverage rule does not
  hold; else `undecidable` (the hardstyle negative: lines unmatched, nothing readable). `ok` and
  `undecidable` are not flags.

Every threshold above is a named constant in `audit/anchors.py`, pinned by
`tests/test_audit_anchors.py` and committed (2026-11-04) before any real run.

**Injected errors (bench gate).** `audit.inject` damages a correct record per case — unmodified,
global +2/+5/+10/+20 s, a linear drift reaching 2-10 s at the last line, one verse shifted 3-20 s
(a section of ≥ 5 lines: the check never moves a shorter block by more than 5 s, so a shorter verse
would measure the case mix, not the check), one verse of ≥ 2 lines deleted, four lines of another
song appended after the last one — in one seeded random order, and runs the check on each. The
gated shifts are the note's, all sheet-late; the sheet-early direction (global −2/−5/−10 s, stamps
clamped at 0 as no record has a negative stamp) is injected too and **reported only** (`early`:
detection ≥ 5 s and < 5 s, offset error p90 and max), outside every check. A global shift is
detected when the song is flagged `shifted`, a verse shift only when the injected verse's own
block reads `shifted`. Gate (late shifts only): shifts ≥ 5 s detected in ≥ 90 % of cases (2 s
reported); the shifted part's median within 0.5 s of the injected shift in **every** detected case
(the 90th percentile, nearest rank, is reported); ≤ 5 % of unmodified sheets flagged. Drift,
deleted verses and extra paragraphs are reported (`unmatched_*_share`). Exit 0 only when the gate
passes.

```bash
# local (owner's host). Jamendo audio is CC NC/ND: stems and ears stay under scratch/.
cd apps/server
# 1. ears: each song through the stem + whole-ear path (English: the 1B checkpoint, else MMS)
for f in benchmarks/data/jamendolyrics/mp3/*.mp3; do s=$(basename "$f" .mp3);
  cp "$f" "../../scratch/audit/raw/jam-$s.mp3"; done   # audit.stems takes raw/<id>.<ext>
# (audit image) python -m audit.stems jam-<stem> ... ; then per song
# python -m audit.ear jam-<stem> --whole --model jonatasgrosman/wav2vec2-xls-r-1b-english
#   (fr/de/es: --model MahmoudAshraf/mms-300m-1130-forced-aligner)
# 2. correct records + manifest, then the gate
PYTHONPATH=src uv run python -m audit.inject manifest --jamendo benchmarks/data/jamendolyrics \
  --out ../../scratch/audit/inject
PYTHONPATH=src uv run python -m audit.inject run --pairs ../../scratch/audit/inject/manifest.json \
  --seed 1 --json ../../scratch/audit/inject/gate-seed1.json
```

**Pilot-set acceptance (local):** GOSSIP and Got 2 Luv U flagged `shifted` with sign and size
within 1 s of the Faz 3 reading (−18.9 s, −4.0 s), none of the other 17 flagged, the hardstyle
track not `wrong-version candidate`; Enemy `wrong-version candidate` only if the owner's listen
confirms another mix. Run it on the record the document was aligned on (`run_local`'s curl above
fetches it):

```bash
PYTHONPATH=src uv run python -m audit.anchors <id> --doc ../../scratch/audit/runs/<id>/prod.json \
  --lyrics ../../scratch/audit/runs/<id>/record.json --json ../../scratch/audit/runs/<id>/anchors.json
```

### A second ear (Qwen3-ASR, Whisper)

`audit.ext_ear` cuts the stem at the finder's silences — ≥ 500 ms below −25 dB on the vocal
loudness contour (`audit.analysis`, the same source as line QA's threshold; −25.0 dB itself is
voiced) — into voiced pieces, cuts any piece longer than 30 s at its quietest frame in the middle
half until none is, transcribes each piece on its own and spreads its letters evenly over it (the
letter rate is the piece's length); pieces never merge into one word. `--mix` transcribes the
played mix instead (Whisper reads separated vocals worse: Jam-ALT, `yerel-model-denetim-2026-11.md` §1) with the stem's cut points.
`--word-times` (Qwen only, en/ja/es/de/fr) spreads letters over Qwen3-ForcedAligner's word spans;
Whisper's own segments inside a piece are used the same way (`word_times: "whisper-segments"`);
the file says which (`word_times`). The output is the whole-song ear's shape plus `source`
(backend, model, package version, settings), `timing: "segment"`, `input`, `segments` (the pieces)
and `lines` (the finest timed spans: Whisper's segments or the aligner's words, else the piece);
`audit.textmatch`, `audit.finder` and `audit.anchors --ear` read it unchanged. Japanese stays in
script.

The backends are imported lazily and are **not** in `uv.lock`; install them where they run:

```bash
# Qwen3-ASR on the desktop, in the bench GPU image (the path that ran Qwen3-ForcedAligner in
# 2026-08). qwen-asr 0.0.6 is Apache-2.0 but pins soynlp 0.0.493 (GPL-3.0), which it imports only
# for Korean: install it without its pins and add the rest (all Apache-2.0 / MIT), so no GPL
# package enters the image. gradio/flask/sox are only for its demo CLIs.
docker run --rm --gpus all --ipc=host -v "$PWD:/repo" -w /repo/apps/server \
  -e PYTHONPATH=/repo/apps/server/src:/repo/apps/server -v kashi-bench-models:/models \
  --entrypoint bash kashi-bench-gpu -lc "pip install -q --no-deps qwen-asr==0.0.6 && \
  pip install -q transformers==4.57.6 accelerate==1.12.0 nagisa==0.2.11 qwen-omni-utils==0.0.9 && \
  python -m audit.ext_ear <id> --backend qwen3-asr --language en"

# Whisper large-v3-turbo on cnr-intel's CPU (faster-whisper 1.2.1, MIT; CTranslate2 INT8):
cd apps/server
PYTHONPATH=src uv run --with faster-whisper==1.2.1 python -m audit.ext_ear <id> \
  --backend faster-whisper --mix <the played mix> --language en
```

Day-one gate for Qwen: it runs. The 20 pilot mixes for the Whisper arm are fetched again (audit.stems
deleted the downloads), stay on cnr-intel, and are deleted when the test ends — record the
listing (`ls -la`) before and after.

**Gates** (`audit.ear_gates`, pre-registered, `tests/test_audit_ext_ear.py`): per song both ears
against the same document — placed lines (the finder's ear-text match, look-alike borrowings left
out; `--search-ms` widens the ±8 s window); the external ear's placed share ≥ the current ear's;
`--expect healthy` → |median line offset| ≤ 0.5 s; `--expect sheet-late` (GOSSIP, Got 2 Luv U) →
median < −1 s, `sheet-early` → > +1 s; external transcript lines (`lines`, else the pieces) with
text lying ≥ 50 % inside a region where the CTC letter curve stays below 0.2 for more than 2 s
≤ 5 % (hallucination on instrumentals; the share is exact). Japanese and Turkish songs are
reported, never gated (the language comes from the document, or `--language` / the entry's
`language` — a Japanese sheet in romaji needs it). `--manifest`
(`[{id, doc, ear, ext, expect[, search_ms, language]}]`) adds the arm's verdict: pooled placed
lines ≥ the current ear's, every expectation met, pooled silent-line share ≤ 5 %. Give GOSSIP's
entry `"search_ms": 30000` (its verses sit 17-19 s off; ±8 s placed 11 lines at −3.5 s), and keep
the healthy songs at the default window.

```bash
PYTHONPATH=src uv run python -m audit.ear_gates --manifest ../../scratch/audit/ext/qwen.json \
  --json ../../scratch/audit/ext/qwen-gates.json
```

### Summarizer trial (step 3)

`audit.summarize` builds one **text** pack per song from `runs/<id>/moments.json` and every
rendered `moment-<t>.json` (suffixed variants such as `-real` are skipped): per moment its source
(finder rank and score, or control), the finder's events, the voice returns in the window, the
ear's text, and per document version one row per line with every difference already computed
(doc−voice, screen−voice, the start delta against the previous version, word flag counts,
syllables against loudness bumps, the ear's letters). The question comes last; no picture; the
pack is built from a whitelist of number fields, so no verdict, claim, note or owner answer can
enter it. `--seed` shuffles the moment order (the three stability runs); `--kind probe` asks the
M4 question instead ("which line of the last version has the second-largest |screen−voice|, and
how large?") and stores the answer (none when not unique). `est_tokens` is a chars/3.5 estimate
against the 12k target; `count` gives the real number — with Claude's tokenizer (request 1, and
request 2's worst case: request 1 + a prose of `max_tokens` + the conversion line), or with
`--backend local` the server's own (llama-server `/apply-template` + `/tokenize`; a song fits when
request 1 + the conversion line + twice `--max-tokens`, prose in and answer out, stays within
16,384; check that the server version has both endpoints).

**Numbers-plus-picture arm (Opus only).** `pack --images` attaches each moment's
`moment-<t>.png` (from `audit.render`; a moment without one is named on stderr): the table, a
short note on how to read the picture, then per moment a one-line label and the picture (base64
PNG) in the table's order, the question last. The pictures' hashes enter the prompt hash, and a
picture rendered again after packing is refused at send time. The local backend refuses picture
packs; score both arms with the same `score` call.

`run` sends two requests per song: prose, then the same prefix + the prose + "now as JSON"
(`audit/summarize_schema.json`: per moment `t_ms`, `class`, `severity` 0-3, `reason`; the answer
must judge every moment of the pack exactly once). One output per song in `--out`
(`<id>.json`: model, prompt hash, settings, prose, JSON, usage, cost; temp file + atomic rename).
A rerun skips songs done with the same prompt hash and settings and resumes a finished prose at
request 2 (also after request 2 failed: the prose is never paid twice). A submitted batch is
reused (`.batch-r1.json` / `.batch-r2.json`, one entry per batch, removed once the records are
written) only for requests whose params are byte-identical to what it was sent — another effort,
seed or pack sends a new batch, and a grown request set sends only the new requests.
Infrastructure failures (errored, expired, canceled, missing) run again; the model's own failed
answers (refusal, empty, truncated, invalid JSON) only with `--retry-failed`, and the record keeps
the song's **first** answer (`first_answer`) for M5. A reply cut at `max_tokens` (`finish_reason:
length` locally) is `truncated` and never sent on as a complete prose.

- `--backend local`: llama-server's OpenAI-compatible `/v1/chat/completions` (httpx),
  temperature 0, `chat_template_kwargs.enable_thinking=false` (override or extend with
  `--extra-body '{...}'`; check which switch the server's Gemma 4 template reads), request 2 with
  `response_format` json_schema (grammar-constrained).
- `--backend batch`: Message Batches through the `anthropic` SDK (2.3.0, `audit` extra; it sends
  API version 2026-10-21). `claude-opus-5-5`, `thinking: {type: adaptive}` (Opus 5.5 cannot
  disable it), `output_config.effort` always sent (`--effort`, default `medium`, the model's own
  default), request 2 adds `output_config.format` (json_schema); no `temperature`/`top_p`/`top_k`
  (400), no prefill, no `fallbacks` (Batches rejects it — a `stop_reason: refusal` is counted and
  recorded instead). Request 2 needs request 1's text, so two batches run back to back; results come
  in any order and are matched by `custom_id` (`r1-<id>`, `r2-<id>`); succeeded / errored /
  canceled / expired / refusal are counted per batch. Cost from `usage` at Batches prices
  (`PRICES`: input, output, cache writes 5 min / 1 h, cache reads; thinking is billed as output).
  No prompt caching: request 2 comes after the first batch has ended, past a 5-minute cache, and
  a 1-hour write (2×) would not pay for one read. The key is read from `ANTHROPIC_API_KEY` only and
  never written to a file or a log. Packs carry lyric lines and song ids: on the Batches path they
  are retained for 29 days (`yerel-model-denetim-2026-11.md` §7).

`score` (pre-registered, `tests/test_audit_summarize.py`): labels from `review.jsonl` (repeat
`--labels` for the owner's listening set; a later file wins per moment), unsure and
owner-disputed verdicts left out. A moment is flagged at severity ≥ 1; a labelled moment without
a valid prediction counts as not flagged. Gated on the first `--runs` directory, the others
reported. Every labelled moment counts (n is the labelled set): a labelled song missing from the
run counts as not flagged and fails the `complete` gate. **M1** recall of real errors (pile, late,
early, missing, wrong_text) ≥ 0.90 and precision ≥ 0.60; **M2** Spearman of model severity against label severity (ok 0, pile 1,
late/early 2, missing/wrong_text 3) pooled ≥ 0.5 and precision@3 (expected under ties) ≥ 0.7 on
songs with ≥ 3 real errors; **M3** recall of late + early + missing + wrong_text ≥ 0.7 (piles
reported); **M4** probe answers right (line, value within 10 ms) on ≥ 95 % of the songs with a
unique answer, and at least 20 such songs (19 of 20); **M5** each song's first answer from the
model schema-valid and covering every moment ≥ 99 % (a refusal, an empty or truncated reply counts
against it; infrastructure failures do not count), class agreement over every pair of runs ≥ 90 %
with at least three runs of distinct seeds. Shares are exact, never rounded before a gate.
`passed` needs the probe run.

```bash
cd apps/server
# packs (three shuffled orders + the probe), counted once
for s in 1 2 3; do PYTHONPATH=src uv run --extra audit python -m audit.summarize pack --all \
  --seed $s --out ../../scratch/audit/summ/packs-s$s; done
PYTHONPATH=src uv run --extra audit python -m audit.summarize pack --all --kind probe --seed 1 \
  --out ../../scratch/audit/summ/probe
ANTHROPIC_API_KEY=... PYTHONPATH=src uv run --extra audit python -m audit.summarize count \
  --packs ../../scratch/audit/summ/packs-s1                # Claude's tokenizer: the Opus cost
PYTHONPATH=src uv run --extra audit python -m audit.summarize count --backend local \
  --url http://<desktop>:8080 --packs ../../scratch/audit/summ/packs-s1   # Gemma: the 16k fit
# Opus through Batches (the key and the workspace are the owner's call), then Gemma on the desktop
ANTHROPIC_API_KEY=... PYTHONPATH=src uv run --extra audit python -m audit.summarize run \
  --packs ../../scratch/audit/summ/packs-s1 --backend batch --effort medium \
  --out ../../scratch/audit/summ/opus-s1
PYTHONPATH=src uv run --extra audit python -m audit.summarize run \
  --packs ../../scratch/audit/summ/packs-s1 --backend local --url http://<desktop>:8080 \
  --model gemma-4-26b-a4b --out ../../scratch/audit/summ/gemma-s1
# (s2, s3 and the probe likewise); the Opus picture arm from its own packs
PYTHONPATH=src uv run --extra audit python -m audit.summarize pack --all --images --seed 1 \
  --out ../../scratch/audit/summ/packs-img-s1
# then per arm
PYTHONPATH=src uv run --extra audit python -m audit.summarize score \
  --runs ../../scratch/audit/summ/opus-s1 ../../scratch/audit/summ/opus-s2 \
  ../../scratch/audit/summ/opus-s3 --labels ../../scratch/audit/review.jsonl \
  --probe ../../scratch/audit/summ/opus-probe --json ../../scratch/audit/summ/opus-score.json
```

### Japanese: script-aware matching

`audit.kana` puts the four Japanese writings into one form — a kanji/kana sheet, a romaji sheet
(lrclib has both), the MMS ear (Latin letters: uroman romanises what it hears) and Qwen3-ASR (script)
— and `audit.textmatch.matcher_for("jpn")` returns the matching pair (line normalizer, ear stream
builder) for `locate`; every other language keeps the a-z pair. `audit.ear_gates` uses it for
Japanese documents (reported, not gated). The finder, `audit.score` and `audit.find` are unchanged
(a-z); `audit.anchors` is not Japanese-aware (its anchors are space-separated words).

**Why romaji, not kana, is the common form.** Turning MMS's noisy greedy letters back into kana is
ambiguous and lossy (`kng`, `tsh` are no kana); kana → romaji is a deterministic table, and romaji
keeps textmatch's a-z thresholds (12 letters, ratio 0.55, 50-150 ms per letter) meaningful. So:
kanji → kana with the production analyzer (`kashi_server.pipeline.japanese`: fugashi 1.5.2,
MIT AND BSD-3-Clause, + unidic-lite 1.0.8, MIT — already base dependencies, so no new package; the
SudachiPy candidate was not needed), kana → Hepburn by a hand-written table (digraphs, small tsu,
the long-vowel mark, loanword small vowels), then one simplifier for every Latin writing: long
vowels (`ō`, `ou`, `oo`, `oh` before a consonant, `ei`, doubled vowels) and doubled consonants
(small tsu, `tch`, `cch`, `nn`) fold to one letter, `n'` and `m` before b/p/m to `n`, Hepburn and
Kunrei to one spelling (`shi/si`, `chi/ti`, `tsu/tu`, `fu/hu`, `ji/zi`, `sh/sy`, `ch/ty`, `j/zy`,
`dzu/du/zu`, `di/zi`, `wo/o`). Each output letter keeps its source letter's time; the ear stream is
read as one run, as a line is, so a long vowel folds across a word boundary on both sides alike.
Particles written by spelling (`ha`/`he` for は/へ) and dictionary misreadings (a kanji read in
another reading than sung) are not fixed; the fuzzy match absorbs some of them. Tests:
`tests/test_audit_kana.py` (synthetic sentences, hand-written ears). No gate until at least three
Japanese songs have checked lines.

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
  record was 17-19 s late) shows up in `audit.score` and in a control, not in the ranking —
  `audit.anchors` (whole-song unique anchors) is the check for it.
- Japanese lyrics never match in the finder and `audit.score` (a-z); `textmatch.matcher_for("jpn")`
  (`audit.kana`) matches them, `audit.ear_gates` uses it. CJK glyphs draw as boxes in the pictures.
- Most finder false positives are a voice "return" with no ear letters at all: wordless outros,
  instrument figures leaking into the stem, breaths.

Findings and numbers: `docs/research/perceptual-audit-2026-11.md`.
