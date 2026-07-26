# Research index

This directory holds Kashi's permanent measurement and research records: spike
results, benchmark analyses, calibration rounds and design memos. Each file is
the *reason* behind a decision — why a dependency was rejected, why a default
flipped, why a feature was built one way and not another. Code and plans move
on; these records stay so a later session (or a later Caner) does not have to
re-derive an answer that was already paid for once.

| Report | Question it answers | Verdict / status | Date |
|---|---|---|---|
| [allin1-viability-2026-07.md](allin1-viability-2026-07.md) | Can the `allin1` music-structure stack be installed and used for section detection? | **NO-GO** — dead on arrival (NATTEN ≥0.20 removed the API it needs, upstreams frozen); P6 pivoted to dependency-free librosa Laplacian segmentation. Includes a supply-chain warning about the `openmirlab` `-infer` forks. | 2026-07-20 |
| [bad-girl-sections-2026-07.md](bad-girl-sections-2026-07.md) | Why does BAD GIRL (UJ6mMotRd1M) produce zero energy sections? | **Diagnosed** — not a bug: a brickwalled master saturates the normalized energy envelope, so the P70 threshold lands inside the plateau's noise band. Accepted as a v1 limit of the energy proxy; the real fix is the P6 structure work (this track is its acceptance case). | 2026-07-19 |
| [embed-threshold-calibration-2026-07.md](embed-threshold-calibration-2026-07.md) | Which `EMBED_THRESHOLD` makes the embedding line-theme layer trustworthy? | **Decided — the layer defaults OFF** (`fx_embeddings` off from pipeline 2.9.0). No threshold reaches the ≤5% false-positive bar: precision plateaus near 24% strict / ~50% lenient and does not improve with the score. Threshold moved to 0.90/0.90 as a harm-reduction floor for anyone who enables it anyway. | 2026-07-20 |
| [fx-layer-spike-2026-07.md](fx-layer-spike-2026-07.md) | Can a transparent, always-on-top, click-through WebGL particle window hold its perf/durability gate on Windows and macOS? | **GO** — both platforms pass decisively (p95 7.1 ms Win / 9.1 ms macOS against a 16.7 ms gate; CPU ~3%; no leak; sleep/wake survived). P7, the separate effect-layer window, is technically unblocked; particle count is nearly free at this scale and battery, not framerate, is the real constraint. | 2026-07-25 |
| [hizalama-v2-benchmark-2026-07.md](hizalama-v2-benchmark-2026-07.md) | For alignment v2: where do the timing errors actually come from, which separation model wins, and does windowed alignment work? | **Decided + acceptance met** — `kim-melband` becomes the separation default, `separation_mixback` → 0 (mixback measurably hurts), windowed alignment clears the bar (word MAE 191 ms, PCO@0.3 91.5%) and windowed documents score by lrclib-anchor agreement instead of the CTC-prob ramp. MMS-CTC stays the backbone. Also carries the eval-hardening backlog. | 2026-07-11 (decisions 07-12) |
| [runtime-llm-tagging-memo.md](runtime-llm-tagging-memo.md) | Should an LLM tag uncovered lyric lines at ingest time, as a third fx layer? | **Design only — not built.** Blocked on determinism (the document contract promises byte-identical fx across runs) and operational surface (network dependency + API key in an all-local, best-effort pipeline). Full design sketch and explicit revisit triggers are recorded for a later phase. | 2026-07-19 |
| [soft-offset-spike-2026-07.md](soft-offset-spike-2026-07.md) | Does shifting each line rigidly toward its lrclib anchor recover word-level precision? | **NO-GO** — measured −2.4 points PCO@0.3 at realistic anchor jitter against a +5 requirement; at j500 it injects anchor noise straight into word timings. P8 (productionize) dropped; the bench harness stays for future re-runs. | 2026-07-18 |
| [telemetry-6.7-sketch.md](telemetry-6.7-sketch.md) | What would client→server diagnostic telemetry look like, and what causes the "wrong timing, fixed by a YTM refresh" bug? | **Idea / design input — not built** (candidate scope for Faz 6.7). The bundled bug investigation *did* land a root cause: the asymmetric position-staleness gap (duration is guarded at a track switch, position is not) amplified by gapless playback under YTM Premium; three proposed extension guards are written up and the fix is folded into 6.7. | 2026-07-24 |
| [video-song-substitution-memo.md](video-song-substitution-memo.md) | Can the video-edit vs song-stream duration mismatch (the Sinsirella class) be fixed permanently? | **Analysis — no implementation; shipped behavior stays honest-fail + upload escape.** Client duration as a *selector* is a dead end; the real candidate is video→song id mapping via `musicVideoType` (Phase A) and `counterpart.videoId` (Phase B), both Faz 7 candidates pending live probes. | 2026-07-19 (Faz 6.5 P8 round) |
| [ytm-integration-2026-07.md](ytm-integration-2026-07.md) | Before building the extension: what is the real current state of YTM's DOM, the player API and MV3/LNA constraints? | **Verified — built on.** Confirmed MAIN-world mediaSession, the plain `<video>` element, `videodatachange` on `#movie_player` as the primary track-change signal, the static `world:"MAIN"` declaration, the 30 s alarms floor, and the load-bearing rule that the WS bridge must be opened only by the service worker. Corrects several stale baseline assumptions (ad selector, LNA regression, repo rename). | 2026-07-07 |

## Conventions

- **New report ⇒ new row.** Adding a file here without adding its row makes the
  index lie; the index is the entry point, not an optional extra.
- **Name reports with their date stamp** (`<topic>-YYYY-MM.md`, or a `-memo` /
  `-sketch` suffix for design-only documents) so ordering and staleness are
  visible from the filename alone.
- **Nothing is deleted when it goes stale.** A report that no longer holds keeps
  its file and gets its status changed to `superseded by <X>` (and, ideally, a
  one-line note at the top of the report itself). The wrong answer and the
  reason it was wrong are part of the record.
- **State the verdict the report actually reached** — GO / NO-GO / decided /
  design-only / superseded. If a report has no verdict, say so rather than
  inventing one.
