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

## What is left

- The back-fill on the GPU helper and a cluster replay of what it recorded have not run (checks
  above); a worker metric for replays and fallbacks.
- Nightcore songs are never recorded (their clock would have to join the key); uploads wait for
  the owner.
- An audio swap behind an id with unchanged hints is caught only by `fresh_audio`.
