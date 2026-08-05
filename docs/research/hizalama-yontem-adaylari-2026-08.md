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
