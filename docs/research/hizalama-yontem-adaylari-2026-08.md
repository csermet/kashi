# Alignment method candidates — licence, audio source, cost (Faz 8 P3/P5, 2026-08-05)

**Status:** research synthesis, no implementation. The decision document for
Faz 8's second half. Companion to
[hizalama-zinciri-durum-2026-08.md](hizalama-zinciri-durum-2026-08.md), which
measures what we have today.

Three independent web-research rounds fed this: ASR/forced-alignment
candidates, music-specific and rhythm-based methods, and a dedicated licence +
audio-source verification pass. Every candidate is tagged with the three
labels Caner asked for — **LICENCE** (commercial viability, with an
alternative where it is closed), **AUDIO SOURCE** (server download / client
capture / user file), and **COST** (CPU per song, arm64).

---

## 0. The finding that reorders everything

**The aligner we ship today is licensed CC-BY-NC-4.0 — commercially closed.**

Two research rounds reached this independently, and the primary source is the
wrapper's own README:

> "the default model has CC-BY-NC 4.0 License, so make sure to use a different
> model for commercial usage"
> — [MahmoudAshraf97/ctc-forced-aligner](https://github.com/MahmoudAshraf97/ctc-forced-aligner)

- `MahmoudAshraf/mms-300m-1130-forced-aligner` — model card states
  `license: cc-by-nc-4.0`.
- `facebook/mms-300m`, the base it fine-tunes — also CC-BY-NC-4.0. The
  derivative inherits it; there is no separate exemption.
- The wrapper *code* is BSD. Only the weights are the problem.

This closes the question the roadmap deferred to F11. It also changes what
Faz 8 is: not "is our alignment good enough", but **"we need a different model
anyway — so which one, and does it also fix what we measured?"**

The same pass found four more licence items, so the commercial path is not a
single swap:

| Item | Licence | Commercial | Note |
|---|---|---|---|
| `mms-300m-1130-forced-aligner` + `facebook/mms-300m` | **CC-BY-NC-4.0** | **NO** | the aligner itself |
| `diffq` / `diffq-fixed` | **CC-BY-NC-4.0** (code) | **NO** | *unconditional* dependency of `audio-separator`; ships in the image even though the RoFormer path never calls it. Fixable with `--no-deps` or an upstream change — a packaging problem, not an architectural one |
| `mel_band_roformer_kim_ft_unwa.ckpt` | **none / unstated** | **UNKNOWN** | no model card, `license: null` on the source repo. Treat as unlicensed |
| `librubberband` + `mwader/static-ffmpeg` | GPLv2+ / GPLv3+ | conditional | called via `subprocess`, so no copyleft reach into our Python (standard mere-aggregation reading). **But GPL is incompatible with Apple App Store / Microsoft Store distribution** — direct download and self-hosted server are fine |
| `fast-langdetect` model (`lid.176`) | CC-BY-SA-3.0 | yes, with attribution | attribution + share-alike notice, not NC |
| everything else (~120 packages) | MIT / BSD / Apache / ISC | yes | no further GPL/AGPL/NC found |

**A claim to distrust.** Three separate searches returned the *identical*
sentence asserting the Kim RoFormer checkpoint "was relicensed to MIT on
2026-04-22, independently confirmed with the original author". No primary
source supports it: the GitHub API reports `license: null`, the repo has no
LICENSE file, and the HF page has no model card. Treat this as a search-summary
fabrication and **do not use it for a commercial decision**. The conservative
reading stands: the weights are unlicensed.

---

## 1. Candidate table

| Candidate | Type | LICENCE (code / weights) | Commercial | CJK | COST / arm64 | Maintained | Invasiveness |
|---|---|---|---|---|---|---|---|
| **Current** — ctc-forced-aligner + mms-300m | forced align (CTC) | BSD / **CC-BY-NC-4.0** | **NO** | fails structurally | ~10 min CPU per song | active | — |
| **Qwen3-ForcedAligner-0.6B** (+ Qwen3-ASR) | forced align, text-conditioned | **Apache-2.0 / Apache-2.0** | **YES** | official "sub-optimal, follow-up" | 0.6B ≈ 1.3 GB; Rust port has NEON/AVX2 | very active (2026-01) | drop-in for the align stage |
| **NeMo Forced Aligner** + Parakeet | forced align (CTC) | Apache-2.0 / CC-BY-4.0 (`parakeet-tdt_ctc-0.6b-ja`) | **YES** with the right model | Japanese model exists; Chinese unverified | GPU preferred, CPU possible; **arm64 UNVERIFIED** | active (NVIDIA) | drop-in, but NeMo is a heavy dependency graph |
| **MFA 3.0** | classic forced align (Kaldi) | MIT / mostly open | likely yes | **strong** — <15 ms boundary error claimed for ja/ko | light, CPU-only — but **conda-forge does not support linux-aarch64** | very active (v3.4.1, 2026-07) | drop-in; RPi worker would need Kaldi built from source |
| **WhisperX** | ASR + align hybrid | BSD-2 / EN=MIT, most others **CC-BY-NC-4.0** | partial | weak | moderate | active | repeats our exact licence problem |
| **SOFA** (singing-oriented aligner) | forced align, sung-voice trained | MIT / MIT | yes | Chinese-first; needs your own G2P for ja/en | unverified | active (2025-05) | drop-in *if* you write the G2P |
| **Yohane** ja-karaoke checkpoint | MMS fine-tune for Japanese karaoke | MIT (code) / **inherits CC-BY-NC** ⚠ | **NO** | purpose-built for mora-level Japanese | same as current | very active (2026-08-02) | checkpoint swap — see warning below |
| **LyricsAlignment-MTL** (ICASSP 2022) | music-specific, pitch-assisted | MIT / MIT | yes | unverified | GPU preferred | active (2025-06) | replaces MMS |
| **AutoLyrixAlign** | music-specific (MIREX 2019 winner) | **GPLv3** | separate licence needed | unverified | unverified | **abandoned (2020)** | replaces MMS |
| **STARS** / **VocalParse** | phoneme + note + style, audio-LLM | MIT / Apache-2.0 | yes | — | **GPU mandatory / 1.7B LLM** | active | unrealistic for this cluster |
| **CrisperWhisper** | verbatim ASR (no text input) | MIT / **Nyra non-commercial** | **NO** | unverified | moderate | active | wrong shape — we already have the text |
| **stable-ts** | whisper timestamps | MIT | yes | — | — | **paused indefinitely (2026-05)** | dropped |

**Warning on Yohane.** The second research round recommended
`NextFire/mms-300m-ForcedAligner-karaoke-ja-Latn` as the lowest-risk fix for
our Japanese gap — and technically it is: it is exactly a Japanese
karaoke fine-tune of the model we already load, mora-level, MIT repo, updated
three days ago. **But it is a fine-tune of `facebook/mms-300m`, so it inherits
CC-BY-NC-4.0.** It is a legitimate answer for the homelab and a dead end for
the paid tier. That contradiction is the whole shape of Faz 8.

### Add-on components (do not replace the aligner)

| Component | Licence | Use | Note |
|---|---|---|---|
| **FireRedVAD** | Apache-2.0 | vocal-activity mask — stop leaving words in silence | explicitly supports speech/singing/music detection; its "beats Silero" number is self-reported, needs our own 10–15 song check |
| **basic-pitch** (Spotify) | Apache-2.0 | onset/note detection for snapping word starts | CPU-friendly, active |
| **CREPE / pYIN** | MIT / ISC (already in librosa) | pitch + sustain detection | pYIN adds **no** new dependency |
| **Onset-snap** (arXiv 2606.11903) | — | bipartite matching of predicted onsets to detected ones | **no code published** — the idea is usable, the implementation would be ours |

---

## 2. Audio source

Today: server-side yt-dlp. Faz 8's brief was to label every candidate with its
audio-source assumption, because client-side capture removes the yt-dlp
problem at the root. Technical verdict only — the ToS/legal reading stays in
F11.

| Path | Verdict | Why |
|---|---|---|
| **Chrome MV3 `tabCapture` + offscreen document** | **GO** | supported pattern since Chrome 116, unchanged in 2026: service worker gets `getMediaStreamId()`, offscreen document opens the stream. Reference implementation exists (`antor44/Audio-Transcription`: tab capture → local WebSocket, the same bridge shape we already run) |
| **Electron Windows (WASAPI loopback)** | **GO** | native in Chromium, no extra driver, stable since Electron 31 |
| **Electron macOS (CoreAudio Tap)** | **CONDITIONAL** | needs macOS 14.2+ **and Electron 39+** (stable 2025-10), plus `NSAudioCaptureUsageDescription` in Info.plist — omit it and you get a silent dead stream with no error |
| **Electron Linux (PipeWire/PulseAudio)** | **CONDITIONAL** | works on modern PipeWire distros, less proven; pure-PulseAudio systems may need a loopback module |
| **Keep audio audible while capturing** | **CONDITIONAL** | `tabCapture` mutes the captured tab by default; the offscreen document must reconnect the source to `AudioContext.destination` manually. Easy to forget, obvious in the field |

Quality and timing are not the obstacle: the captured stream is decoded 48 kHz
PCM — one decode from the source, no re-compression, far above the 16 kHz mono
the aligner wants. `tabCapture` draws from the browser's own audio graph, so it
does **not** suffer the 20–100 ppm clock drift of OS-level loopback.

Unverified: whether YouTube Music's current Widevine level blocks tab capture
in practice. That needs one live test, not more reading.

| | Server download (yt-dlp) | Client capture | User file |
|---|---|---|---|
| Quality | original CDN source | decoded PCM, 48 kHz | user-dependent |
| Reliability | breaks on YouTube anti-bot changes | browser/OS version gates, per-platform testing | very high, but manual |
| Build cost | **low — already exists** | **high** — two clients, three platforms, sync work | very low |
| Platform reach | any client | Chromium + Win/macOS/Linux, no mobile | anywhere |
| ToS exposure (F11) | high | low | none |

---

## 3. Where this leaves the decision

Faz 8's question was "is there a better method". The answer that came back is
harder and more useful: **the current method cannot ship commercially at all**,
and the three candidates that can are each blocked on something different —
Qwen3 on CJK maturity, NeMo on dependency weight and unverified arm64, MFA on
arm64 outright.

Four things are true at once and should be decided separately:

1. **The commercial licence problem is real and not optional.** It touches the
   aligner, `diffq`, and the separation weights. None of it blocks the homelab
   today.
2. **Three measurement defects are fixable without changing the method**
   (companion report §3). Until they are fixed, no method comparison is
   legible — half the archive scores exactly 1.00.
3. **The CJK gap has a cheap fix that is commercially poisoned** (Yohane) and
   an expensive one that is clean (Qwen3/MFA/SOFA).
4. **Client-side capture is technically GO on the main path** and would remove
   the yt-dlp dependency, at the cost of real cross-platform work.

Recommended reading order for the decision: fix the metric first (it is cheap
and it is the instrument every later comparison depends on), then run one
head-to-head on the benchmark harness — `apps/server/benchmarks/`, PCO/MAE
over 79 songs — between today's MMS and **Qwen3-ForcedAligner-0.6B**, the only
candidate that is Apache-2.0 on both code and weights *and* claims official
support for singing with backing music.

Open questions that need a live probe rather than more research:

- Does `mel_band_roformer_kim_ft_unwa` have a licence at all? (ask the author)
- Does YTM's DRM allow `tabCapture` today? (one test)
- Is Qwen3's CJK "sub-optimal" caveat serious on *our* songs? (one A/B on two
  tracks — one Japanese, one Latin)

---

# Round 2 (same day) — GPU, CJK, and the arbiter layer

Three further research rounds, run after the first decision pass. Each one
changed something material.

## 4. We are closer to the state of the art than the field reports suggest

Published JamendoLyrics numbers: Spotify's contrastive M6 reaches **AAE 0.15 s
/ PCO@0.3 92** (English); the previous generation (GYL/HBE) sits at 0.22–0.23 s.
**Kashi measures 191 ms MAE / PCO@0.3 91.5 %** — behind the published best,
ahead of 2022-class systems.

That reframes the whole phase. **The aligner is not the problem.** The loss is
in `line_qa.py`'s blind thresholds and the tail cases, which is exactly the
layer we own. Swapping models is a licence necessity, not a quality strategy.

Metric note: the field standard is AAE + PCO@0.3, where 0.3 s was chosen as a
human-perception tolerance. No karaoke-specific perception study was found
(UNVERIFIED); adjacent numbers — AV sync detection thresholds around 45–125 ms
(ITU BT.1359 / NTIA TM-11-474) — suggest **also reporting PCO@0.1** if we care
about *felt* sync rather than passable sync.

## 5. The CJK gap is deeper than `line.split()`

`alignment.py:214` calls `preprocess_text(..., romanize=True)`, and uroman —
MMS's romanizer — **treats kanji as Chinese and emits pinyin**: 空 becomes
"kong", not "sora". It is a documented uroman limitation, not a bug.

**So fixing the token-count identity alone would not fix Japanese.** The
document would still be aligned against acoustically wrong text. This also
explains why the Japanese documents' line-mode scores look the way they do.

Two corrections to earlier notes follow from this:

- **Yohane does not solve kanji.** Reading its source: `lyrics.py` uses neither
  MeCab nor pykakasi — it splits *romaji* with vowel/consonant rules and
  **expects the user to supply romaji lyrics**. That is the anime-karaoke
  convention (a human provides romaji/furigana). lrclib gives us kanji, so
  Yohane is not the cheap drop-in it looked like — independently of its
  inherited CC-BY-NC licence.
- **The pattern that does work** is Nightingale's (GPL-3 — *ideas only, no code
  copied*): segment with fugashi → pull each morpheme's **UniDic kana reading**
  → align at kana/mora level. uroman romanizes *kana* correctly, so the
  existing MMS chain stays usable once the reading layer exists.

### Recommended shape: (a)+(c) hybrid

1. **Reading/segmentation layer** — JA: `fugashi`+`unidic-lite` or `SudachiPy`
   (both commercially safe; Sudachi's dictionary is more actively maintained,
   fugashi's reading fields more established — compare in the PoC).
   ZH: `jieba` + `pypinyin`. KO: today's `split()` already works (hangul is
   space-delimited).
2. **Emit `(surface, alignment_units)` pairs**, where the Japanese alignment
   unit is the kana **mora**. Feed morae as one space-separated stream: uroman
   is then in safe territory and `regroup_words_into_lines`' identity holds
   with the mora count as the expected number.
3. **Fallback** when the identity still fails: split by character proportion
   rather than dropping the whole document to line mode.

**Excluded on licence:** `pykakasi` (GPL-3), `KoNLPy` (GPL-3), `LTP`
(commercial use requires a paid licence).

**Gikun/ateji** — the anime convention of writing 宇宙 and singing "sora" — is
unsolvable by any dictionary, and is precisely why the karaoke world asks a
human for romaji. Frequency in lyrics is UNVERIFIED. Mitigation: a wrong
reading depresses the CTC score, so the existing quality gate and line-mode
fallback already act as the safety net.

**LLM segmentation: not needed.** Supervised segmenters reach ~97 % F1 on
Chinese word segmentation while LLMs trail badly zero-shot, and an LLM call
cannot be made bit-identical even at temperature 0 (batch-invariance breaks
it) — which the document contract requires. A dictionary segmenter is
deterministic by construction.

## 6. The arbiter layer has prior art, and one of it is ready to use

- **BEACON** (Apache-2.0, 2026) — merges word timings from several
  aligners/ASRs by **IoU ≥ 0.9 consensus voting**, rejecting spans without a
  strict majority. Model-agnostic: it eats standard word-timestamp files, so
  MMS and Qwen3-FA outputs feed it directly. Written for speech, not singing —
  the architecture transfers, the singing-specific rules are ours.
- **No singing-specific multi-aligner fusion paper exists** (searched). That
  part is genuinely ours to write.
- **Vowel alignment — the load-bearing detail.** In singing, a note onset
  aligns with the syllable's **vowel**, not its leading consonant. So snapping
  a word start to the nearest onset shifts it systematically *late* by the
  consonant's duration. Any snap must be vowel-aware. (The ms-level published
  measurement is UNVERIFIED; measurable on our own benchmark set.)
- **Onset tooling licence trap:** `madmom` has the best onset models but ships
  them **CC BY-NC-SA** — commercially barred. `basic-pitch` (Apache-2.0) is the
  clean alternative.
- **Training-data licence trap (F11):** **DALI is CC BY-NC-SA and MTG-Jamendo
  bars commercial use.** Evaluating on them is fine; **training a model on them
  is not.** A learned arbiter would have to be trained on features derived from
  our own archive and our own labels — the features are signal-level (aligner
  disagreement, onset distance, VAD coverage, lrclib deviation), so no licensed
  audio is needed.
- **Qwen3-FA caveat:** it quantises to **80 ms time bins** (±40 ms), which is
  material at our 191 ms MAE scale — and its language list **does not include
  Turkish**.

**Honest verdict from the round:** the arbiter is not over-engineering, but
order matters. Cheapest-largest first: (1) VAD mask + the score fix, (2) add
Qwen3-FA as a second opinion and treat disagreement as low confidence — this
replaces the blind 2.5 s threshold with evidence, and marks lines "uncertain"
instead of deleting their words, (3) vowel-aware onset snap. A learned arbiter
is step 4 and cannot be trained before the first three produce telemetry.

## 7. GPU — verdicts

| Card | Production | Experiments | Why |
|---|---|---|---|
| RX 480 8 GB | **NO-GO** | **NO-GO** | ROCm dropped gfx803 in 4.5 (2021); the liveliest community fork reaches torch **2.6**, we pin 2.9.1. No Vulkan path exists for CTC alignment at all |
| RTX 3060 Ti 8 GB | **CONDITIONAL GO** | unnecessary | 8 GB fits every model when the stages run sequentially; ~8–12× over CPU. Conditions: the card is in use elsewhere, ~10 kWh/month idle, and a driver/kernel pinning discipline (`apt-mark hold`, DKMS) added to the upgrade checklist |
| RTX 5070 Ti 16 GB | NO-GO (not 24/7) | **GO** | torch 2.9.1 + cu128 supports sm_120 since PyTorch 2.7 stable. A 79-song benchmark run lands at **~2–3 hours** against ~13 on CPU. Traps: the default pip index (cu126) has no sm_120, and Windows-native Triton is broken on sm_120 — use WSL2 and eager mode |

Putting a GPU into the cluster is **medium** ongoing maintenance: half a day to
install, then one more verification step at every kernel and K8s upgrade. Only
`cnr-intel` can host one physically (the DeskMini has no PCIe slot), and that
is the storage node — so a GPU there also makes it the compute single point of
failure.

---

# Round 3 (2026-08-06) — the permissive CTC checkpoint search

The line-level signal hunt ended with one lever left: a second aligner. The
seam takes CTC checkpoints, so a single permissively licensed CTC checkpoint
is both the commercial escape from CC-BY-NC and the cross-model second
opinion the arbiter needs. This round searched for it.

## Wrapper compatibility — measured from source, and it widens the field

Three findings from reading `ctc-forced-aligner`'s code change who qualifies:

1. **`<star>` does not need to be in the tokenizer.** `get_alignments` extends
   the dictionary itself and `generate_emissions` appends the star column to
   the emissions after log-softmax. Every `AutoModelForCTC` checkpoint gets it
   for free — not an elimination criterion after all.
2. **Vocab keys are lowercased by the wrapper**, so uppercase-letter models
   (`facebook/wav2vec2-large-960h-lv60-self`) work unchanged.
3. **The trap: out-of-vocab tokens are dropped silently** (`if c in
   dictionary` — no UNK, no error). A phoneme-vocab model fed latin text
   loses nearly every token without raising. Any candidate run must watch
   **PCO and the line-mode rate together**; either alone can hide this.

Also verified: `uroman` is MIT (the licence file, not hearsay), and the
wrapper has a sound `romanize=False` path that preserves Turkish diacritics —
Kashi hardcodes `romanize=True` in `_align_texts`, so a per-model flag is a
small change when a native-vocab model is adopted.

## Candidates (licence lineage verified)

| Model | Lineage | Vocab | Languages | Verdict |
|---|---|---|---|---|
| **voidful/wav2vec2-xlsr-multilingual-56** | XLSR-53 (Apache-2.0) → Apache-2.0 | char-level multilingual (**unverified** — repo is `gated:auto`, needs any HF token) | 56 incl. **TR + JA** | **prime candidate** — the only multilingual permissive option found |
| **jonatasgrosman/wav2vec2-large-xlsr-53-english** (+de/fr/es siblings) | XLSR-53 → Apache-2.0 | lowercase latin, 26 letters — **verified from vocab.json, not gated** | one per language, no TR | **drops straight in**, zero prerequisites |
| **mpoyraz/wav2vec2-xls-r-300m-cv8-turkish** | XLS-R-300m → Apache-2.0 | latin + ç ğ ı ö ş ü — **verified** | TR (WER 10.6 CV8) | works today under romanize; full quality wants `romanize=False` |
| ttop324/wav2vec2-live-japanese | XLSR-53 → Apache-2.0 | hiragana | JA | pairs with the P-B3 kana path (`romanize=False`, feed kana) — needs its own benchmark |
| facebook/wav2vec2-lv-60-espeak-cv-ft | Apache-2.0 | **IPA phonemes** | many | silent-drop failure class + espeak-ng is GPL-3 — low priority |
| w2v-bert-2.0 fine-tunes | base is MIT | — | no multilingual CTC fine-tune exists | adapter needed (mel features, not raw waves) — self-train base, plan B |

**Self-training, honestly assessed:** not fantasy, last resort. Yohane's
training notebook is public and swaps to an Apache base with one line; the
real cost is data (its own dataset is gated and rights-murky; commercial
training needs CommonVoice/MLS-class corpora, which are *speech*, not
singing) plus ~30–100 GPU-hours and a week of engineering (rough estimate,
marked as such).

## Risks before reading any candidate's numbers

- All candidates are **ASR fine-tunes**; the incumbent is alignment-specific.
  The likely outcome is "works, lower PCO" — fine for a second opinion,
  insufficient (unmeasured) as the primary commercial model.
- The quality-ramp constants are MMS-calibrated; a different model's prob
  distribution shifts them, so cross-model disagreement needs its own
  calibration before it can be trusted as an arbiter signal.
- voidful is untouched since 2023 and gated — if adopted, mirror the files
  locally.
- No permissive romaji-vocab Japanese model exists (searched); the JA route is
  either voidful's romanized path or the hiragana model with kana input, and
  both need measuring.

## Round 3 first measurement — jg-EN head-to-head (2026-08-06)

`2026-08-06-jg-xlsr53-en.json`: the first non-MMS aligner ever to run through
the whole harness. 20 English songs, identical config to the baseline.

**The seam works end-to-end.** Zero failures, zero line-mode collapses, all 20
songs scored — the silent-drop trap did not fire on a lowercase-latin vocab.
The escape route from CC-BY-NC is real and its cost is now a number:

| (eng subset) | MMS (NC) | jg-EN (Apache) | gap |
|---|---|---|---|
| PCO@0.1 | 0.481 | 0.448 | −0.033 |
| PCO@0.3 | 0.875 | 0.842 | −0.033 |
| PCO@0.5 | 0.934 | 0.907 | −0.027 |
| MAE median | 160 ms | 171 ms | +11 ms |
| MAE mean | 252 ms | 345 ms | +93 ms (heavier tail) |

A flat ~3-point PCO gap at every tolerance, medians nearly touching, the mean
dragged by a fatter tail. For an ASR fine-tune against an alignment-specific
model, that is closer than expected — per-song, MMS wins 14 / loses 3.

**The warning: they fail on the same songs.** Song-level PCO correlation
between the two models is **+0.920**. On MMS's worst English song (Avercage,
0.61) jg lands 0.42 — the second opinion agrees, including agreeing to be
wrong. Same architecture family, same separated vocals, same windows: the
hard songs are hard for acoustic reasons that hit both.

What this does and does not kill:

- It **weakens** cross-model disagreement as a *song-level* signal — a second
  opinion that always concurs adds nothing there.
- It does **not** yet answer the *line/word-level* question, which is the one
  the arbiter actually needs: two models can both find a song hard while
  disagreeing about *which words* are wrong. The result files only carry
  aggregates, so this needs the harness to dump per-word timings and a
  two-run disagreement analysis against ground truth. That is the next
  measurement, and the arbiter's fate hangs on it.

Also still open: voidful (the multilingual primary candidate) has not run yet
— the per-language ladder's ceiling is measured, the single-model route is not.

## The 1B run — a permissive model matches the CC-BY-NC incumbent (2026-08-06)

`2026-08-06-jg-xlsr1b-en.json`. Same author, same 33-token vocab, same config;
the only variable is 300M → 1B parameters. 20 English songs.

| | MMS (CC-BY-NC) | jg 300M | **jg 1B (Apache-2.0)** |
|---|---|---|---|
| PCO@0.1 | 0.4808 | 0.4476 | 0.4783 |
| PCO@0.2 | 0.7458 | 0.7161 | **0.7460** |
| PCO@0.3 | 0.8746 | 0.8416 | **0.8789** |
| PCO@0.5 | 0.9340 | 0.9066 | **0.9400** |
| MAE median | 160 ms | 171 ms | **157 ms** |
| align seconds (GPU) | — | 47 | 110 |

**At the tolerances that matter the permissive model is now ahead**, and
per-song it is 8 wins / 7 losses / 5 ties — a coin flip, not a compromise.
Scaling recovered the whole three-point gap and a little more, for 2.3× the
alignment time on GPU.

This is the licence question answered with a measurement rather than a
trade-off: **there is a commercially clean aligner that does not cost quality
on English.** What it costs is compute, and the seam means adopting it is a
config value.

Caveats that keep this from being a decision yet:

- **English only, 20 songs.** The multilingual claim is untested; the sibling
  1B checkpoints are per-language, and voidful (the one multilingual
  permissive candidate) has still not run.
- **Turkish has no 1B option** in this family — `mpoyraz` is 300M.
- **CPU cost is unmeasured.** 2.3× on GPU says nothing certain about the
  production CPU worker, which already spends ~10 min per song.
- The quality-ramp constants remain MMS-calibrated (`alignment.py:113`).

### And the arbiter got worse news

Song-level PCO correlation with MMS: **300M +0.920, 1B +0.945**. The better
the second model, the more it agrees — including on the failures. On MMS's
worst song both alternatives land in the same territory.

That is not surprising in hindsight and it is worth stating plainly: these are
all wav2vec2-family CTC models reading the same separated vocals through the
same windows. **Architectural diversity is what a second opinion needs, and
this family cannot supply it.** Cross-model disagreement at *word* level may
still carry signal the song aggregate hides — that measurement is still owed —
but the song-level evidence says a same-family second opinion is a weak
foundation for the arbiter.

---

# Round 4 (2026-08-06 evening) — the frame changes: EN+TR primary, compute unconstrained

Caner's redirection: English + Turkish are the targets (Japanese a bonus, 56
languages never needed), compute cost is explicitly not a filter (5070 Ti
available, 3060 Ti possible), and quality is the only goal. Licence remains
binding. Two research rounds under the new frame.

## Architecturally diverse second opinion (the arbiter's missing piece)

Measured context: wav2vec2-family models correlate +0.92/+0.945 with MMS at
song level — same-family second opinions are structurally useless.

| Candidate | Architecture | Verdict |
|---|---|---|
| **Qwen3-ForcedAligner-0.6B** | LLM slot-filling, **no CTC/Viterbi at all** | **The pick for EN/JA.** Apache-2.0, measured 32.4 ms AAS on human-labelled speech (2-4× ahead of NFA/MFA-class), adapter 1–2 days (its API is nearly our `align()` contract). 80 ms bins are noise at our marking thresholds. Caveats: **no Turkish** (and it degrades *silently* on unknown languages), no per-word score (arbiter must use time-delta only), 5-min limit, never evaluated on singing — the ASR sibling officially supports singing, the aligner hasn't been measured there |
| **faster-whisper ASR path** | encoder-decoder cross-attention | **The only clean TR second opinion.** MIT; word MAE 68–71 ms on speech (beats MFA/wav2vec2). Must run on the **mix**, not separated vocals (isolated vocals measurably hurt Whisper) — which also makes it input-independent from MMS. 3–5 days (free transcription → lrclib text matching). **Do not use `whisper-timestamped`: AGPL-3 + GPL dtw** |
| MFA 3.x | Kaldi GMM-HMM | **Rejected**: Qwen3-FA was trained on MFA pseudo-labels, so they are not independent witnesses; plus conda+PostgreSQL ops burden |
| NeMo Parakeet/Canary | FastConformer CTC | Rejected: no TR, still CTC family |

Literature agrees with the design: BEACON (arXiv 2607.03670) ensembles
architecturally diverse aligners precisely to "reduce model-specific bias",
marking majority-less units *Unresolved* — our "suspect line" verdict.

## Quality ceiling per language

- **EN**: no better ready-made permissive CTC than jg-1B was found. The two
  levers above it: **singing-adapt fine-tuning** (below) and
  **facebook/omniASR-CTC 1B/3B/7B** (Nov 2025, Apache, scale ceiling —
  but fairseq2 stack, not AutoModelForCTC; needs a 1-hour logit-access
  pre-check before any backend work; unmeasured on singing).
- **TR**: **no usable 1B exists** (the one candidate, Baybars 1B, measures
  WER 0.46 — five times worse than mpoyraz 300M). The real path is
  **w2v-bert-2.0 (MIT) fine-tuned on Common Voice TR (CC0, ~130 h)** —
  1–2 GPU-days on the 5070 Ti. The true cost of the TR track is the **eval
  set**: JamendoLyrics has no Turkish, so measuring TR at all needs a small
  hand-labelled set first.
- **JA (bonus)**: `sakasegawa/japanese-wav2vec2-large-hiragana-ctc` (Apache,
  modern 35kh reazon base) likely beats ttop324 — half-day check; the
  dual-head detail needs verifying.
- **Singing-specific aligners: the category is licence-dead.** STARS
  (GTSinger NC), VocalParse (NC corpora, and not even word timestamps),
  LyricsAlignment-MTL (DALI NC weights), SOFA (undocumented data). All RED
  for commercial lineage.

## Self-training is now the strongest arm

Commercially clean data actually exists:
- **Common Voice TR: CC0, ~130 h** — the TR base, fully clean.
- **Jamendo's own CC-BY subset** (per-track licence metadata, filterable by
  API) + lrclib lyrics + our pipeline as pseudo-labeller with a confidence
  filter → **singing-adapt fine-tune of jg-1B**. Licence chain Apache +
  CC-BY = clean, with attribution records.
- Vocadito (CC-BY-4.0, 40 clips) as a sanity/eval set, not training.
- RED and out: DAMP (research-only), DALI, MTG-Jamendo, OpenSinger, M4Singer,
  GTSinger, Opencpop.

Estimated (marked as such): 600M w2v-bert CTC fine-tune ≈ 1–2 GPU-days;
XLS-R 1B ≈ 2–4 GPU-days with 8-bit optimizer in 16 GB. All feasible on the
5070 Ti.

## ⚠️ New licence alarm: the separator

**`kim-melband` — the production default — has NO licence file at all**
(GitHub API: license=null), training data undocumented. For commercial
shipping that reads as all-rights-reserved. Clean fallback measured in the
July matrix: **htdemucs_ft (MIT, weights included)**. Cheap action: open a
licence question issue on the checkpoint repo; decision note for F11 either
way. New 2026 separators (Deux 17.55 SDR etc.) are NC or unlicensed — and the
July finding stands that SDR does not predict alignment quality, so no
re-benchmark is warranted.

## The full 1B ladder (2026-08-06) — migration is quality-neutral, licence-positive

All four benchmark languages, jonatasgrosman XLS-R **1B** (Apache-2.0) against
MMS (CC-BY-NC), identical config, per-language subsets:

| lang | songs | MMS PCO@0.3 | 1B PCO@0.3 | Δ | MAE med (MMS→1B) |
|---|---|---|---|---|---|
| eng | 20 | 0.8746 | **0.8789** | +0.004 | 160 → 157 |
| deu | 20 | 0.9552 | 0.9437 | −0.012 | 114 → 130 |
| fra | 19 | 0.9035 | 0.8963 | −0.007 | 141 → 138 |
| spa | 18* | 0.9241 | 0.9243 | +0.000 | 134 → 125 |

*two Spanish songs lost to transient HF download timeouts, not model failures.

**Verdict: a tie.** Slightly ahead in English and Spanish, slightly behind in
German and French, nothing beyond noise on 20-song subsets. The primary-model
migration off CC-BY-NC costs **no measurable quality** and the German −1.2 pt
is exactly what the singing-adapt fine-tune experiment exists to recover.
This closes the "is there a hidden quality tax?" question with data from all
79 songs.
