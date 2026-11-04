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
| `audit.anchor_check <id> --doc D [--record R]` / `--pilot --expect E` | host | Faz 3.5 A1-A3: is the lyrics sheet where the singing is (below) |
| `audit.external_ear pieces / transcribe / import / step2` | GPU host / host | Faz 3.5 Step 2: a lyrics-free ASR ear in the whole-song ear's shape, and its gates |
| `audit.summarizer count / submit / collect / local / evaluate` | host (+ API key / desktop LLM) | Faz 3.5 Steps 0 and 3: the summarizer A/B on identical packs, and M1-M5 |
| `audit.inject ears` / `audit.inject run` | audit image / host | Faz 3.5 Step 1 on injected errors: JamendoLyrics sheets damaged on purpose |
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

### Faz 3.5: the anchor check (Step 1)

The plan, its gates and their reasons: `docs/research/yerel-model-denetim-2026-11.md`, "Faz 3.5".
**Every gate is committed in `audit/gates.py` before any run** (pinned by `tests/test_audit_gates.py`);
where the note needed an operational reading the module says so (`READING`). A gate changed after its
step has run is a new pre-registration: report the old verdict next to the new one.

`audit.anchors` reads a sheet (line texts with stamps: a document's lines, or an lrclib record's
own stamps with `--record`) against the whole-song ear, with no new model:

- **A1 unique anchors.** A line with ≥ 12 letters that shares < 0.6 with every other line is placed
  over the whole song by the same matcher the finder uses (`textmatch.locate`, unambiguous only).
  Consecutive anchors whose offsets agree within 1 s (against the median of the last three) form a
  section; a lone disagreeing anchor is dropped, two that agree open the next section. A section
  whose median offset is beyond ±1 s flags `shift` (`global` when every section is shifted, else
  `section`). Offsets are heard minus sheet on the document clock: −18.9 s = the sheet is 18.9 s late.
- **A2 activity.** The sheet's expected activity (each line for its letters at 90 ms a letter, cut at
  the next stamp) against the ear's P(some letter) on a 100 ms grid, offsets ±30 s × stretches
  0.95-1.05; normalised cross-correlation ≥ 0.8 is a confident fit. It flags a shift only when A1
  found no section (a sheet of refrains).
- **A3 coverage.** A line is heard when its letters match (≥ 0.55) within ±8 s of its corrected start
  (no margin test: a refrain sung twice is heard twice). Singing (P ≥ 0.5) that no heard line
  explains forms blocks (gaps ≤ 1 s merge, ≥ 3 s kept), readable at ≥ 4 letters a second.
  `wrong_sheet` = a readable block **and** ≥ 25 % of the singing unexplained **and** ≥ 25 % of the
  lines unheard — processed vocals the model cannot spell (the hardstyle negative) stay out.
  `unsheeted_singing` (a readable block) and `unsung_lines` (≥ 3 consecutive lines unheard) are
  reported, never alarms.

```bash
python -m audit.anchor_check <id> --doc <dump> [--record record.json]   # -> runs/<id>/anchors.json
python -m audit.anchor_check --pilot --doc <2.29 dump> --expect ../../scratch/audit/step1-expect.json
```

`step1-expect.json` (outside the repository; it names songs) maps an id to what Faz 3 read:
`{"<id>": {"expect": "shift", "offset_ms": -18900, "label": "..."}}`, `{"expect": "none"}` for the
named negative, `{"expect": "wrong_sheet"}` for Enemy only after the owner's listen confirms another
mix. Ids it does not name are expected clean. The pilot passes when every expected shift is flagged
within 1 s of its reading, every expected wrong sheet raises `wrong_sheet`, every other song raises
no alarm, and every named song has a document and a whole-song ear.

Injected errors, on the bench's JamendoLyrics copy (human-timed lines = a healthy sheet):

```bash
# once, audit image with the worker env (ALIGN_MODELS picks each language's checkpoint):
python -m audit.inject ears --jamendo-root benchmarks/data/jamendolyrics \
  --stems benchmarks/data/stems/kim-base --out ../../scratch/audit/jamendo-ears
python -m audit.inject run --jamendo-root benchmarks/data/jamendolyrics \
  --ears ../../scratch/audit/jamendo-ears --json ../../scratch/audit/step1-inject.json
```

Per song: unmodified; every stamp +2/+5/+10/+20 s; linear drift with the last line +5/+10 s; two
4-line verses moved 3-20 s (random sign); a deleted verse; another song's 4 lines appended — in a
seeded random order. Gates: detection ≥ 90 % for shifts ≥ 5 s, median offset error ≤ 0.5 s over
detected global and verse shifts, false alarms (`shift` or `wrong_sheet` on an unmodified song)
≤ 5 %. Reported: 2 s shifts, verses under 5 s, deleted verse, extra paragraph.

### Faz 3.5: a second ear (Step 2)

Qwen3-ASR (and Whisper large-v3-turbo as the second arm) never see the lyrics, so they can hear a
sheet that is wrong. They return text without times: `audit.external_ear` cuts the stem at the
finder's silences (≥ 500 ms below −25 dB; a stretch longer than 30 s is cut at its quietest frame
between 15 and 30 s; pieces under 300 ms are breaths), transcribes each piece, spreads its
characters evenly over it and writes `ear/<id>/<name>.json` **in the whole-song ear's shape**
(`timing: "segment"`, a letter curve of 1 inside pieces with text). The ear-text match, the finder,
`audit.score` and `audit.anchors` read it unchanged. Segment-level times: for the ≥ 1 s class, never
for word timing. Characters of any script are kept; a Japanese script ear meets any sheet in
kana (below).

```bash
python -m audit.external_ear pieces <id>                       # where the stem is cut
# bench GPU image with `pip install qwen-asr` (the day-one gate: it runs on the 5070 Ti)
python -m audit.external_ear transcribe <id> --backend qwen3-asr
# cnr-intel CPU, faster-whisper; the MIX cut at the stem's pieces (separated vocals hurt Whisper)
python -m audit.external_ear transcribe <id> --backend whisper --audio <mix>
# pieces transcribed anywhere else: [{"start_ms", "end_ms", "text"}]
python -m audit.external_ear import <id> --transcript pieces.json --model qwen3-asr
python -m audit.external_ear step2 --doc <2.29 dump> --ear qwen3-asr \
  --expect ../../scratch/audit/step1-expect.json
```

The backends' calls (`qwen_asr.Qwen3ASRModel.transcribe`, `faster_whisper.WhisperModel`) are
written from their documentation and were never run here; `import` works whatever runs them. Step 2
gates per arm (`audit.gates`): placed-line share (the finder's ear-text match, pooled over songs)
at or above the CTC ear's; every healthy song with ≥ 3 placed lines within ±0.5 s; every expected
shift beyond 1 s with its sign; at most 5 % of the pieces with text centred in a stretch the CTC
letter curve calls silent (P < 0.2) for > 2 s. Healthy = not named in the expectations file, or
named with `expect: none` and without `"healthy": false`. The 20 mixes kept for the Whisper arm are
deleted when the test ends; record the listing.

### Japanese: both sides to kana

The ear-text match reduced every text to a-z, so a Japanese sheet in script placed no line, and a
script ear could not meet a romaji sheet. `audit.kana` turns any text into folded hiragana by one
function used on both sides: Japanese script through the production module's UniDic readings
(`pipeline.japanese`), Latin letters through romaji (Hepburn, Kunrei and Nihon-shiki spellings, l
read as r, doubled consonants as っ, m before b/p/m as ん), then long vowels fold (ー dropped; a
vowel repeating the previous kana's vowel, う after an o and い after an e dropped), so ありがとう,
アリガトー and "arigato" are one string. `finder.line_offsets` (and with it `audit.find`,
`audit.score` and the Step 2 gates) switches to kana when ≥ 20 % of the sheet's or the ear's
characters are Japanese script; the unit is the kana (≥ 6 per line, 180 ms a unit, 100-300 ms
clamped). Latin songs never reach that path (proven identical to the old matcher on seeded
documents). Until three Japanese songs have checked lines their placement is reported, never gated
(Step 2 keeps them out of every gate).

### Faz 3.5: the summarizer A/B (Steps 0 and 3)

`audit.pack` builds one text per song and run, the same for every arm: the song's numbers
(`audit.score`, the ear-text match), an unsorted LINE TABLE (`lit_minus_voice_ms` = when the screen
lights the line minus where it is sung) and the labelled moments in a seeded shuffled order, each
with the finder's events and its moment file's ready-made differences. The model never subtracts;
the numeric probe (M4: the second-largest |lit_minus_voice_ms|) exists to measure exactly that. The
prompts and the JSON schema are constants; their hash (`PROMPT_SHA`, pinned by a test) goes into
every output. Two requests per song: prose, then JSON with the prose appended (strict JSON costs
reasoning); the pack sits in the cached system prefix of both.

| arm | how |
|---|---|
| `opus` | Claude Opus 5.5 through Message Batches, effort `high` set explicitly, no thinking or sampling parameters (it takes none), cost read from `usage` at batch prices |
| `opus-pictures` | the same with `--pictures`: each moment's `moment-<t>.png`, named by its moment id |
| `gemma` | the desktop's llama.cpp server (`/v1/chat/completions`, temperature 0, `cache_prompt`, the schema as `response_format`) |

```bash
# the SDK is no project dependency: uv run --with anthropic ...; credentials from
# ANTHROPIC_API_KEY or `ant auth login`
uv run --with anthropic python -m audit.summarizer count --doc <2.29 dump> \
  --verdicts ../../scratch/audit/review.jsonl --claude --local http://<desktop>:8080   # Step 0
uv run --with anthropic python -m audit.summarizer submit --doc D --verdicts R --arm opus --phase prose
uv run --with anthropic python -m audit.summarizer collect --doc D --verdicts R --arm opus  # until ended
uv run --with anthropic python -m audit.summarizer submit --doc D --verdicts R --arm opus --phase json
uv run --with anthropic python -m audit.summarizer collect --doc D --verdicts R --arm opus
python -m audit.summarizer local --doc D --verdicts R --arm gemma --url http://<desktop>:8080
python -m audit.summarizer evaluate --verdicts R --arm opus --listening ../../scratch/audit/listening.jsonl
```

Outputs: `runs/step3/<arm>/run<k>/<id>.json` (model, effort, prompt hash, moment ids, prose,
answer, first-try validity, usage, cost), written atomically; every command resumes. A refusal
(`stop_reason: refusal`) is recorded as no answer: the server-side `fallbacks` parameter is rejected
on the Batches API. Message Batches keep requests 29 days and are not zero-retention eligible; the
pack carries lyric lines and numbers, never audio.

`audit.step3` scores against the Faz 3 verdicts under the owner rules (disputed and `unsure` out):
reproducibility. The listening set is the accuracy reference, reported:
`scratch/audit/listening.jsonl`, the same JSONL shape (`id, t_ms, verdict`, no `agreement`), a
subset of the 96 moments written from the owner's answers; `review.jsonl` stays frozen. A listening
moment without a prediction (outside the pack, or its output invalid) is skipped and counted
(`no_prediction`), never scored as the model's "ok". The note's gates need operational readings, written in the module and
pre-registered with this commit: a real error is a BAD label, flagged by any BAD verdict (`unsure`
flags nothing, nor does a missing or invalid output); label severity ok 0 / pile 1 / visible 2;
every gate must hold in every one of the three shuffled runs; precision@3 pooled over songs with ≥ 3
real errors; M3 recalls a visible moment with any visible-class verdict; M4 is a share (≥ 19/20);
M5 agreement is the share of labelled moments with one verdict in every run.

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
- Japanese lyrics matched nothing until `audit.kana` (2026-11): the MMS ear is romanised, the lyrics
  are in script. CJK glyphs still draw as boxes in the pictures.
- Most finder false positives are a voice "return" with no ear letters at all: wordless outros,
  instrument figures leaking into the stem, breaths.

Findings and numbers: `docs/research/perceptual-audit-2026-11.md`.
