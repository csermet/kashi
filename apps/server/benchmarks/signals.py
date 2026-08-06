"""Candidate confidence signals — the hunt for something that actually knows.

Why this exists (Faz 8, 2026-08-06). The shipped `quality_score` was measured
against ground truth on all 79 JamendoLyrics songs and correlates at Pearson
**+0.36** with real accuracy. It is the model grading its own homework: the
mean CTC probability simply does not carry the information. Worse, the
client's 0.5 gate destroys 11 accurate documents to catch 2 bad ones.

Recalibrating that ramp was never the fix. What is needed is INDEPENDENT
evidence — something that did not come out of the same forward pass. This
module computes candidates for it, and the benchmark writes them next to the
ground-truth error for each song, so the question "does this signal know
anything?" becomes a correlation rather than an opinion.

**Nothing here is wired into production, on purpose.** The defect being
repaired was shipping a metric nobody had validated; the same mistake in the
other direction is not an improvement. A signal earns its way in by beating
r=0.36 on this rig, and not before.

Everything here is free: librosa and the aligner's own output. No new model,
no new dependency. Paid candidates (a VAD model, a second aligner) only make
sense if the free ones fall short.
"""

import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Word-start deviation counted as "landing on" an onset. 100 ms is the
# subtitle-perception literature's floor for a noticeable offset; 50/200 are
# reported alongside so the tolerance itself can be chosen from data.
ONSET_TOLERANCES_MS = (50, 100, 200)
# A word whose per-character duration is this far from the song's median is
# implausible — the aligner either crushed it or stretched it over a gap.
DURATION_OUTLIER_FACTOR = 3.0


@dataclass(frozen=True)
class SongSignals:
    """One song's candidate signals. Flat and JSON-ready: the analysis that
    consumes it is a correlation against the same row's ground-truth error."""

    # --- probability tail. The MEAN is what today's score uses and it fails;
    # the question is whether the same numbers know more in their tail.
    prob_mean: float
    prob_median: float
    prob_p10: float
    prob_frac_below_01: float
    prob_frac_below_05: float
    # --- vocal onsets. Independent of the aligner: computed from the audio.
    onset_median_ms: float | None
    onset_within: dict[str, float] | None
    onset_count: int | None
    # --- internal plausibility. Free, and independent of both.
    duration_outlier_frac: float
    overlap_frac: float
    silence_frac: float


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))
    return ordered[index]


def probability_signals(probs: list[float]) -> dict:
    """Does the prob distribution know more than its mean?

    Today's ramp averages these, which lets a handful of confidently-aligned
    words carry a document whose hard parts were lost. The tail is the obvious
    place to look for the information the mean throws away.
    """
    if not probs:
        return {
            "prob_mean": 0.0,
            "prob_median": 0.0,
            "prob_p10": 0.0,
            "prob_frac_below_01": 1.0,
            "prob_frac_below_05": 1.0,
        }
    n = len(probs)
    return {
        "prob_mean": round(sum(probs) / n, 5),
        "prob_median": round(_percentile(probs, 0.5), 5),
        "prob_p10": round(_percentile(probs, 0.10), 5),
        "prob_frac_below_01": round(sum(1 for p in probs if p < 0.01) / n, 4),
        "prob_frac_below_05": round(sum(1 for p in probs if p < 0.05) / n, 4),
    }


def detect_onsets(audio_path: Path) -> list[int] | None:
    """Vocal onset times in ms, or None if the audio could not be read.

    Separated once per song and handed to every scope that needs it — the
    audio pass is the expensive part of this module and a per-line recompute
    would multiply it by forty.
    """
    try:
        import librosa

        y, sr = librosa.load(str(audio_path), sr=22050, mono=True)
        onsets = librosa.onset.onset_detect(y=y, sr=sr, units="time", backtrack=True)
    except Exception:  # a broken file must not end the sweep
        logger.exception("onset detection failed for %s", audio_path.name)
        return None
    return [round(float(t) * 1000) for t in onsets]


def onset_distances(onset_ms: list[int], word_starts_ms: list[int]) -> list[float]:
    """Distance from each word start to its nearest onset. Pure.

    Both lists are sorted, so the nearest onset is found by walking forward
    once rather than scanning per word.
    """
    if not onset_ms or not word_starts_ms:
        return []
    distances: list[float] = []
    cursor = 0
    for start in sorted(word_starts_ms):
        while cursor + 1 < len(onset_ms) and abs(onset_ms[cursor + 1] - start) <= abs(
            onset_ms[cursor] - start
        ):
            cursor += 1
        distances.append(abs(onset_ms[cursor] - start))
    return distances


def onset_summary(distances: list[float]) -> dict:
    """Distances -> the shape the correlation reads. Pure."""
    if not distances:
        return {"onset_median_ms": None, "onset_within": None}
    n = len(distances)
    return {
        "onset_median_ms": round(_percentile(distances, 0.5), 1),
        "onset_within": {
            str(tol): round(sum(1 for d in distances if d <= tol) / n, 4)
            for tol in ONSET_TOLERANCES_MS
        },
    }


def line_signals(result, onset_ms: list[int] | None) -> list[dict]:
    """Per-LINE candidates (Faz 8, 2026-08-06).

    The song-level hunt ended with document gating looking like the wrong
    frame: at a looser badness threshold every candidate destroyed dozens of
    good documents, and the archive's own damage is concentrated in a tail of
    lines while the median document is clean. A document is not uniformly good
    or bad, so asking a signal to judge one was asking the wrong question.

    The field complaint was never "this song is bad" — it was "these words
    drift". This is that question, at that scale.
    """
    rows: list[dict] = []
    for index, words in enumerate(result.words_per_line):
        row: dict = {"line": index, "n_words": len(words)}
        if not words:
            rows.append(row)
            continue
        probs = [w.prob for w in words]
        row.update(probability_signals(probs))
        if onset_ms:
            row.update(onset_summary(onset_distances(onset_ms, [w.start_ms for w in words])))
        span_ms = max(1, words[-1].end_ms - words[0].start_ms)
        row.update(plausibility_signals(words, span_ms))
        # The aligner's own per-line score, which line QA deliberately does NOT
        # use as a flagging signal (measured: good lines score 0.00). Carried
        # so that decision can be re-checked against ground truth rather than
        # inherited.
        if index < len(result.lines):
            row["line_score"] = round(result.lines[index].score, 5)
        rows.append(row)
    return rows


def onset_signals(audio_path: Path, word_starts_ms: list[int]) -> dict:
    """Song-level onset summary. Thin wrapper over the pieces above so the
    song and line scopes cannot drift apart."""
    onset_ms = detect_onsets(audio_path)
    if onset_ms is None:
        return {"onset_median_ms": None, "onset_within": None, "onset_count": None}
    summary = onset_summary(onset_distances(onset_ms, word_starts_ms))
    return {**summary, "onset_count": len(onset_ms)}


def plausibility_signals(words: list, total_duration_ms: int) -> dict:
    """Does the word stream look like singing, judged on its own shape?

    No model and no audio — just whether the spans are internally sane. A
    document whose words overlap, or whose durations scatter wildly around the
    song's own pace, is suspect regardless of how confident the aligner was.
    """
    spans = [(w.start_ms, w.end_ms, w.text) for w in words if w.text]
    if not spans:
        return {"duration_outlier_frac": 1.0, "overlap_frac": 1.0, "silence_frac": 1.0}

    per_char = [
        (end - start) / len(text) for start, end, text in spans if end > start and text
    ]
    outliers = 0.0
    if per_char:
        median_speed = _percentile(per_char, 0.5)
        if median_speed > 0:
            outliers = sum(
                1
                for speed in per_char
                if speed > median_speed * DURATION_OUTLIER_FACTOR
                or speed < median_speed / DURATION_OUTLIER_FACTOR
            ) / len(per_char)

    overlaps = sum(
        1 for (_, end, _), (start, _, _) in zip(spans, spans[1:], strict=False) if start < end
    )
    sung_ms = sum(max(0, end - start) for start, end, _ in spans)
    return {
        "duration_outlier_frac": round(outliers, 4),
        "overlap_frac": round(overlaps / max(1, len(spans) - 1), 4),
        # How much of the track carries no word at all. A document that lost a
        # section leaves a hole; one that smeared words over the instrumental
        # leaves none.
        "silence_frac": round(max(0.0, 1 - sung_ms / max(1, total_duration_ms)), 4),
    }


def collect(
    result, audio_path: Path, total_duration_ms: int, onset_ms: list[int] | None = None
) -> dict:
    """Every candidate for one song, flat and JSON-ready.

    `onset_ms` is accepted so the caller can detect once and share it with the
    per-line scope; omitted, it is detected here.
    """
    words = [w for chunk in result.words_per_line for w in chunk]
    signals: dict = {}
    signals.update(probability_signals([w.prob for w in words]))
    if onset_ms is None:
        signals.update(onset_signals(audio_path, [w.start_ms for w in words]))
    else:
        signals.update(onset_summary(onset_distances(onset_ms, [w.start_ms for w in words])))
        signals["onset_count"] = len(onset_ms)
    signals.update(plausibility_signals(words, total_duration_ms))
    return signals
