"""One picture (+ its numbers) per audit moment.

Panels, top to bottom, on ONE song-clock axis (= player position):
  1. separated-vocal mel spectrogram, vocal loudness (dB re song max) with
     line QA's silence threshold, librosa onsets (thin), voice onsets (where
     the voice returns after silence; thick);
  2. the model's ear: P(some letter) per frame and its greedy letters;
  3. one lane per document version: word boxes (start..end), flagged words,
     and under them when the SCREEN lights each word (start - offset);
  4. the overlay strip: one row per frame, what is lit at that player time.

    python -m audit.render <id> <t_ms> --doc 2.28=<dump>,2.29=<dump> \
        --strip <frames.json> [--real-dir <frame pngs>] [--out-dir <dir>]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from audit.analysis import SongAnalysis, analyse
from audit.docs import load_document
from audit.ear import ear_path, whole_path
from audit.moment import ear_letters_in, line_numbers, lines_in, word_flags
from audit.paths import RUNS_DIR
from audit.stems import decode_16k

HALF_MS = 2500
WIDTH_PX = 1568  # the long side the agent's image reader keeps
DPI = 100
VOICE_ONSET_MATCH_MS = 1500

THEMES = {
    # Purple night: tinted dark ground, purple accent, magma spectrogram.
    "mor": {
        "bg": "#16121e", "panel": "#1d1828", "text": "#ece6f5", "muted": "#8f86a3",
        "accent": "#b48cff", "voice": "#4fd1c5", "onset": "#5d5670", "energy": "#d8c8ff",
        "zero": "#ff5c7a", "short": "#ffb347", "box": "#6f5aa8", "moment": "#ffffff",
        "sung": "#ece6f5", "rest": "#5f5872", "active": "#b48cff", "fx": "#ffd166",
        "cmap": "magma",
    },
    # Quiet grey: neutral ground, colour only where it carries a role.
    "sade": {
        "bg": "#121316", "panel": "#1a1c20", "text": "#e6e7ea", "muted": "#7d8189",
        "accent": "#a78bfa", "voice": "#34d399", "onset": "#4b4f57", "energy": "#cbd5e1",
        "zero": "#f87171", "short": "#fbbf24", "box": "#52576a", "moment": "#e6e7ea",
        "sung": "#e6e7ea", "rest": "#5b606b", "active": "#a78bfa", "fx": "#fbbf24",
        "cmap": "viridis",
    },
    # High contrast: grey spectrogram so every coloured mark stands out.
    "kontrast": {
        "bg": "#0b0b0d", "panel": "#111114", "text": "#ffffff", "muted": "#9a9aa3",
        "accent": "#c084fc", "voice": "#22d3ee", "onset": "#6b6b75", "energy": "#fde68a",
        "zero": "#ff3b5c", "short": "#ff9f1c", "box": "#7c3aed", "moment": "#ffffff",
        "sung": "#ffffff", "rest": "#55555e", "active": "#e879f9", "fx": "#fde047",
        "cmap": "gray",
    },
}
THEME = dict(THEMES["mor"])


# Label layout: ~36 ms of axis per character at 13 pt on a 5 s, 1450 px axis.
MS_PER_CHAR = 36
LABEL_GAP_MS = 30


def place_labels(items: list[tuple[int, str]], rows: int) -> list[int]:
    """Row per (start_ms, text) so labels in one row do not overlap; greedy,
    first row that is free, else the row that frees up first."""
    ends = [-(10**9)] * rows
    out = []
    for start, text in items:
        free = [r for r in range(rows) if ends[r] + LABEL_GAP_MS <= start]
        row = free[0] if free else min(range(rows), key=lambda r: ends[r])
        ends[row] = start + len(text) * MS_PER_CHAR
        out.append(row)
    return out


def ear_words(tokens: list[dict]) -> list[tuple[int, str]]:
    """The model's greedy letters grouped into its own words (split on '|')."""
    words: list[tuple[int, str]] = []
    current: list[dict] = []
    for tok in tokens + [{"text": "|", "start_ms": 0, "end_ms": 0}]:
        if tok["text"] == "|":
            if current:
                words.append((current[0]["start_ms"], "".join(t["text"] for t in current)))
            current = []
        else:
            current.append(tok)
    return words


def _setup_matplotlib():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({
        "font.size": 13, "axes.facecolor": THEME["panel"], "figure.facecolor": THEME["bg"],
        "axes.edgecolor": THEME["muted"], "axes.labelcolor": THEME["text"],
        "xtick.color": THEME["text"], "ytick.color": THEME["text"], "text.color": THEME["text"],
        "axes.titlecolor": THEME["text"],
    })
    return plt


def _spectrogram(video_id: str, t0: int, t1: int):
    import librosa
    import numpy as np

    y, sr = librosa.load(str(decode_16k(video_id)), sr=16000, mono=True,
                         offset=max(0, t0) / 1000, duration=(t1 - max(0, t0)) / 1000)
    mel = librosa.feature.melspectrogram(y=y, sr=sr, n_fft=1024, hop_length=160, n_mels=96,
                                         fmax=8000)
    return librosa.power_to_db(mel, ref=np.max), max(0, t0)


def load_ear(video_id: str, t_ms: int) -> dict | None:
    """The moment's own ear clip, else the window cut from the whole-song ear."""
    clip = ear_path(video_id, t_ms)
    if clip.exists():
        return json.loads(clip.read_text())
    whole = whole_path(video_id)
    if not whole.exists():
        return None
    ear = json.loads(whole.read_text())
    stride = ear["stride_ms"]
    lo = max(0, t_ms - HALF_MS)
    i0 = int(lo / stride)
    i1 = int((t_ms + HALF_MS) / stride) + 1
    ear["letter_prob"] = ear["letter_prob"][i0:i1]
    ear["start_ms"] = round(i0 * stride)
    ear["tokens"] = [t for t in ear["tokens"] if lo <= t["start_ms"] <= t_ms + HALF_MS]
    ear["text"] = "".join(t["text"] for t in ear["tokens"]).replace("|", " ").strip()
    ear["t_ms"] = t_ms
    return ear


def doc_beats(doc: dict, t0: int, t1: int) -> list[tuple[int, bool]]:
    """(time, is bar start) for the document's beats inside [t0, t1]."""
    beats = doc.get("beats") or {}
    times = beats.get("times_ms") or []
    downs = set(beats.get("downbeat_indices") or [])
    return [(t, i in downs) for i, t in enumerate(times) if t0 <= t <= t1]


def build_numbers(video_id: str, t_ms: int, versions: list[tuple[str, dict]],
                  song: SongAnalysis, ear: dict | None, strip: dict | None,
                  offset_ms: int) -> dict:
    t0, t1 = t_ms - HALF_MS, t_ms + HALF_MS
    out: dict = {"id": video_id, "t_ms": t_ms, "offset_ms": offset_ms, "versions": {}}
    for label, doc in versions:
        rows = []
        for li, line in lines_in(doc, t0, t1):
            words = line.get("words") or []
            start = words[0]["start_ms"] if words else line["start_ms"]
            vo = song.nearest(song.voice_onsets, start)
            if vo is not None and abs(vo - start) > VOICE_ONSET_MATCH_MS:
                vo = None
            row = line_numbers(line, offset_ms, vo, song.nearest(song.onsets, start),
                               song.level_at(start))
            row["line"] = li
            row["ear_letters"] = ear_letters_in(ear, line["start_ms"], line["end_ms"])
            rows.append(row)
        out["versions"][label] = {"pipeline_version": doc.get("pipeline_version"), "lines": rows}
    out["voice_onsets_ms"] = [v for v in song.voice_onsets if t0 <= v <= t1]
    beats = doc_beats(versions[-1][1], t0, t1)
    out["beats_ms"] = [t for t, _ in beats]
    out["bar_starts_ms"] = [t for t, down in beats if down]
    if ear:
        out["ear"] = {"model": ear["model"], "text": ear["text"]}
    if strip:
        out["strip"] = [
            {"t": f["t"], "line": f["line"], "active": f["active"],
             "lit": f["words"][f["active"]] if f["active"] >= 0 else None,
             "sung": [f["words"][i] for i in f["sung"]], "fx_fired": f.get("fx_fired")}
            for f in strip["frames"]
        ]
    return out


def render(video_id: str, t_ms: int, versions: list[tuple[str, dict]], strip: dict | None,
           real_dir: Path | None, out_png: Path, offset_ms: int) -> dict:
    plt = _setup_matplotlib()
    from matplotlib import gridspec
    from matplotlib.patches import Rectangle

    song = analyse(video_id)
    ear = load_ear(video_id, t_ms)
    numbers = build_numbers(video_id, t_ms, versions, song, ear, strip, offset_ms)
    t0, t1 = t_ms - HALF_MS, t_ms + HALF_MS
    frames = strip["frames"] if strip else []
    use_real = real_dir is not None and all((real_dir / f"frame-{f['t']}.png").exists()
                                            for f in frames)
    row_h = 0.62 if use_real else 0.36
    heights = [2.8, 1.1] + [1.3] * len(versions) + ([row_h * len(frames) + 0.3] if frames else [])
    fig = plt.figure(figsize=(WIDTH_PX / DPI, sum(heights) + 0.7), dpi=DPI)
    grid = gridspec.GridSpec(len(heights), 1, height_ratios=heights, hspace=0.12,
                             left=0.06, right=0.985, top=1 - 0.55 / (sum(heights) + 0.7),
                             bottom=0.02)
    doc0 = versions[-1][1]
    title = (f"{doc0['track'].get('artist', '')} – {doc0['track'].get('title', '')}   "
             f"t={t_ms / 1000:.2f}s   offset +{offset_ms} ms (screen lights a word "
             f"{offset_ms} ms before its box)")
    fig.suptitle(title, fontsize=15, x=0.06, ha="left", y=0.995)

    def axis(i, sharex=None):
        ax = fig.add_subplot(grid[i], sharex=sharex)
        ax.set_xlim(t0, t1)
        ax.axvline(t_ms, color=THEME["moment"], lw=1.2, ls="--", alpha=0.7)
        for v in song.voice_onsets:
            if t0 <= v <= t1:
                ax.axvline(v, color=THEME["voice"], lw=2.2, alpha=0.85)
        for f in frames:
            ax.axvline(f["t"], color=THEME["accent"], lw=0.6, alpha=0.35)
        return ax

    # 1. spectrogram + loudness + onsets
    ax = axis(0)
    spec, s0 = _spectrogram(video_id, t0, t1)
    ax.imshow(spec, origin="lower", aspect="auto", cmap=THEME["cmap"],
              extent=(s0, s0 + spec.shape[1] * 10, 0, 8000))
    ax.set_ylim(0, 8000)
    ax.set_ylabel("Hz")
    for o in song.onsets:
        if t0 <= o <= t1:
            ax.axvline(o, ymin=0.9, ymax=1, color=THEME["energy"], lw=1)
    # The document's beat grid along the bottom; bar starts (downbeats) taller.
    for t, down in doc_beats(doc0, t0, t1):
        ax.axvline(t, ymin=0, ymax=0.16 if down else 0.09, color=THEME["text"],
                   lw=3.2 if down else 1.8)
    ex = ax.twinx()
    pts = [(t, db) for t, db in song.energy if t0 <= t <= t1]
    ex.plot([p[0] for p in pts], [p[1] for p in pts], color=THEME["energy"], lw=1.6)
    ex.axhline(-25, color=THEME["energy"], lw=0.8, ls=":")
    ex.set_ylim(-60, 2)
    ex.set_ylabel("dB")
    for k, f in enumerate(frames, 1):
        ax.text(f["t"], 7600, str(k), color=THEME["accent"], fontsize=12, ha="center",
                fontweight="bold")
    ax.tick_params(labelbottom=False)

    # 2. the model's ear
    ax_e = axis(1, sharex=ax)
    if ear:
        stride = ear["stride_ms"]
        xs = [ear["start_ms"] + i * stride for i in range(len(ear["letter_prob"]))]
        ax_e.fill_between(xs, ear["letter_prob"], color=THEME["voice"], alpha=0.35, step="post")
        spoken = [(t, w) for t, w in ear_words(ear["tokens"]) if t0 <= t <= t1]
        for (t, w), row in zip(spoken, place_labels(spoken, 2), strict=True):
            ax_e.text(t, 0.62 + 0.24 * row, w, fontsize=13, fontfamily="monospace",
                      color=THEME["text"], va="bottom")
        ax_e.set_ylabel("ear")
    else:
        ax_e.text(t0 + 50, 0.4, "no ear data", color=THEME["muted"])
    ax_e.set_ylim(0, 1.05)
    ax_e.set_yticks([])
    ax_e.tick_params(labelbottom=False)

    # 3. word boxes per version
    for vi, (label, doc) in enumerate(versions):
        axv = axis(2 + vi, sharex=ax)
        axv.set_ylim(0, 1)
        axv.set_yticks([])
        axv.set_ylabel(label, rotation=0, ha="right", va="center", fontsize=14)
        for _li, line in lines_in(doc, t0, t1):
            words = line.get("words") or []
            axv.axvline(line["start_ms"], ymin=0.16, ymax=1, color=THEME["muted"], lw=1.4)
            if not words:
                axv.text(max(line["start_ms"], t0), 0.5, f"[no words] {line['text']}",
                         color=THEME["muted"], fontsize=12)
                continue
            flags = word_flags(words)
            label_rows = place_labels([(w["start_ms"], w["text"]) for w in words], 3)
            for wi, (w, fl) in enumerate(zip(words, flags, strict=True)):
                s, e = w["start_ms"], w["end_ms"]
                colour = THEME["zero"] if "zero" in fl else THEME["short"] if "short" in fl \
                    else THEME["box"]
                if e > s:
                    axv.add_patch(Rectangle((s, 0.2), e - s, 0.22, color=colour, alpha=0.9))
                else:
                    axv.plot([s], [0.31], marker="v", color=colour, markersize=11)
                axv.text(s, 0.47 + 0.17 * label_rows[wi], w["text"], fontsize=12, va="bottom",
                         color=THEME["zero"] if "pile" in fl else THEME["text"])
                # When the screen lights it: start - offset, until the next word does.
                nxt = words[wi + 1]["start_ms"] if wi + 1 < len(words) else line["end_ms"]
                lit0, lit1 = s - offset_ms, nxt - offset_ms
                if lit1 > lit0:
                    axv.add_patch(Rectangle((lit0, 0.03), lit1 - lit0, 0.11,
                                            color=THEME["accent"], alpha=0.3 + 0.45 * (wi % 2)))
                else:
                    axv.plot([lit0], [0.085], marker="|", color=THEME["zero"], markersize=14)
        axv.tick_params(labelbottom=(vi == len(versions) - 1 and not frames))

    # 4. the strip
    if frames:
        axs = fig.add_subplot(grid[-1])
        axs.set_xlim(0, 1)
        axs.set_ylim(len(frames), -0.3)
        axs.axis("off")
        for k, f in enumerate(frames, 1):
            y = k - 0.5
            axs.text(0.0, y, f"{k:>2}  {f['t'] / 1000:7.2f}s", fontsize=13, va="center",
                     fontfamily="monospace", color=THEME["accent"])
            if use_real and real_dir is not None:
                import matplotlib.image as mpimg

                img = mpimg.imread(str(real_dir / f"frame-{f['t']}.png"))
                hgt, wid = img.shape[0], img.shape[1]
                # Row = 1 data unit = row_h inches; keep the crop's aspect.
                axis_w_in = WIDTH_PX / DPI * (0.985 - 0.06)
                frac = min(0.42, 0.92 * row_h * wid / hgt / axis_w_in)
                axs.imshow(img, extent=(0.57, 0.57 + frac, y + 0.46, y - 0.46), aspect="auto")
            if f["line"] < 0:
                axs.text(0.15, y, "♪ (no line)", fontsize=13, va="center", color=THEME["muted"])
                continue
            if not f["words"]:
                # A line without word timings shows as plain, static text.
                axs.text(0.15, y, f"[plain] {f['text']}", fontsize=13, va="center",
                         color=THEME["sung"], fontfamily="monospace")
                continue
            # Monospace: a character is CHAR_W of the axis at 13 pt; shrink to fit.
            room = (0.55 if use_real else 0.84) - 0.15
            chars = sum(len(w) + 1 for w in f["words"])
            size = min(13.0, 13.0 * room / max(0.01, chars * 0.0075))
            x = 0.15
            for i, word in enumerate(f["words"]):
                colour = (THEME["active"] if i == f["active"] else
                          THEME["sung"] if i in f["sung"] else THEME["rest"])
                weight = "bold" if i == f["active"] else "normal"
                txt = axs.text(x, y, word, fontsize=size, va="center", color=colour,
                               fontfamily="monospace", fontweight=weight)
                if f.get("fx_fired") == i:
                    txt.set_bbox({"facecolor": "none", "edgecolor": THEME["fx"], "pad": 1})
                x += (len(word) + 1) * 0.0075 * size / 13.0
        # imshow re-fits the limits to each image's extent; put them back.
        axs.set_xlim(0, 1)
        axs.set_ylim(len(frames), -0.3)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=DPI, facecolor=THEME["bg"])
    plt.close(fig)
    return numbers


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render one audit moment.")
    parser.add_argument("id")
    parser.add_argument("t_ms", type=int)
    parser.add_argument("--doc", required=True,
                        help="label=path[,label=path...]; the LAST is the one the strip shows")
    parser.add_argument("--strip", help="frames.json from apps/overlay/scripts/audit-strip.mjs")
    parser.add_argument("--real-dir", help="directory with frame-<t>.png from --mode real")
    parser.add_argument("--offset", type=int, default=200)
    parser.add_argument("--out-dir", help="default: scratch/audit/runs/<id>")
    parser.add_argument("--theme", choices=sorted(THEMES), default="mor")
    parser.add_argument("--suffix", default="", help="appended to the output file names")
    args = parser.parse_args(argv)
    THEME.clear()
    THEME.update(THEMES[args.theme])
    versions = []
    for item in args.doc.split(","):
        label, path = item.split("=", 1)
        versions.append((label, load_document(Path(path), args.id)))
    strip = json.loads(Path(args.strip).read_text()) if args.strip else None
    out_dir = Path(args.out_dir) if args.out_dir else RUNS_DIR / args.id
    png = out_dir / f"moment-{args.t_ms}{args.suffix}.png"
    numbers = render(args.id, args.t_ms, versions, strip,
                     Path(args.real_dir) if args.real_dir else None, png, args.offset)
    (out_dir / f"moment-{args.t_ms}{args.suffix}.json").write_text(
        json.dumps(numbers, indent=1, ensure_ascii=False) + "\n")
    print(png)
    return 0


if __name__ == "__main__":
    sys.exit(main())
