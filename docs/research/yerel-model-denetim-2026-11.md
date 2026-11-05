# Local models in the perceptual audit — research and decision (2026-11)

**Status: decided (option 1, 2026-11-03) — research only, no implementation, nothing measured here.** Sources were read on
2026-11-01; every number carries a source label (list at the end), and estimates are marked as such. The
question: can local models on the owner's GPU desktop work through the whole archive (~300 songs) without
spending tokens, summarise each song and point at what deserves a look — so that the agent reads only those
places and the owner listens only where the agent is unsure?

- **The most valuable piece needs no LLM.** The worst failures of the Faz 3 audit came from the lyrics
  source (`perceptual-audit-2026-11.md`). Detecting a shifted or wrong-version lrclib sheet is a known
  problem class with ready methods (text anchors plus a robust offset, activity correlation, coverage), and
  they run on emissions the aligner already computes (§8).
- **A better ear exists and is new: Qwen3-ASR** (Apache-2.0, January 2026), the only open model with a
  published word error rate on full songs with music. It transcribes without the lyrics, so it can see a
  sheet that is wrong (§1).
- **No local audio LLM can judge timing today.** The best open word-grounding score is 34.5 mIoU and no
  system counts repeated occurrences better than 13.2 % (§2).
- **The Claude API takes no audio.** It can be the summarizer of numbers, ear text, lyrics and pictures, not
  the listener (§7).
- **A local summarizer is unproven** for numeric judgement. The installed Gemma 4 26B-A4B gets a
  pre-registered A/B against Opus on the 96 labelled moments before it gets any role (§3, Faz 3.5).

## What the audit has today

- **Ear:** the production aligner's own CTC checkpoint (English XLS-R 1B, Turkish XLS-R 300M, MMS for the
  rest), greedy-decoded into timed letters (`apps/server/audit/ear.py`). For Turkish and the MMS languages its
  letter times equal the word boxes by construction; only the letters are independent evidence.
- **Ear-text match** (`audit.textmatch`): a line's letters slid along the ear's letter stream within ±8 s of
  the document's time. Letters are reduced to a-z, so Japanese script never matches. A ±30 s window caught the
  17-19 s case that ±8 s missed.
- **Finder, score, pictures:** the agent reads numbers first, the picture second (`apps/server/audit/README.md`).
- **Labelled data** (kept outside the repository): 96 moments (45 ok, 18 pile, 12 late, 6 early, 6 missing,
  4 wrong_text, 5 unsure); 8 blind owner answers (5 agree, 1 partial, 2 disagree); one song with 13
  human-timed lines (the ear-text match placed 11, 10 of them within ±190 ms, median +67 ms); the pop set's
  valid region (278 words); stems and whole-song ears for the 20 pilot songs.
- **Machines:** the GPU desktop (RTX 5070 Ti 16 GB, 32 GB RAM, Windows, Docker) runs manually started
  overnight batches (~8 h). Its local LLM server holds Gemma 4 26B-A4B QAT at 16k context with reasoning off
  and uses ~15.8 of 16.3 GB VRAM when loaded: **audio models and the LLM cannot be resident together.**
  Results are copied to cnr-intel.

## 1. A better ear

| candidate | licence | fits 16 GB | word times | singing evidence | tr / ja | verdict |
|---|---|---|---|---|---|---|
| **Qwen3-ASR-1.7B** (2026-01) | Apache-2.0 [qasr-card] | yes; ~2B params bf16, no official VRAM figure; a 4080 SUPER 16 GB community server runs it [qasr-srv] | none from the ASR; Qwen3-ForcedAligner-0.6B adds them (80 ms bins, 11 languages, no Turkish) [qasr-card] | card lists "Speech, Singing Voice, Songs with BGM"; WER M4Singer 5.98 (best other 7.29), Opencpop 3.08 (best other 2.98), full songs with music EntireSongs-en 14.60 (Gemini-2.5-Pro 12.18), -zh 13.91 [qasr-card]; Mandarin confirmation in VocalParse [vocalparse] | both; Japanese output in script | **shortlist #1** |
| parakeet-tdt-0.6b-v3 (2025-08) | CC-BY-4.0 [parakeet3] | yes, < 2 GB | native, from the TDT duration head; ms accuracy not published | none; the English v2 card lists "song lyrics transcription" without a number [parakeet2] | neither | #2 for en/es/de/fr; NeMo needs WSL2 on Windows [nemo-win] |
| Whisper large-v3-turbo via faster-whisper | MIT [fw] | ~6 GB fp16; INT8 disabled on Blackwell since CTranslate2 4.6.2 [ct2] | cross-attention DTW; never measured on singing | Jam-ALT: large-v3 WER 32.6 on the mix, **48.0 on Demucs vocals** ("isolated vocals tend to substantially degrade the results", except large-v2 English) [jamalt]; best open result large-**v2** on the mix with stem-derived RMS-VAD segments, 20.35 [syed25]; turbo unmeasured; hallucinates on instrumentals [lyricwhiz][whisper-ns] | both | #3; mix input only, audit-only |
| Voxtral Mini 3B / Mini 4B Realtime | Apache-2.0 | 9.5 GB / at the limit | open models have none (word timestamps are API-only) [voxtral2] | none | no Turkish | reject |
| Canary-1b-v2, Omnilingual ASR | CC-BY-4.0 / Apache-2.0 | yes | CTC + NFA / not documented | none | partial | reject: CTC/wav2vec2 family, not independent [canary][omniasr] |
| CrisperWhisper, SingAlign, madmom | non-commercial weights | — | — | — | — | allowed for internal eval only; not needed |

Context already measured in this repository: Qwen3-ForcedAligner windowed reaches PCO@0.3 0.812 (MAE 429 ms)
on 20 English songs, song-level correlation with MMS +0.483 (`hizalama-yontem-adaylari-2026-08.md`), and as a
production suspicion signal it raised 10.3 % false alarms against a 5 % bar (`olu-fikirler.md`).

**Why this is not the rejected "second opinion".** `olu-fikirler.md` rejects a weaker aligner and a
same-family model as a word-level suspicion signal. Qwen3-ASR has a different role on new evidence:
(i) it takes **no lyrics input**, so unlike a lyrics-conditioned aligner it can see a shifted or wrong sheet;
(ii) its target is the seconds- and verse-scale lyrics-source class (3 of the 20 pilot songs) and Japanese
script, not word timing; (iii) the evidence is new — the lyrics-source finding and the ±8 s blind spot of
Faz 3, and a model class released in 2026-01 with published singing WER. The Faz 8.1 verdict on word-level
second opinions stands unchanged.

**Time axis.** Qwen3-ASR returns text without times. Cut the stem at the finder's silences (≥ 500 ms below
−25 dB) into pieces of ≤ 30 s, transcribe each piece and stamp it with its start; the ear-text match then
reads a segment-timed stream (letter rate from the segment length). That resolves the ≥ 1 s class it is for.
Where Qwen3-ForcedAligner covers the language (en, ja, es, de, fr) word times can be added; Turkish stays
segment-level.

**Japanese.** The pilot set has one Japanese song, and the match reduces text to a-z. Script output needs a
script-aware matcher (both sides to kana; romaji lrclib records transliterated). Until there are at least
three Japanese songs with checked lines, Japanese is reported, not gated.

## 2. A listening judge (audio LLM)

| model | licence | fits 16 GB | timing evidence | verdict |
|---|---|---|---|---|
| Qwen3-Omni-30B-A3B (2025-09) | Apache-2.0 | no: AWQ 4-bit is 27.6 GB [qomni-awq] | word-timestamp shift 647 ms on LibriSpeech [moss] | strongest listener, does not fit |
| **MOSS-Audio-4B-Thinking** (2026-04) | Apache-2.0 [moss] | yes, ~4.6B params; VRAM not published | TAG-Bench word subset 24.3 mIoU [tagbench]; MOSS-4B-Instruct shift 358 ms (8B: 132 ms, does not fit) [moss] | best that fits; custom `infer.py` / SGLang only |
| FireRedAudio (2026-08) | Apache-2.0 [firered] | unclear, 9B LLM | best TAG-Bench: 31.2 overall, 34.5 word subset, 15.1 % parse failures [tagbench] | likely over 16 GB |
| Qwen2.5-Omni-7B (2025-03) | Apache-2.0 | yes as GGUF Q4_K_M, 4.68 GB, llama.cpp audio input [qomni25-gguf][llamacpp-mm] | zero-shot AudioGrounding mIoU 11.8 [timeaudio] | cheap control |
| Gemma 4 E4B / 12B (2026) | Apache-2.0 | yes | ≤ 30 s audio, trained for ASR and speech translation; no grounding or music numbers published [gemma4-card][gemma-audio] | no evidence. The installed 26B-A4B takes text and images only [gemma4-26b] |
| Audio Flamingo 3 | weights non-commercial [af3] | — | TAG-Bench 8.7 [tagbench] | eval-only, weak |
| Kimi-Audio, Step-Audio 2 mini, MiniCPM-o, Phi-4-multimodal, Voxtral | permissive | mostly | Kimi-Audio TAG-Bench 1.4; open models within 5 points of chance on STAR-Bench temporal ordering [tagbench][starbench] | no |

What the field can do (2026):

- **TAG-Bench** (1,750 queries, 149.5 h, 21 systems): best mIoU 31.2. On the single-spoken-word subset (279
  queries, 7.2 s clips — the closest analogue to "when is 'don't' sung here?") the best is 34.5, Gemini-3.1-Pro
  14.3. With several true intervals no system exceeds **13.2 % count accuracy**, and all under-report
  [tagbench].
- **SpotSound:** zero-shot open models 2.4-9.1 mIoU on short events; task-specific fine-tuning reaches 52.7
  [spotsound]. **MusTBench:** vocal-offset Hit@3s 9.5 for Qwen3-Omni [mustbench].
- **arXiv 2511.13225** ("Seeing isn't Hearing"): vision-language models identify synthetic spoken words from
  spectrogram images at chance level (25 %, four choices), and fine-tuned gains came from text priors, not
  the image [vlm-spec]. It tested phoneme identity, not "is there voice energy inside this box"; it is a
  warning for the agent's picture reading, which the Faz 3.5 numbers-only vs numbers-plus-picture ablation
  measures on our task.

**Conclusion.** No local audio LLM localises words to < 0.5 s zero-shot; counting repeats is the field's
weakest skill. At most an audio LLM answers yes/no questions (voice or instrument, is this line in this
clip), and only after a one-day kill test. The non-LLM "is there voice here" detector already exists: the CTC
letter-probability curve in `audit.ear`.

## 3. A summarizer (local text LLM)

| model | licence | fits 16 GB | speed on a 16 GB card | notes |
|---|---|---|---|---|
| **Gemma 4 26B-A4B QAT Q4_0** (installed) | Apache-2.0 [gemma4-26b] | yes, 14.4 GB; 25.2B total / 3.8B active | ~85 tok/s generation and ~2,300 tok/s prefill at 20k on a 5060 Ti (blog) [g4-5060]; ~70 tok/s on a 5070 Ti (self-reported) [5070-guide] | text and image input |
| Qwen3.8-27B IQ4_XS (not installed) | Apache-2.0 [qwen38] | yes, 14.3 GB [qwen38-gguf] | ~24-36 tok/s depending on quant (self-reported) [5070-guide] | quality-first alternative |
| gpt-oss-20b MXFP4 (not installed) | Apache-2.0 [gptoss] | yes, ~14 GB | ~96 tok/s on a 5070 Ti (self-reported) [5070-guide] | speed-first alternative |
| Gemma 3 27B QAT / Gemma 4 31B | Gemma Terms / Apache-2.0 | no: 17.2 / 17.7 GB before KV cache [gemma3-qat][gemma4-31b] | — | — |

Evidence that shapes the prompt:

- **JSON mode costs reasoning.** Strict JSON output cut GSM8K from 76.6 to 29.9 (GPT-3.5); free prose
  followed by a conversion to JSON recovered it; a `verdict` key before `reason` skipped the reasoning
  [json-mode]. On a grammar-constrained server this means **two requests** per song: prose, then JSON with
  the prose appended, reusing the cached prefix [llamacpp-server].
- **Long context degrades.** At 32k tokens most models fell below half their short-context score [nolima];
  the installed server runs at 16k. Per-song input stays ≤ 12k (moment table first, question last); 12k is an
  assumption until counted.
- **Arithmetic over numeric logs is weak** [ts-anomaly]. The model is never asked to subtract or count
  milliseconds: the finder's differences and tags go in ready-made, and the model judges them.
- **Fine-tuning:** 96 labels with a smallest class of 4 cannot even be evaluated per class. QLoRA on 16 GB
  fits ≤ 14B or gpt-oss-20b, not a 27B [unsloth]. Revisit at ≥ 300-500 labels.

## 4. Non-LLM evidence and the perception literature

- **Thresholds.** The +220 / −330 ms asymmetry is Masclef, Vaglio & Moussallam, ISMIR 2021 (53 listeners,
  80 DALI songs) [masclef]. Early-side tolerance shrinks with tempo and word rate (−0.36 s slow vs −0.31 s
  fast; −0.39 s high vs −0.28 s low words per second); the late side does not move; errors on beat-aligned
  words are judged more confidently. Their Asym-PCO window [−0.3, +0.2] matches `audit.score`.
- **Crowd-sourced sync is noisy in a known way.** An audit of DALI found ~10 % of songs with issues: ~100
  with wrong lyrics, ~100 with extra paragraphs at the end, more than 200 with timestamp offsets (constant,
  linearly drifting or local; most under 1 s, some 8 s or more) [kick24]. These are our three classes.
- **Onset and pitch tools** give corroborating onsets, never word identity: ROSVOT (MIT, note and
  word-boundary model, Mandarin-trained) is closest to syllable onsets [rosvot]; basic-pitch (Apache-2.0)
  note onsets equal syllable onsets only in syllabic singing [basicpitch]; torchcrepe (MIT) gives f0 only
  [crepe]; madmom models are CC-BY-NC-SA [madmom].

## 5. Measurement protocol (principles)

1. Thresholds are written down and committed before the first run. A part that does not beat the current ear
   on the labelled set does not enter.
2. **No circular witnesses.** The CTC ear cannot certify the CTC aligner's times; agreement of the agent with
   its own Faz 3 verdicts is reproducibility, not accuracy. Accuracy needs owner listens.
3. Pool per song; where a threshold is fitted, report leave-one-song-out.
4. Report the classes a viewer sees (seconds-scale shifts, never-lit words, wrong lines) before piles under
   ~0.4 s.

The gates per step are in the Faz 3.5 section.

## 6. Cost and flow on the owner's machines (300 songs)

| phase | LLM server | work | time (estimate) |
|---|---|---|---|
| A | off | download and separation, ~1 min per track on the GPU worker (`roadmap.md`, phase 9.3) | ~5 h |
| A | off | whole-song CTC ear (2.5 min on CPU, of which 1 min is model load) | minutes on GPU |
| A | off | Qwen3-ASR at RTF 0.0148 (vendor figure, GPU not named) [qasr-card] | ~15 min |
| B | on | summarizer, two requests per song | ~1.5-2 h |

- One night if downloads hold, two nights to be safe. Nothing starts the machine; a missed night costs a day.
- When the desktop is off nothing audit-related runs; the CPU worker keeps the production queue
  (`docs/gpu-worker.md`). Audit jobs write one output per song (model, prompt hash and settings inside, temp
  file and atomic rename) and resume where they stopped.
- Windows Modern Standby stops desktop processes [standby]; the batch keeps the machine awake from the
  Windows side (`SetThreadExecutionState`), which WSL cannot do.
- **Audio:** raw downloads are deleted as soon as the stem exists (audit README); stems stay for the project
  and are deleted at its close. Test-only exception: the 20 pilot mixes stay on cnr-intel for the Whisper arm
  and are deleted when the test ends, verified by a listing. No audio leaves the owner's machines.

## 7. The Claude API option

**Audio input: none (verified 2026-11-01).** User content blocks are text, image (JPEG/PNG/GIF/WebP), document
(PDF, plain text), search result, tool blocks and container upload; there is no audio block or audio media
type [api-messages]. The Files API maps uploads to document, image or container upload [api-files], and the
pricing page has no audio rate [api-pricing]. The listener role is impossible; the summarizer role is open.

| per MTok, input / output | standard | Batches (−50 %) |
|---|---|---|
| Opus 5.5 | $4 / $20 | $2 / $10 |
| Sonnet 5.5 | $2 / $10 | $1 / $5 |
| Haiku 5.5 (prompt ≤ 100k) | $0.10 / $0.50 | $0.05 / $0.25 |

Batches: most finish within an hour, expire after 24 h, results kept 29 days; caching discounts stack
[api-batch][api-pricing]. Batches are not zero-retention eligible (29-day retention) [api-batch].

**Cost per 300 songs** (estimate; 12k input and 500 output tokens per song are assumptions; Opus 5.5 always
thinks and bills thinking as output, 1-4k tokens per song depending on effort):

| model, Batches | text only | + 4 pictures per song (~1.75k tokens each, assumption) |
|---|---|---|
| Opus 5.5 | ≈ $12-21 | + ≈ $4 |
| Sonnet 5.5 | ≈ $6-10 | + ≈ $2 |
| Haiku 5.5 | < $1 | + < $0.5 |

- **Quality:** expected above a local 4B-active MoE on numeric judgement, and it can read the pictures. That
  is an expectation; the Faz 3.5 A/B measures it on the same inputs.
- **Speed:** typically under an hour for 300 songs, against ~1.5-2 h locally.
- **Privacy and copyright:** raw audio never leaves. Derived numbers, the ear's text, lyric lines and song
  titles do (the moment files carry lyric text), and they are retained for 29 days on the Batches path.

## 8. Lyrics source and anchor verification

The classes to catch: a sheet shifted globally or per section (GOSSIP 17-19 s, Got 2 Luv U 3-4 s), a sheet
for a different mix (Enemy, pending the owner's listen), and local verse shifts. Duration checks see none of
them (lrclib accepts ±2 s [lrclib]).

| method | how | strength | repeated refrains |
|---|---|---|---|
| **A1 unique text anchors + robust offset** | anchors are runs of ≥ 2 consecutive heard words [hazen06] (≥ 5 on noisy stems [demirel21]) matching an n-gram that occurs **once** in the sheet; Smith-Waterman over the whole song with longest-phrase-first narrowing [dsalign]; per section the median of anchor deltas with a dispersion test (t-test in [lyricsync-patent]); ≥ 5 confirmed lines before moving lines > 5 s [ultrasinger] | sub-second where anchors exist | placed afterwards, between the unique anchors on either side, in order |
| **A2 activity correlation** | expected voice activity from the sheet against the CTC non-blank mass; grid over offset ±30 s and stretch ±5 %; accept NCC ≥ 0.8 [dali] | independent of text errors; catches global offset and drift | ambiguous at the refrain period; confirms A1 |
| **A3 coverage / version score** | per-line character error against the ear in its corrected window with a cap [gigaspeech]; song-level 3-gram TF-IDF cosine [vaglio21]; share of sung activity matched by no line | the wrong-mix signature: an unmatched sung block **and** unmatched sheet lines | n/a |

- **Window trade-off:** keep the narrow window (±8 s) for word-level placement; use the whole song only for
  unique anchors. That removes the GOSSIP blind spot without letting refrains fool the match.
- **Which model in production:** none new. The methods run on the production checkpoint's emissions
  (permissive). Qwen3-ASR stays an audit witness unless it later earns more.
- **Version bookkeeping:** an anchor check in the pipeline is a pipeline change (`pipeline_version`). If a
  failed check makes the lookup try another lrclib record, that is a rung change: bump `LOOKUP_LADDER_VERSION`.
- **The twin:** the overlay's own lrclib client shows records without audio; a server-side check cannot
  protect that path unless the overlay consults the server's verdict.
- **Upstream:** lrclib has `POST /api/flag` for wrong records (needs a publish token) [lrclib]; whether to
  flag what we find is the owner's call.

## Decision

**Owner's choice (2026-11-03): option 1, and the Faz 3.5 trial below is approved.** Step 0's listening set is
planned for 2026-11-08 (it may be split over two days).

1. **Recommended — now: the deterministic anchor check (A1-A3) plus Qwen3-ASR as an independent transcript
   witness; a summarizer only after the Faz 3.5 Step 3 gates.** Opus 5.5 Batches is the default summarizer
   candidate and the installed Gemma 4 26B-A4B the local one, on identical inputs. Until a summarizer passes,
   the agent keeps reading flagged songs in session as today. Highest value for the least new machinery, and
   Qwen3-ASR opens Japanese and a wrong-sheet witness.
2. **All local:** Qwen3-ASR plus the Gemma summarizer; the agent only for the final look. No API cost, the most
   engineering, and worthless if the summarizer gates fail.
3. **Anchor check only:** no new model. Smallest step; fixes the worst class; Japanese stays closed.
4. **Claude only:** every song's top moments, pictures plus numbers, through Opus Batches (≈ $16-25 per 300
   songs); the desktop only produces stems and ears. Its accuracy needs the owner's listening set.
5. **Audio-LLM judge:** not in the plan beyond an optional one-day kill test.

## Faz 3.5 proposal: local ear + summarizer trial

**Code this needs from the audit work stream** (this note writes none): (a) the whole-song anchor methods
A1-A3; (b) a segment-timed external-ear adapter that writes the same shape as the whole-song ear file; (c) a
script-aware Japanese matcher; (d) the summarizer input pack (moment files and finder events, ≤ 12k tokens)
and a two-request client for the local server and for Batches.

**Step 0 — freeze the evaluation (1 day).** Real errors n = 46 (18 + 12 + 6 + 6 + 4; the 5 `unsure` are out
until listened). Inputs: the 8 blind answers, the 13 human-timed lines, the pop valid region (278 words). New:
an **owner listening set** of ≥ 20 moments spread over the classes plus Enemy's verse (half a day; the
accuracy reference). Count the tokens of the 20 pilot prompts to re-derive the 16k fit and the cost. Commit
every threshold below before any run.

**Step 1 — anchor check (2-3 days, host CPU).**
- Pilot set: GOSSIP and Got 2 Luv U flagged with |section median| > 1 s, sign and size within 1 s of the Faz 3
  reading (−18.9 s, −4.0 s). The hardstyle track (11 of 40 lines placed, processed vocals) is the named
  negative; the discriminator is "an unmatched sung block whose transcript spells other words **and**
  unmatched sheet lines" against "no readable letters, low placement". No flag on the remaining 17.
- Enemy: flagged by coverage (pre-registered: unmatched sung duration ≥ 25 % of sung duration and ≥ 25 % of
  sheet lines unmatched) only if the owner's listen confirms a different mix; otherwise it joins the negatives.
- Injected errors on the bench's JamendoLyrics copy (79 songs) [jamendo]: global +2/+5/+10/+20 s, linear drift,
  one verse shifted 3-20 s, a deleted verse, an extra trailing paragraph, in random order. Gates: detection
  ≥ 90 % for shifts ≥ 5 s (2 s reported), offset error ≤ 0.5 s when detected, false alarms ≤ 5 % on unmodified
  songs.

**Step 2 — second ear (2-3 days).**
- (a) Qwen3-ASR-1.7B on the desktop in phase A, on the known path: the `qwen-asr` package installed into the
  bench GPU image, which already ran Qwen3-ForcedAligner on this card in 2026-08 (`benchmarks/qwen_probe.py`);
  the vendor image and vLLM are unverified on Blackwell. Day-one gate: it runs. Input: 20 stems, plus 3 mixes for comparison.
- (b) Whisper large-v3-turbo with faster-whisper on cnr-intel's CPU (the CUDA 12 CTranslate2 stack stays out of
  the CUDA 13 image), on the 20 mixes with stem-derived voice segments.
- Gates per arm: placed-line share at or above the current ear's; on songs Faz 3 read as healthy, |median line
  offset| ≤ 0.5 s (segment-timed); GOSSIP and Got 2 Luv U read |median| > 1 s with the Faz 3 sign;
  transcript lines inside regions the CTC letter curve calls silent for > 2 s ≤ 5 %.
- Reported, not gated: Japanese placement (n = 1); Turkish placement (segment level); word-timing MAE of
  Qwen3-ForcedAligner and Whisper on the pop valid region. The Faz 8.1 verdict on word-level second opinions
  stands whatever these show.
- Test end: the 20 mixes are deleted and the listing is recorded.

**Step 3 — summarizer A/B (2 days, phase B).** Same text inputs for both (no picture, differences
precomputed, ≤ 12k tokens), prose then JSON in two requests. Opus 5.5 through Batches (effort set explicitly,
cost read from `usage`) against Gemma 4 26B-A4B (16k, reasoning off). Stability: three runs with the moment
table shuffled (no seeds: the local run is deterministic at temperature 0, and Opus takes no sampling
parameters).
- M1 recall of real errors ≥ 0.90 (n = 46, ≤ 4 misses) at precision ≥ 0.60, within the finder-and-control
  candidate set. The comparison with the finder's own ranking is reported, not gated (n is too small).
- M2 Spearman between model severity and label severity, pooled over all moments, ≥ 0.5; precision@3 of real
  errors ≥ 0.7 on songs with ≥ 3 of them.
- M3 recall of the visible classes (late + early + missing + wrong_text, n = 28) ≥ 0.7; pile reported.
- M4 numeric probe on an unsorted table ("which line has the second-largest |lit − voice| and how large?")
  correct for ≥ 19 of 20 songs; failure removes every comparison from the model's job.
- M5 schema-valid on the first try ≥ 99 %; verdict agreement across shuffled runs ≥ 90 %.
- Opus extra arm: numbers-only against numbers-plus-picture (tests the spectrogram-reading worry on our task);
  agreement with the Faz 3 verdicts is reported as reproducibility, accuracy against the owner listening set.
- A model that fails the gates gets no role.

**Step 4 — optional audio-LLM kill test (1 day).** MOSS-Audio-4B-Thinking, Qwen2.5-Omni-7B GGUF as the
control; 20 clips of 4-8 s (≥ 5 with the word repeated 2-4 times, ≥ 5 without it); TAG-Bench prompt form;
three shuffled runs. Pass: count exact ≥ 80 % and onsets within ±250 ms ≥ 80 %. Median onset error > 500 ms
ends it.

**Time:** about two calendar weeks, bound by the desktop's night windows and the audit code above. Each step
ends with a "measured / passed / failed" line in this note; what fails goes to `olu-fikirler.md`.

## Faz 3.5 results (local runs, 2026-11-05)

Code: branch `claude/friendly-mayer-7v5syf` (gates as pre-registered in `audit/gates.py`; no gate changed).
Document: the 2.29 dump of the pilot set (20 songs). Hosts: cnr-intel (anchor check, Whisper, summarizer
clients), the desktop's RTX 5070 Ti (Jamendo ears, Gemma, Qwen3-ASR). Raw outputs stay in `scratch/audit/`.

**Step 0 — measured, passed (token fit).** Pack sizes for the 20 songs: 2,413-9,028 tokens on Gemma's
tokenizer, 2,969-10,126 on Claude's `count_tokens`; none above 12k, so every pack plus the 4k answer fits the
installed 16k context. Measured Opus cost for both arms, 3 runs × 20 songs: **$8.02** ($3.28 text,
$4.74 with pictures). The owner listening set is planned for 2026-11-08; until then Step 3 reports
reproducibility only.

**Step 1 — measured, failed.**
- Pilot: both lyrics-source shifts flagged within the gate: GOSSIP −19,310 ms (reading −18,900, off by
  410 ms), Got 2 Luv U −3,975 ms (reading −4,000, off by 25 ms). It fails on `wrong_sheet`: raised on three
  healthy songs (Crazy In Love, New Rules, SexyBack), on BxMxC (the coverage check matched 0 of 13
  Japanese lines: A3 does not take the kana path), on the hardstyle negative and on Enemy (counts as a miss
  until the owner's listen confirms another mix). Got 2 Luv U raises `wrong_sheet` next to its shift.
- Injected errors (79 Jamendo songs, 869 trials, seed 35): detection of shifts ≥ 5 s **0.798** (gate 0.90):
  global 222/237 (0.94), drift 144/158 (0.91), one verse 61/140 (0.44). Offset error median **64 ms**, p90
  115 ms (gate 500, passed). False alarms **15/79 = 0.19** (gate 0.05), every one `wrong_sheet`; no `shift`
  alarm on an unmodified song. Reported: 2 s shifts 0.937, verses under 5 s 0.389, deleted verse 0.911,
  extra paragraph 1.0.
- Reading: the whole-sheet offset (A1/A2) does what GOSSIP needed; the per-verse shift and the
  `wrong_sheet` rule (A3) do not meet their gates.

**Step 2 — measured, failed for both arms.**
- Whisper large-v3-turbo (faster-whisper 1.2.1, CPU int8, the 20 mixes cut at the stem's pieces):
  placed-line share 0.645 against the CTC ear's 0.500 (passed); both shifted songs read beyond 1 s with
  the Faz 3 sign (passed; GOSSIP reads −3.7 s, the ±8 s window sees only its second chorus); healthy
  |median| ≤ 0.5 s failed on one song of 16 (Deli Oğlan +569 ms; four more sit at −324 to −433 ms); pieces
  with text inside CTC silence 0.198 (gate 0.05, failed). Japanese, reported: BxMxC 24 of 42 lines placed
  (CTC ear 6), median −18 ms.
- The 20 mixes were deleted on 2026-11-05 19:28 UTC; both listings are in `scratch/audit/`.
- Qwen3-ASR-1.7B (qwen-asr 0.0.6, bf16 on the 5070 Ti, the 20 stems, language detection on; 2026-11-05):
  it runs (day-one gate), 20 songs in about 7 minutes with the model reloaded per song. Placed-line share
  **0.678** against the CTC ear's 0.500 (passed); GOSSIP −3,850 ms and Got 2 Luv U −4,366 ms (passed);
  healthy |median| ≤ 0.5 s failed on two songs of 16 (Deli Oğlan +620 ms, as with Whisper; the hardstyle
  negative +1,183 ms); pieces with text inside CTC silence **0.150** (gate 0.05, failed; worst song 0.444).
  Japanese, reported: BxMxC 12 of 42 lines placed (CTC ear 6, Whisper 24), median +124 ms; it wrote Latin
  letters, not Japanese script.
- Reading, not a gate change: the silence gate counts the CTC ear's gaps more than hallucination at segment
  level. All 32 Qwen pieces it counts are pieces where Whisper wrote text too (Whisper's 14 others, 11 on one
  song, are its own), their median length is 12 s (a piece is judged by its middle), and most carry the
  sheet's own lines (Sorry For Party Rocking, Enemy, Hey Mama, a Turkish verse). On the hardstyle negative
  both ears hear a verse the sheet does not have ("Coffee goes cold…" where the sheet sings "Lying in the
  light on…"), so its +1.18 s may be a version mismatch rather than an ear error. A finer cut or a
  per-character silence rule would be a new pre-registration.

**Step 3 — measured, failed for every arm** (against the Faz 3 verdicts: reproducibility, not accuracy).

| arm | M1 recall / precision | M2 Spearman / p@3 | M3 visible | M4 probe | M5 valid / agreement | cost |
|---|---|---|---|---|---|---|
| Gemma 4 26B-A4B (local, 16k) | 0.85-0.91 / 0.64-0.66 | 0.28-0.40 / 0.83-0.88 | 0.68-0.82 | 4-6 of 20 | 0.983 / **0.556** | $0 |
| Opus 5.5, numbers | 0.96-0.98 / 0.69-0.71 | 0.61-0.63 / 0.88-0.92 | 0.93 | 19-20 of 20 | 1.0 / **0.767** | $3.28 |
| Opus 5.5, numbers + pictures | 0.96 / 0.75-0.79 | 0.66-0.72 / 0.92-0.96 | 0.86-0.93 | **18**-20 of 20 | 1.0 / **0.778** | $4.74 |

Ranges are over the three shuffled runs; a gate must hold in every run. Gemma fails M1-M5 almost
everywhere, and its M4 failure takes every comparison out of its job. Opus fails only M5's run-to-run
agreement (and, with pictures, M4 in one run); the pictures raise precision and Spearman. As registered,
"a model that fails the gates gets no role": the agent keeps reading flagged songs in session. A lower
agreement gate, or a majority vote over runs, would be a new pre-registration (the owner's call, with the
listening-set accuracy next to it).

## Sources

Read on 2026-11-01. Labels in brackets above.

Ear
- [qasr-card] https://huggingface.co/Qwen/Qwen3-ASR-1.7B · repository https://github.com/QwenLM/Qwen3-ASR · report https://arxiv.org/abs/2601.21337
- [qasr-srv] https://github.com/malaiwah/qwen3-asr-server
- [vocalparse] https://arxiv.org/html/2605.04613
- [parakeet3] https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3 · [parakeet2] https://huggingface.co/nvidia/parakeet-tdt-0.6b-v2
- [nemo-win] https://github.com/NVIDIA-NeMo/Speech/discussions/6515
- [fw] https://github.com/SYSTRAN/faster-whisper · [ct2] https://github.com/OpenNMT/CTranslate2/releases
- [jamalt] https://arxiv.org/html/2408.06370v1 · [syed25] https://arxiv.org/html/2506.15514v1
- [lyricwhiz] https://arxiv.org/abs/2306.17103 · [whisper-ns] https://arxiv.org/html/2501.11378v1
- [voxtral2] https://mistral.ai/news/voxtral-transcribe-2 · https://huggingface.co/mistralai/Voxtral-Mini-4B-Realtime-2602
- [canary] https://huggingface.co/nvidia/canary-1b-v2 · [omniasr] https://github.com/facebookresearch/omnilingual-asr

Listening judge
- [vlm-spec] https://arxiv.org/abs/2511.13225
- [tagbench] https://arxiv.org/html/2609.01542v2
- [moss] https://github.com/OpenMOSS/MOSS-Audio
- [qomni-awq] https://huggingface.co/cyankiwi/Qwen3-Omni-30B-A3B-Instruct-AWQ-4bit
- [qomni25-gguf] https://huggingface.co/ggml-org/Qwen2.5-Omni-7B-GGUF · [llamacpp-mm] https://github.com/ggml-org/llama.cpp/blob/master/docs/multimodal.md
- [timeaudio] https://arxiv.org/html/2511.11039v1
- [firered] https://huggingface.co/FireRedTeam/FireRedAudio
- [gemma4-card] https://ai.google.dev/gemma/docs/core/model_card_4 · [gemma-audio] https://ai.google.dev/gemma/docs/capabilities/audio
- [af3] https://huggingface.co/nvidia/audio-flamingo-3
- [starbench] https://arxiv.org/html/2510.24693 · [spotsound] https://arxiv.org/html/2604.13023v1 · [mustbench] https://arxiv.org/html/2605.29300v2

Summarizer
- [gemma4-26b] https://huggingface.co/google/gemma-4-26B-A4B-it-qat-q4_0-gguf
- [g4-5060] https://njannasch-dev-astro.pages.dev/blog/gemma-4-moe-dense-5060ti-context-window/ (blog)
- [5070-guide] https://bestllmfor.com/guide/rtx-5070-ti-llm-local-config/ (self-reported)
- [qwen38] https://huggingface.co/Qwen/Qwen3.8-27B · [qwen38-gguf] https://huggingface.co/unsloth/Qwen3.8-27B-GGUF
- [gptoss] https://huggingface.co/openai/gpt-oss-20b · [gemma3-qat] https://huggingface.co/google/gemma-3-27b-it-qat-q4_0-gguf
- [gemma4-31b] https://huggingface.co/google/gemma-4-31B-it-qat-q4_0-gguf
- [json-mode] https://arxiv.org/abs/2408.02442 · [nolima] https://arxiv.org/abs/2502.05167 · [ts-anomaly] https://arxiv.org/abs/2410.05440
- [llamacpp-server] https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md
- [unsloth] https://unsloth.ai/docs/get-started/fine-tuning-for-beginners/unsloth-requirements.md
- [standby] https://learn.microsoft.com/en-us/windows-hardware/design/device-experiences/modern-standby-vs-s3

Perception and onsets
- [masclef] https://archives.ismir.net/ismir2021/paper/000052.pdf
- [kick24] https://www.research-collection.ethz.ch/server/api/core/bitstreams/1569974e-3b0f-46cf-b622-92411f1f7e26/content
- [rosvot] https://github.com/RickyL-2000/ROSVOT · [basicpitch] https://github.com/spotify/basic-pitch · [crepe] https://pypi.org/project/torchcrepe · [madmom] https://github.com/CPJKU/madmom

Claude API
- [api-messages] https://platform.claude.com/docs/en/api/messages/create
- [api-files] https://platform.claude.com/docs/en/build-with-claude/files
- [api-pricing] https://platform.claude.com/docs/en/about-claude/pricing
- [api-batch] https://platform.claude.com/docs/en/build-with-claude/batch-processing

Anchors
- [lrclib] https://lrclib.net/docs.md
- [hazen06] https://sls.csail.mit.edu/publications/2006/IS061258.pdf
- [demirel21] https://webspace.eecs.qmul.ac.uk/s.e.dixon/pub/2021/DemirelAhlbaeckDixon-ICASSP2021-preprint.pdf
- [dsalign] https://github.com/mozilla/DSAlign/blob/master/doc/algo.md
- [lyricsync-patent] https://patents.google.com/patent/US11245950B1
- [ultrasinger] https://github.com/MrDix/UltraSinger/pull/208
- [dali] https://arxiv.org/html/1906.10606v1
- [gigaspeech] https://arxiv.org/pdf/2106.06909
- [vaglio21] https://archives.ismir.net/ismir2021/paper/000089.pdf
- [jamendo] https://huggingface.co/datasets/jamendolyrics/jamendolyrics
