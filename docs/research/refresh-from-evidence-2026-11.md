# Refresh without download: keep what the model heard, not the audio (2026-11-15/16)

Every timing release so far had to rebuild stored documents by running the whole job again:
download, separation, three alignment passes, then the rules. The archive (~307 songs) therefore
followed a release only in part ("refresh 20-40 songs"), and each refresh paid YouTube's weather
(two of 36 downloads answered 403 on 2026-11-14; videos disappear). This note maps what a document
is made from, measures two ways of keeping enough of it to rebuild the document without the audio,
records the owner's decisions (2026-11-16), and describes what was built behind
`settings.evidence_mode` (off by default) and how it was proven. Working files, scripts and every
number below: `kashi-gozden-gecirme/indirmesiz-yenileme/` (outside the repository). Measured on a
4-core machine at 2 threads, on the 39 pilot and hold-out songs (7 423 s of audio, 190 s a song),
with the harness stems and emission caches.

## Short answer

- **A document reads the audio in six places** (download, separation, the aligner's emission
  passes, the stem's onsets, the mix features, the download's duration); everything this month's
  releases changed (votes, guards, carries, line QA) is code over what those places returned.
  Emissions are text-independent: a window's emissions depend on the stem, the checkpoint and its
  revision, and the slice's sample range, never on the lyrics.
- **Keeping the evidence** (the emission arrays a job read, the onsets, the mix features, the
  download's duration): **1.99 MB a song**, lossless (0.61 GB for 307 songs); a rebuild costs
  **1.7 s of one CPU** a song, no GPU, no model weights, no download; the rebuilt document is
  **byte-identical to a full run, 39 of 39**, through the worker's own `process_job`. It survives
  any rule change after the emissions; a release that cuts the windows differently, or changes the
  aligner or the separator, sends the affected songs through a full job, which records again. Of
  the four release steps from 2.31.0 to the 2.35.0 candidate, three could have replayed all 39
  songs; 2.34.0 re-cut windows in 2 of 39 (99.2 % of the slices were still held).
- **Keeping the vocal stem** instead (16 kHz mono FLAC, the aligner's exact input): 3.92 MB a song,
  and every refresh redoes every emission pass (163-533 s of CPU a song here, ~16 min on the cluster
  worker, ~40 s on the GPU helper), not exactly (a recompute differs in the last bits; across
  devices word starts move by "a frame or two", `docs/gpu-worker.md`), and it keeps a derivative of
  the audio, which the server has always promised not to.
- **Decided by the owner, 2026-11-16:** (1) no audio derivative is stored — evidence only (the
  measured comparison below is the record of why); (2) evidence lives in the deployment's own
  database, in `track_evidence`, and the nightly logical dump leaves that table's DATA out;
  (3) the archive gets its evidence from the GPU helper with the back-fill command.

## What a document is made from

The worker's job (`worker/process.py`, `pipeline/*`) for a plain r = 1 song with a stamped lrclib
record, in order. "Release?" = can a timing release change this stage's output.

| # | stage | reads | cost (here, 2 threads) | release? |
|---|---|---|---|---|
| 1 | lyrics lookup (override, lrclib ladder, mirror) | job hints/options, `song_overrides`, lrclib | network, < 1 s | ladder changes only (`LOOKUP_LADDER_VERSION`) |
| 2 | download (yt-dlp) | YouTube | median 5.3 s; 403s, removals | no |
| 3 | client-edit gate, nightcore detection | hints, download duration, lrclib search | < 1 s | rarely |
| 4 | separation (Kim MelBand) | the mix | median 669 s on CPU; far less on the GPU helper | no (model is config) |
| 5 | decode to 16 kHz mono | the stem | < 1 s | no |
| 6 | sheet shift's ear (`heard_tokens`) | whole-track emission pass + greedy decode | one whole pass: 54-199 s | the shift rule yes, the emissions no |
| 7 | sheet shift | tokens, stamps | ms | yes (2.29.1, 2.30.1, 2.34.0) |
| 8 | window plan | line texts, shifted stamps, track length | ms | yes, through 7 or its own rules |
| 9 | window emissions, one forward pass per slice | the 16 kHz stem | 50-130 s | no; which slices exist follows 8 |
| 10 | two Viterbi passes per window, V1 line regions | emissions, texts | ~1 s a song | yes |
| 11 | hybrid, seams, the vote (its whole-track pass = 6's), order guard, unpile, regroup, offsets | 10's rows, the whole-track emissions | ms | yes |
| 12 | line QA | aligned result, stamps, **onsets of the stem** (librosa) | onsets 1-2 s | rules yes, onsets no |
| 13 | nightcore gate and rescale | result, speed factor | ms | rarely |
| 14 | beats, energy, sections | **the mix** (librosa) | a few s | no (unchanged since 2.12.0) |
| 15 | fx tags and selection | text, sections | ms | lexicon only |
| 16 | palette | artwork URL | < 1 s | no |
| 17 | document, validation, persist guard | all of the above | ms | yes |

The smallest set from which a later pipeline version can rebuild the document exactly: the
emission arrays of 6 and 9 (keyed by checkpoint, pinned revision and sample range), the onsets, the
16 kHz track's length and digest, the mix features, the download's duration / bitrate / codec, and
— for stamped lyrics with a record length — the aligner wav's duration. Everything else is
recomputed exactly as a full job does, the lyrics lookup included (it needs no audio).

Two side findings. The whole-track emission pass runs **twice** in every job (6 and the vote), on
the same wav with the same model, and the two arrays are bitwise identical: about 37 % of a job's
emission time is a copy (the recorder serves the second pass from the first). And the onset
detector, beats, energy and structure swallow their own errors and return None, so a refactor that
hands them a path they cannot read changes documents silently.

## The two designs, measured

| | evidence (built) | vocal stem (declined) |
|---|---|---|
| stored a song | 1.99 MB lossless (median 2.08, max 2.57) + 16 kB meta | 3.92 MB FLAC, lossless (PCM equal 39/39) |
| 307 songs | 0.61 GB | 1.20 GB |
| lossy variant | float16 emissions 0.85 MB (not run through documents) | Opus 24 kbps 0.61 MB |
| refresh cost a song | 1.7 s of one CPU, tokenizer only | every emission pass: 163-533 s wall, 286-1 007 s CPU; peak RSS 4.9-7.4 GB |
| 307 songs | ~9 min CPU | ~14-45 h on 2 CPU threads by language mix; ~82 h on the cluster worker at 16 min a song; ~3.4 h on the GPU helper |
| exact against the stored document | yes, 39/39 byte-identical (`generated_at` aside) | no: last-bit differences, a line score moved in the 4th decimal on 3 songs, "a frame or two" across devices |
| survives a rule change after the emissions | yes | yes |
| survives a changed lyrics record | if the window slices stay | yes |
| survives a window-plan change | the songs whose slices stay | yes |
| survives an aligner checkpoint / revision change | no | yes (needs the model, GPU or hours) |
| survives a separator change | no | no |
| stores a derivative of the audio | no (log-probabilities over the aligner's alphabet, 50 frames a second) | yes |

Sizes: the emission arrays a 2.34.0 run reads on the 39 songs, encoded by byte planes then zlib
(plain zlib 2.29 MB, raw float32 2.74 MB); FLAC of the 39 harness stems. The stem's cost: three
songs with the real models and no cache (Turkish 300M, 193 s of audio, 214 s wall; English XLS-R
1B, 213 s, 533 s; MMS, 122 s, 163 s), emissions 95 % of the time.

**Survival over this month's releases.** Each release tree from 2.30.0 to the 2.35.0 candidate ran
the 39 songs with an emission pass that only records which slices it is asked for:

| release step | songs replayable | slices found in the earlier record | songs that re-cut windows |
|---|---|---|---|
| 2.30.0 -> 2.31.0 | 37 / 39 | 1 098 / 1 143 | 2 (aligned without windows in 2.30.0) |
| 2.31.0 -> 2.32.0 | 39 / 39 | 1 143 / 1 143 | - |
| 2.32.0 -> 2.33.0 | 39 / 39 | 1 143 / 1 143 | - |
| 2.33.0 -> 2.34.0 | 37 / 39 | 1 134 / 1 143 | 2 (1 and 8 slices) |
| 2.34.0 -> 2.35.0 candidate | 39 / 39 | 1 143 / 1 143 | - |

The 2.35.0 candidate reads per-letter log-probabilities from all three passes — what evidence
keeps and a store of aligned spans could not have served.

## Where it lives

`track_evidence` in the deployment's database (migration `a4e7c19d2b58`, additive): one row per
song (`source_type`, `source_id` unique), `key` and `meta` as JSONB, the arrays as one `bytea`
column (`STORAGE EXTERNAL`: they arrive compressed) that the ORM defers. The database is the one
store both workers already reach (the GPU helper through its database connection), and uploads
already live there as `bytea`.

Measured on the 39 stored rows: 1.99 MB of arrays and ~16 kB of meta a song; 1.04x on disk;
`pg_dump` writes `bytea` as hex (2x), gzip -9 brings it to 1.14x — **for 307 songs every nightly
logical dump grows by ~0.69 GB**, every one of them kept. Evidence is a cache (losing it costs
downloads, never a wrong document), so the nightly logical dump must leave the table's DATA out:
`pg_dump --exclude-table-data=track_evidence`, **not** `--exclude-table`, or a restore has the
Alembic revision that created the table without the table, and every worker built at that
revision fails on its first job. (The owner's dump change: the app database is excluded from the
cluster-wide dump and dumped on its own with `--create --exclude-table-data=track_evidence`; a
restore with the unchanged command has the table, empty.)

## The refresh path

`settings.evidence_mode`, an operational field (a replay builds what a full job builds):

- **off** (default): no session, no store access; the job is today's, byte for byte.
- **record**: every YouTube job runs in full under a `Recorder`; when it completes, its evidence is
  stored (best effort, after the job's own commit). Never stored: a recording that saw two
  different tracks (a second-pass separation), a nightcore clock, an unpinned checkpoint, a key
  library without a version, a failed extractor ("found nothing" is a value, a failure is not), a
  fault in the recorder's own bookkeeping, or a job that never aligned. A row's slices from earlier
  window plans of the same audio, key, checkpoint and revision are kept, up to twice the new
  record's size.
- **replay**: the song's key and meta are read first, the arrays only for that version of the row
  (a writer in between makes the replay stop). It is replayed when its song-wide key equals what
  this worker would record for this job — evidence format; the version of the code that computed
  each value (`EVIDENCE_CODE`: emissions, onsets, mix, stem, each pinned to a hash of its source by
  a test); the separator (mode, model, mixback) and whether this job separates first; the
  structure flag; the aligner library's commit, librosa, the separator library and the ffmpeg
  build — and it holds everything every replay reads. torch, transformers, numpy and yt-dlp are
  provenance only (the GPU helper records, the CPU worker replays and serves the recorder's own
  arrays; yt-dlp is bumped whenever YouTube breaks it). Per emission pass the replayer needs an
  entry with the same checkpoint, pinned revision and sample range. YouTube songs only: an upload
  is neither recorded nor replayed (re-uploading is how an upload is processed again).
- **Audio identity.** A replay compares the job's credible duration hint with the hint the record
  was made under (or, without one, with the recorded download's duration); more than 3 s apart, the
  job runs in full and records again. It catches a job whose player reports another length (an
  admin reprocess with corrected hints); it cannot see audio swapped behind the id while the hints
  stay the same (a reprocess reuses the latest job's hints): `options.fresh_audio` on an admin
  reprocess fetches again, and rows have no age limit.
- **Fallback.** Anything a replay cannot serve raises `EvidenceMissing`; it, a non-transient
  pipeline error or any other fault rolls the replay back and runs the job in full (which
  records); cancels, lost leases, database errors and transient errors propagate as in a full job.
  Every stop happens before the persist, the replay's last step. A stated nightcore clock skips the
  replay (it would stop after the branch's lrclib searches).
- **Overrides and the persist guard** are untouched: a replay runs the same code, applies the
  song's override from the job's options and meets the same guard.

**The back-fill command** (`python -m kashi_server.worker.backfill`, `docs/gpu-worker.md`): the
worker's own job code on an in-memory job built as a reprocess builds one, with the document built
and validated but not stored, and nothing written to `jobs` — so neither a worker that cannot record
nor an older image can pick the work up and replace a stored document (a first version carried it
as a reprocess option; the review showed both failure modes, and the API now refuses that option).
Songs go in source-id order after the previous run's cursor, so a song that always fails cannot
hold the batch; a JSON summary and the exit code say what happened; songs that can never be
recorded are not taken. Its scratch directories are named like the worker's, swept at its start
(one run at a time, under a lock), and SIGTERM unwinds the song so its audio is deleted.

**Order of use.** (1) Merged with the mode off: the table exists and stays empty, nothing else
changes. (2) The nightly dump leaves the table's data out — before `record` is ever on. (3) The GPU
helper, built at the tag the cluster runs, back-fills the archive a few dozen songs at a time.
(4) First recorded song: the helper's log says `evidence stored (... emission arrays, ... bytes)`;
a reprocess of that song on a cluster worker in `replay` logs `rebuilt from stored evidence, no
download` and does not log `key differs at …` (a key difference there means the two images differ
in a key library or setting). (5) Then `replay` on the workers: a release that changes only rules
refreshes the whole archive in minutes of CPU; songs whose evidence no longer matches fall back one
by one and record again.

## What was built and how it was proven

`pipeline/evidence.py` (recorder, replayer, stores, key, encoding, one hook per audio boundary,
each a pass-through without a session), the hooks in `alignment.py` and `worker/process.py`,
`worker/backfill.py`, `settings.evidence_mode`, `TrackEvidence` and its migration,
`ReprocessOptions.fresh_audio`, `extractor_failed` in the four extractors. No pipeline version
change: documents do not change.

- **Off is byte-identical:** main and the branch with the mode off through the worker's own
  `process_job` against a throwaway Postgres 17, 39 songs (the stem as download and as mix,
  emissions from the harness caches, a fake lrclib serving each song's record): 39/39 equal.
- **Replay equals a full run:** off == record == replay, 39/39, with every audio reader made to
  raise in the replay arm and the replay's own tokenizer-only model path running.
- **The back-fill:** with the evidence emptied, the command recorded 39/39 with no job added and
  every stored document byte-identical before and after; replays from those records equal off.
- **Two independent reviews** of the code (2026-11-16) found nine, then ten things between the
  prototype and switching a mode on — the memo answering a second-pass stem from the mix's arrays,
  the key not naming the job's own separation, unpinned checkpoints, recorded extractor failures,
  uploads, the stem code outside the pin, the missing identity check, the queued back-fill job,
  scratch left by a killed back-fill, a cursor-less candidate list, a value required that some
  jobs never read, the identity check's loop, torn reads, recorder faults failing jobs. Each fix
  came with a test that fails without it. One-line mutations of the new code (every earlier one
  kept, one for each fix): 96 of 96 make the tests fail; the one that first survived (the store
  without its "job completed" check, hidden by the missing-values check) got its own test.
- **Final proof** on the merged code: main == off == record == replay, 39/39 (replay 1.74 s a song,
  77.5 MB of evidence); then, with the evidence emptied, the back-fill command stored 39 of 39, added
  no job and left every stored document byte-identical, and the replay from its records equals off,
  39/39.
- **Gate:** pytest (DB tests against a throwaway Postgres 17), ruff, pyright and check-versions,
  all clean; no TypeScript, schema or codegen change.

## Addendum: the stem's level envelope (2026-11-16, EVIDENCE_FORMAT 4)

A rule that asks "is the voice silent here?" needs the stem, which a replay never reads; so the
envelope became evidence before any rule read it (`pipeline/level.py`, EVIDENCE_CODE `envelope` 2;
format 3 / code 1 was its first draft, never deployed).

- **Definition.** The aligner's own 16 kHz mono int16 wav (on the 39 songs `wave` reads exactly the
  samples the aligner's `load_audio` hands the model: same count, same values), 320-sample (20 ms)
  frames from sample 0, the last frame over the samples it has. Per frame, one byte each:
  - the mean square in half-dB steps under the loudest frame, rounded half up (0 = the loudest,
    255 = 127.25 dB under it or quieter, digital silence included);
  - the zero-crossing count over the pairs of neighbouring samples inside the frame (a sample of 0
    counts as non-negative), capped at 255 (a full frame has at most 319).
  Per track, the loudest frame's mean square as an exact fraction (sum of squared samples, sample
  count), which puts the levels back on a dBFS scale.
- **Exact.** Integer sums and counts; a frame's step is found by comparing its mean square, as an
  exact fraction of the loudest, with a fixed table of 255 integer edges
  (`floor(10^(-(j + 0.5)/20) · 2^96)`, computed with the decimal module, whose ln and exp are
  correctly rounded in software). No floating point decides a byte, so every worker produces the
  same bytes; tests pin the table and the bytes of a synthetic signal. The first draft's float
  quantizer gave the same `levels` on the pinned signal and on all 39 songs, byte for byte.
- **Not audio.** 16 bits per 320 samples, no phase, no spectrum; the crossing count is one coarse
  brightness cue (fundamental, formants and noise mixed), no pitch track. Kept for the voiced class
  a later rule may take on; nothing reads it, nor the loudest frame, yet.
- **Recorded** in the evidence meta as `{step_ms, unit_db, frames, levels, crossings, loudest}`
  (byte strings zlib + base64), in REQUIRED_META. A replay serves it only when both byte strings
  decode to `frames` bytes and `frames == ceil(track_samples / 320)`: an envelope of another wav would
  put a rule's silence on another clock. An envelope that failed (the wav not the aligner's format,
  unreadable, a short read) is reported with `extractor_failed` and the job records nothing.
- **Memory.** The wav is read 1 024 frames (20.5 s) at a time; the first draft held the whole track
  as 64-bit samples (~9x the wav, ~350 MB at the 20-minute cap).
- **Size:** 9 518 frames a song on the 39 (190 s); stored, the levels take 8.6 kB and the
  crossings 10.0 kB a song (max 24.6 kB together): about 0.9 % of a record.
- **Proof** (39 songs, worker `process_job`, throwaway Postgres): main 24b1e7f == the branch with
  the mode off == record == replay, 39/39; every replay rebuilt from evidence with every audio
  reader, `level_envelope` included, made to raise; every stored envelope has
  `frames == ceil(track_samples / 320)`. One-line mutations of the
  code (step edges, the tie rule, the loudest frame and its ties, frame sizes, the tail frame, the
  crossing sign convention and cap, the short read, the dBFS scale, share boundaries, the format
  check, the failure report, the replay's frame and track checks, REQUIRED_META, the version pin,
  the recorder, the hand-over to the aligner, the second pass's own envelope): 33 of 33 make the
  tests fail (the source-hash pin excluded from every one but its own; one, the tail frame's zero,
  after its test was added).

## Preview before refresh (2026-11-16)

A refresh replaces stored documents, and until now the only place a candidate's documents were
compared with the stored ones beforehand was the archive rig on the owner's desktop (287 songs from
its cache). With evidence in the database the same comparison runs where the evidence is, on every
recorded song, and writes nothing: `python -m kashi_server.worker.preview` (`worker/preview.py`,
rules in `worker/preview_rules.py`; how to run it: `docs/gpu-worker.md`).

- **What it builds.** For each YouTube song with a stored document and evidence (or the songs
  given): the worker's own job code under a replayer of the song's evidence, whatever
  `evidence_mode` says, on an in-memory job built as a reprocess builds one (the latest job's hints,
  the song's override, as the back-fill does): the document a replayed reprocess by this image
  would store.
- **What it writes: nothing.** Every read goes through READ ONLY transactions (the database refuses
  a write); the job code gets a session that reaches no database (the in-memory job's heartbeats
  are answered as touching no row, anything else raises); no job row, document, evidence or
  override bookkeeping. The persist guard's question is asked with `stored_document_kept`, the
  decision `persist_processed_track` now acts on (one copy of the rules). The job code's counters
  count in the command's own process, which serves no metrics.
- **No fallback.** Whatever the evidence cannot serve (none, a key that differs, a nightcore clock,
  a player length more than 3 s from the record's, a slice another window plan cuts, the by-ear
  rung, a non-transient error or a fault inside the replay) is `needs a full job: <reason>`; nothing
  is downloaded, separated or aligned.
- **Lyrics, in two passes.** `--lyrics stored` (default) asks lrclib for the one record the stored
  document names, by id: the release is measured on the same sheet. A reprocess runs the lookup
  ladder, though, and the ladder may pick another record today, or miss and need the by-ear rung.
  So every song that comes out `refresh` this way is previewed again with the ladder, exactly as
  its reprocess will run, and stays `refresh` only when the ladder builds the same document on the
  same record; otherwise its verdict is the ladder's (another record: `sheet changed`; the by-ear
  rung: `needs a full job`; lrclib weather: `failed`), or `review` when the ladder says refresh
  with another document. `refresh_ids` come from the confirmed songs only. `--lyrics ladder` runs
  the ladder for every song. An override's lyrics are always the override's. There is no preview
  without a request: the stamps that cut the windows and judge line QA live only in the record.
- **Requests.** Stored mode: one by-id request a song; a title with a nightcore marker adds the
  detection's search (one, or two when the first finds nothing plausible) and, when it finds a
  factor, a second by-id (then the song needs a full job). Each `refresh` adds its ladder pass:
  1-5 requests (the exact get; a search when the get's record is another edit; when nothing is
  found a search, a free-text search and, for a multi-artist hint, both again with the primary
  artist), up to 5 more when the composite-title fallback runs the ladder again, plus the marker
  searches again. `--lyrics ladder`: that ladder pass for every song. lrclib.net requests are at
  least 0.5 s apart (`--lrclib-gap-s`; lrclib asks 200-500 ms in batch work); a busy lrclib.net
  sends them to the mirror through the worker's breaker.
- **What it reports**, per song and numbers or fixed reasons only (line indexes, times, error
  classes; never lyric text, a title or an exception's message, which go to the log): the pipeline
  versions; which document fields differ; the sheet (lyrics source, record, speed factor, line
  count, line texts; an absent speed factor reads 1.0, and stored text whose inline time tags
  stripped equals the new text is the same line); line and word counts, wordless lines, words
  starting before the word before them, lines ending after the next starts, words whose successor
  in the line starts 0 ms / < 60 ms / < 120 ms after them (the 2.34.0 refresh's definitions); line
  starts moved > 100 / 500 / 1 500 ms with the largest, and for each moved start the silent lead
  before and after it (ms to the first 20 ms envelope frame at or above -25 dB re the loudest
  frame, `alignment.FIRST_HEARD_SILENCE_DB`); lines that lost or gained word timings; W1-W4 and
  the moves W2 cannot judge. The summary: counts per verdict, the ids to refresh, why the others
  are not, the vetoes, the counts summed over the songs judged on the same sheet, what a worker
  needs for the reprocesses to replay (`replay`: the mode and the evidence key this image records
  and replays, and this environment's mode), the lrclib requests, the peak memory, the exit code.

**The verdict, first match wins.** Rules in the code; thresholds the archive rig's vetoes, written
before the 2.34.0 and 2.35.0 runs, not tuned here:

1. `failed`: the preview could not tell (lrclib weather, no stored document, no job history, the
   stored document's record gone, evidence replaced while it was read).
2. `needs a full job: <reason>` (above).
3. `keep (guard: ...)`: the persist guard would keep the stored document (a newer pipeline's, an
   override's build this run does not apply, a regression).
4. `sheet changed`: reported, never judged by the timing measures (they pair lines by index).
5. `keep (veto: ...)`, any of:
   W1 a new run of >= 4 words of one line, each starting < 60 ms after the word before it, that no
   run of the stored line contains;
   W2 a line start more than 1 500 ms farther from where the voice returns than the stored start;
   W3 a line with text that had word timings and has none;
   W4 a moved line (start more than 100 ms away) whose first word now lights < 120 ms before its
   second word and lit >= 120 ms before.
6. `review (...)`: for a person, out of `refresh_ids`: a line start moved more than 1 500 ms that
   W2 cannot judge (below), or a rebuilt palette that is the default while the stored one was read
   off the album art (the palette is fetched live, it is not evidence: a failed artwork fetch would
   overwrite the album's colours).
7. `unchanged`: the document differs only in `generated_at` and `pipeline_version` (a reprocess
   would only restamp it: not listed for refresh).
8. `refresh` (in stored mode: confirmed by the ladder).

**W2's witness is the envelope, and what it can judge.** The rig's W2 is the audit voice witness:
a line that follows >= 500 ms of document silence is placed on the voice's return nearest its first
word when that return is within 1.5 s, the voice returning where the stem's level reaches -25 dB re
its loudest frame after >= 150 ms under it. The preview applies that definition to the stored
envelope (20 ms mean-square frames) instead of the audit's contour (librosa RMS over 128 ms windows
every 16 ms, read by tools that are not in the image); tests check both steps against the audit's
own code on the same frames. W2 judges only the lines the STORED document lets the witness place:
a reference taken on the new document lies within 1.5 s of the new start, so it can never put that
start more than 1.5 s farther away; the first line, and every line inside continuous singing, are
never placed. A move of more than 1.5 s anywhere else is therefore `review`, listing the lines.
Not chosen: "a start moved more than 1.5 s whose silent lead grows", which cannot see a start
moved late past the voice's return (its lead stays 0). Caveat: the 2.35.0 rule moves a first word
only out of silence on this same envelope at this same line, so W2 cannot argue with that rule's
own moves; it watches every other move (carries, sheet shifts, regroups).

**Proof** (the 39 pilot and hold-out songs through the worker's `process_job` against a throwaway
Postgres 17; working files `kashi-gozden-gecirme/onizleme/`). Evidence recorded, then every stored
document replaced by the 2.34.0 build's, one song's line 5 moved 300 ms more. The preview of all 39
(evidence mode off, every audio reader made to raise) took 67 s, 1.7 s a song, at a peak of 542 MB
(the harness's process, torch loaded), and left nine tables identical (row counts and content
hashes). Its verdicts: the seeded song `refresh`, confirmed by its ladder pass on the same record;
GOSSIP `review`: lines 0 and 38, the 2.35.0 rule's two moves (1 950 and 6 770 ms; silent leads
1 690 -> 0 and 6 240 -> 10 ms), lie outside W2's reach (the first line is never placed, line 38
only on the new document); 37 `unchanged`. Stored mode asked 39 records by id and one ladder;
ladder mode asked 39 ladders and gave the same verdicts. The worker's own replay of each song then
stored a document equal to the preview's, 39 of 39 (`generated_at` aside), in both modes. Unit
tests put every threshold exactly on its value and one millisecond (or one step) either side;
one-line mutations, first round 69 of 70 and second round 46 of 47 make a test fail, the two others
equivalent (the witness's first silence starting at 0 ms or at the first frame, which starts at
0 ms; "the same record" next to "the same document", which carries the record). The persist
guard's extraction equals main's `persist_processed_track` on 26 040 seeded and boundary cases
(statements, counters, log lines), and that harness reports each of four one-line mutations of the
new guard. On real archive documents the sheet comparison finds the same sheet on the 36 songs
refreshed from 2.31.0 to 2.34.0 on 2026-11-14, 36 of 36 (one carried inline tags), and none of 277
archive documents (2.31.0 and older) lacks a field it reads.

**What it cannot see.** Songs without evidence (not back-filled, a nightcore clock, uploads) and
releases that change what is read off the audio (the aligner, its revision, the separator, a window
plan that cuts new slices: those songs read `needs a full job`); audio swapped behind an id with
unchanged hints. The audit's own voice contour and the ear witness, human word times (the bench)
and the owner's ear (clips). It previews the code it runs: a candidate is previewed by running the
candidate's image with the deployment's database and model cache, never by the deployed worker.
And whether a reprocess will replay at all: that is the worker's mode and image (the summary says
what they must be; the canary below checks it).

**The release order with it.** (1) The archive step runs in the cluster on every song with
evidence, before any document changes: the candidate image's preview gives the per-song vetoes, the
`review` list for a person and, summed over the songs on the same sheet, the counts the rig's
aggregate bars read. (2) The desktop still runs what the preview cannot: the independent voice and
ear witnesses' bars, candidates that change the emissions, (3) human truth with the bench in
shipped mode, and (4) at most eight blind clips; then (5) the reviewer and the tag. After the
rollout: the deployed image's preview lists the ids to refresh; the canary, one of them reprocessed
on a worker in `replay`, must log `rebuilt from stored evidence, no download` before the rest are
fed; each reprocess then stores the document previewed, unless the ladder's answer or the song's
override changes in between.

## The aligner library's limits: the back-fill's `AssertionError: n != i` (2026-11-16)

One back-fill song failed inside its job with `AssertionError: n != i` (no traceback kept). The
assertion is ctc-forced-aligner's own (`get_spans`, at the pinned commit): the Viterbi path's
letter (`n`) is not the text's next letter (`i`), i.e. the path does not spell the text. The text
route is closed (the library drops characters its vocabulary lacks from the targets but not from
`get_spans`, and `_fit_to_vocab` and uroman leave none). Measured on the compiled library with
synthetic log-probabilities (working files `onizleme/`), three inputs break it:

- fewer frames than the targets need (T < L + R, R the repeated neighbours): it raises "targets
  length is too long for CTC" (an earlier archive song's failure);
- targets with no repeated neighbour (R = 0) and T < 2L - 2: its back-pointer buffers, (2L + 2)
  (T - L) bits, are smaller than what its band writes (its own bookkeeping, swept exactly). Deep
  overruns crash or break the path (25 samples at T = L: 15 crashes, 9 paths that do not spell the
  text, `y != t`; at 1.3 L: 17 / 5 / 3 valid), shallow ones mostly come back valid (1.9 L: 3 crashes,
  22 valid); at T = L (the exact fit) it segfaults every time without a repeat;
- a NaN frame: 40 % of the paths do not spell the text, in this message's shape.

6cvfHn9qeEw cannot be settled from here: a dense window without a doubled letter (the second case)
is the likeliest; it is deterministic for the same window plan, text and emissions, so an ordinary
reprocess on the same image would most likely meet it again, and on the CPU worker it may crash
the process instead. Since this change `alignment._viterbi` refuses, before the library runs,
T < L + R, the exact fit without a repeat and non-finite emissions, and turns `get_spans`'
AssertionError into the same error: `alignment_failed` (permanent, like the job's other alignment
dead ends; a new record or a code change, both through a reprocess, is what helps), naming the
window's sample range, the pass, T, L and R. The wider overrun is logged, not refused: a pilot song
(k89YEt8hocI, window 21, segment pass, T = 286, L = 147) sits in it and its stored document came
from that pass; refusing the region failed its job in the first draft. Proven: main == the branch
(off, record, replay) on the 39 songs, every job completed, 39 replays rebuilt; the real library
on every boundary (refused at L + R - 1, at the exact fit without a repeat where it segfaults, at
a NaN frame; aligned like before at L + R with a repeat and L + R + 1); mutations of the guard all
caught. Next time, the error message itself carries the window, the pass, T, L and R.

## What is left

- The preview's first archive run (after the back-fill; then a candidate image's preview before the
  next timing release).
- A reprocess overwrites a stored album-art palette with the default when the artwork fetch fails
  (the preview reports it as `review`); the persist guard could keep the stored one.
- The aligner library's buffer overrun without a repeated letter (logged now): refuse it, or align
  such windows another way, once the log says how often it happens.
- The back-fill on the GPU helper is under way (180 songs recorded by the evening of 2026-11-16);
  a cluster replay of what it recorded (checks above) and a worker metric for replays and
  fallbacks have not run.
- Nightcore songs are never recorded (their clock would have to join the key); uploads wait for
  the owner.
- An audio swap behind an id with unchanged hints is caught only by `fresh_audio`.
