"""Forced alignment: lyrics text + audio -> word timings, regrouped into lines.

The aligner knows nothing about lines. It gets the whole lyric as one stream of
whitespace tokens and returns one segment per token; `regroup_words_into_lines`
walks those segments back into the original line structure. That function is
pure and carries the tricky rules (star tokens, monotonicity, ms rounding), so
it is unit-tested without torch.

If the token accounting ever fails to line up, the job does NOT fail: we emit a
line-level document instead (the overlay already renders those).
"""

import logging
import math
import os
from dataclasses import dataclass
from pathlib import Path

from kashi_server.pipeline.japanese import PreparedLine, prepare_line
from kashi_server.pipeline.windows import plan_windows, reconcile_seams
from kashi_server.vdl_kit.errors import PipelineError

logger = logging.getLogger(__name__)

MODEL_NAME = "MahmoudAshraf/mms-300m-1130-forced-aligner"
STAR_TOKEN = "<star>"

# Model name -> (model, tokenizer). Keyed rather than a pair of globals so the
# seam can route per language later without reloading on every job (Faz 8
# P-B1); today exactly one entry is ever populated.
_loaded: dict[str, tuple] = {}


@dataclass(frozen=True)
class AlignedWord:
    start_ms: int
    end_ms: int
    text: str
    prob: float


@dataclass(frozen=True)
class LineTiming:
    start_ms: int
    end_ms: int
    text: str
    score: float


@dataclass(frozen=True)
class AlignResult:
    sync: str  # "word" | "line"
    lines: list[LineTiming]
    words_per_line: list[list[AlignedWord]]
    quality_score: float
    # True only when window-anchored alignment ACTUALLY ran (plan_windows can
    # decline and fall back to whole-audio) — document provenance keys off it.
    windowed: bool = False
    # WHICH aligner produced these timings — carried so the document records
    # what actually ran rather than a hardcoded guess (Faz 8 P-B1).
    model_name: str = MODEL_NAME
    # WHICH FORMULA produced quality_score. Carried here, next to the number,
    # rather than derived downstream: `windowed` was standing in for it in
    # document.py, and both line-mode exits preserve `windowed` while returning
    # a prob-based score — so word-less documents shipped stamped "anchors"
    # with a 1.0 (Faz 8 audit: nine of ten such documents scored >= 0.94).
    # A proxy that is right most of the time is the bug; the producer knows.
    quality_basis: str = "ctc-probs"


def resolve_model_name(override: str | None = None) -> str:
    """Which aligner this run uses. Pure; the single place that answers it."""
    if override:
        return override
    from kashi_server.config import settings

    return settings.align_model or MODEL_NAME


def _load_model(model_name: str):
    """Loaded once per worker process per model; the weights are ~1.2 GB."""
    if model_name not in _loaded:
        # The [align] extra — absent in plain dev installs, present in the image.
        import torch  # pyright: ignore[reportMissingImports]
        from ctc_forced_aligner import load_alignment_model  # pyright: ignore[reportMissingImports]

        # Prod images ship CPU torch, so the default never changes behaviour;
        # the GPU benchmark image opts in with KASHI_ALIGN_DEVICE=cuda.
        device = os.environ.get("KASHI_ALIGN_DEVICE", "cpu")
        logger.info("loading alignment model %s (%s)", model_name, device)
        _loaded[model_name] = load_alignment_model(
            device=device, model_path=model_name, dtype=torch.float32
        )
    return _loaded[model_name]


def _word_prob(score: float) -> float:
    """Aligner scores are average log-probabilities."""
    return min(1.0, math.exp(score))


# Quality calibration (measured 2026-07-10 on cnr-intel, MMS-300M, full mixes):
#   correct lyrics, real song (Never Gonna Give You Up): mean word-prob 0.078
#   WRONG lyrics, same audio (different song's text):     mean word-prob 0.029
#   clean speech fixture:                                 mean word-prob 0.32
# Raw CTC probabilities are tiny on music even when the timings are visibly
# right, so a naive mean would put every real song under the client's 0.5
# line-mode gate. The document/line score therefore maps the mean through a
# log ramp anchored at the measurements above: wrong-lyrics territory -> ~0.2,
# correctly aligned full mix -> ~0.7, clean vocals -> 1.0. The 0.5 client
# contract itself never moves (plan R-F3-7); only this mapping is tunable.
_QUALITY_LOW_MEAN = 0.02
_QUALITY_HIGH_MEAN = 0.15


def quality_from_probs(probs: list[float]) -> float:
    if not probs:
        return 0.0
    mean = sum(probs) / len(probs)
    if mean <= 0.0:
        return 0.0
    ramp = (math.log(mean) - math.log(_QUALITY_LOW_MEAN)) / (
        math.log(_QUALITY_HIGH_MEAN) - math.log(_QUALITY_LOW_MEAN)
    )
    return min(1.0, max(0.0, ramp))


def _fold_units_onto_surfaces(chunk: list[AlignedWord], plan: PreparedLine) -> list[AlignedWord]:
    """Mora spans -> surface spans (Faz 8 P-B3). Pure.

    The aligner timed what it heard (morae); the document has to display what
    was written (宇宙). Each surface takes the start of its first mora and the
    end of its last, and the WEAKEST prob of the group — a surface is only as
    trustworthy as its shakiest mora, and averaging would let one confident
    kana hide a lost one.
    """
    folded: list[AlignedWord] = []
    cursor = 0
    for surface, count in zip(plan.surfaces, plan.units_per_surface, strict=True):
        owned = chunk[cursor : cursor + count]
        cursor += count
        if not owned:
            continue
        folded.append(
            AlignedWord(
                start_ms=owned[0].start_ms,
                end_ms=max(owned[-1].end_ms, owned[0].start_ms),
                text=surface,
                prob=min(w.prob for w in owned),
            )
        )
    return folded


def regroup_words_into_lines(
    line_texts: list[str],
    results: list[dict],
    plans: list["PreparedLine | None"] | None = None,
) -> tuple[list[LineTiming], list[list[AlignedWord]]] | None:
    """Walk per-word segments back into the original lines.

    Returns None when the token accounting disagrees with the text — the caller
    then degrades to line-level output rather than emitting bogus word timings.

    `plans` (Faz 8 P-B3) carries, per line, the split between what the SCREEN
    shows and what the ALIGNER was given. They are the same thing in English
    and different in Japanese, where the aligner is fed kana morae while the
    document must still display 宇宙. A line with a plan is counted in units
    and its word spans are folded back onto the surfaces that own them; a line
    without one takes the whitespace path unchanged.
    """
    words = [r for r in results if r.get("text") != STAR_TOKEN]
    expected = [
        len(plan.units) if plan else len(line.split())
        for line, plan in zip(line_texts, plans or [None] * len(line_texts), strict=True)
    ]
    if sum(expected) != len(words):
        logger.warning(
            "alignment token mismatch: %d text words vs %d aligned segments",
            sum(expected),
            len(words),
        )
        return None

    aligned: list[AlignedWord] = []
    for index, word in enumerate(words):
        start_ms = round(float(word["start"]) * 1000)
        end_ms = round(float(word["end"]) * 1000)
        # The aligner may overlap neighbours by a frame; clip so word spans stay
        # monotone (the renderer's active-word search assumes it).
        if index + 1 < len(words):
            next_start_ms = round(float(words[index + 1]["start"]) * 1000)
            end_ms = min(end_ms, next_start_ms)
        end_ms = max(end_ms, start_ms)
        aligned.append(
            AlignedWord(
                start_ms=start_ms,
                end_ms=end_ms,
                text=str(word["text"]),
                prob=_word_prob(float(word.get("score", 0.0))),
            )
        )

    lines: list[LineTiming] = []
    words_per_line: list[list[AlignedWord]] = []
    cursor = 0
    for text, count, plan in zip(
        line_texts, expected, plans or [None] * len(line_texts), strict=True
    ):
        chunk = aligned[cursor : cursor + count]
        cursor += count
        if not chunk:  # a line of pure punctuation; keep the text, borrow no time
            continue
        if plan is not None:
            # Fold the mora spans back onto the surfaces that own them, so the
            # document displays 宇宙 over the span of うちゅー rather than
            # three kana the listener never sees written.
            chunk = _fold_units_onto_surfaces(chunk, plan)
        score = quality_from_probs([w.prob for w in chunk])
        lines.append(
            LineTiming(
                start_ms=chunk[0].start_ms,
                end_ms=max(chunk[-1].end_ms, chunk[0].start_ms),
                text=text,
                score=score,
            )
        )
        words_per_line.append(chunk)
    return lines, words_per_line


def _line_only_fallback(
    line_texts: list[str], results: list[dict], windowed: bool, model_name: str = MODEL_NAME
) -> AlignResult:
    """Spread whatever segments we got across the lines, proportionally."""
    words = [r for r in results if r.get("text") != STAR_TOKEN]
    if not words:
        raise PipelineError("alignment_failed", "aligner produced no segments")
    total_words = sum(len(line.split()) for line in line_texts) or 1
    lines: list[LineTiming] = []
    cursor = 0
    for text in line_texts:
        share = max(1, round(len(text.split()) / total_words * len(words)))
        chunk = words[cursor : cursor + share] or words[-1:]
        cursor += share
        probs = [_word_prob(float(w.get("score", 0.0))) for w in chunk]
        lines.append(
            LineTiming(
                start_ms=round(float(chunk[0]["start"]) * 1000),
                end_ms=round(float(chunk[-1]["end"]) * 1000),
                text=text,
                score=quality_from_probs(probs),
            )
        )
    all_probs = [_word_prob(float(w.get("score", 0.0))) for w in words]
    return AlignResult(
        sync="line",
        lines=lines,
        words_per_line=[],
        quality_score=quality_from_probs(all_probs),
        windowed=windowed,
        model_name=model_name,
    )


def _align_texts(
    model, tokenizer, audio, texts: list[str], language: str, star_frequency: str = "segment"
) -> list[dict]:
    """One emissions+Viterbi pass over `audio` for `texts`. Results are
    [{start, end, text, score}] in SECONDS relative to the given audio."""
    from ctc_forced_aligner import (  # pyright: ignore[reportMissingImports]
        generate_emissions,
        get_alignments,
        get_spans,
        postprocess_results,
        preprocess_text,
    )

    emissions, stride = generate_emissions(model, audio, batch_size=4)
    tokens_starred, text_starred = preprocess_text(
        " ".join(texts),
        romanize=True,  # uroman: required by the multilingual MMS model
        language=language,
        split_size="word",
        star_frequency=star_frequency,
    )
    segments, scores, blank = get_alignments(emissions, tokens_starred, tokenizer)
    spans = get_spans(tokens_starred, segments, blank)
    return postprocess_results(text_starred, spans, stride, scores)


SAMPLES_PER_MS = 16  # load_audio normalizes to 16 kHz mono


def align(
    wav_path: Path,
    line_texts: list[str],
    language: str,
    synced_starts_ms: list[int | None] | None = None,
    model_name: str | None = None,
) -> AlignResult:
    """Whole-audio alignment, or — when line stamps are provided and viable —
    lrclib-anchored WINDOWED alignment (P3): each window is aligned
    independently, so a CTC lock loss cannot propagate past a window edge.
    The merged word stream then flows through the same regroup/fallback path
    as the whole-audio mode."""
    from ctc_forced_aligner import load_audio  # pyright: ignore[reportMissingImports]

    model_name = resolve_model_name(model_name)
    model, tokenizer = _load_model(model_name)
    audio = load_audio(str(wav_path), model.dtype, model.device)

    # What the aligner is GIVEN can differ from what the document displays
    # (Faz 8 P-B3). Japanese lines become kana morae, because MMS romanizes
    # through uroman and uroman reads kanji as Chinese — the model was being
    # shown pinyin for text that is sung in Japanese. Every other line is its
    # own alignment text, so this is a no-op outside Japanese.
    plans = [prepare_line(text) for text in line_texts]
    align_texts = [
        " ".join(p.units) if p else text
        for text, p in zip(line_texts, plans, strict=True)
    ]
    if any(plans):
        logger.info(
            "japanese mora path: %d of %d lines converted to kana",
            sum(1 for p in plans if p),
            len(plans),
        )

    plan = None
    if synced_starts_ms is not None:
        total_ms = audio.shape[-1] // SAMPLES_PER_MS
        plan = plan_windows(line_texts, synced_starts_ms, total_ms)

    if plan is None:
        results = _align_texts(model, tokenizer, audio, align_texts, language)
    else:
        logger.info("windowed alignment: %d windows over %d lines", len(plan), len(line_texts))
        merged: list[dict] = []
        for window in plan:
            piece = audio[
                ..., window.slice_start_ms * SAMPLES_PER_MS : window.slice_end_ms * SAMPLES_PER_MS
            ]
            texts = [align_texts[i] for i in window.line_indices]
            offset_s = window.slice_start_ms / 1000
            # "edges": star tokens at BOTH slice edges absorb the pad and the
            # inter-line gap, so forced alignment doesn't stretch real words
            # over non-vocal audio (measured: "segment" cost ~0.13 PCO here).
            for r in _align_texts(model, tokenizer, piece, texts, language, "edges"):
                if r.get("text") == STAR_TOKEN:
                    continue  # regroup drops them anyway; keep offsets word-only
                merged.append(
                    {**r, "start": float(r["start"]) + offset_s, "end": float(r["end"]) + offset_s}
                )
        results = reconcile_seams(merged)

    regrouped = regroup_words_into_lines(line_texts, results, plans)
    if regrouped is None:
        return _line_only_fallback(line_texts, results, plan is not None, model_name)

    lines, words_per_line = regrouped
    all_words = [word for chunk in words_per_line for word in chunk]
    if not all_words:
        raise PipelineError("alignment_failed", "no words survived regrouping")
    quality = quality_from_probs([word.prob for word in all_words])
    return AlignResult(
        sync="word",
        lines=lines,
        words_per_line=words_per_line,
        quality_score=quality,
        windowed=plan is not None,
        model_name=model_name,
    )
