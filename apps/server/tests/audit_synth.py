"""Synthetic songs for the audit tests: pseudo-words, lrclib-like records and
whole-song ear files. No real ids, no real lyrics.

The ear is built the way ``audit.ear --whole`` writes it: one token per letter
(MS_PER_LETTER apart), a ``|`` token between words, ``letter_prob`` per 20 ms
frame (high while a line is sung, low elsewhere).
"""

from __future__ import annotations

import random
from difflib import SequenceMatcher

VID = "synthVid001"
MS_PER_LETTER = 90
STRIDE_MS = 20.0
CONSONANTS = "bdfgklmnprstvz"
VOWELS = "aeiou"


def vocabulary(n: int, seed: int = 7, min_syllables: int = 2, max_syllables: int = 3) -> list[str]:
    """n pseudo-words, every pair less alike than the anchor matcher's fuzzy bar."""
    rng = random.Random(seed)
    out: list[str] = []
    while len(out) < n:
        word = "".join(rng.choice(CONSONANTS) + rng.choice(VOWELS)
                       for _ in range(rng.randint(min_syllables, max_syllables)))
        if all(SequenceMatcher(None, word, w, autojunk=False).ratio() < 0.6 for w in out):
            out.append(word)
    return out


def lines_of(vocab: list[str], n_lines: int, words_per_line: int = 5, seed: int = 3) -> list[str]:
    rng = random.Random(seed)
    return [" ".join(rng.sample(vocab, words_per_line)) for _ in range(n_lines)]


def stamp(ms: int) -> str:
    cs = round(ms / 10)
    return f"[{cs // 6000:02d}:{(cs // 100) % 60:02d}.{cs % 100:02d}]"


def record(paragraphs: list[list[tuple[str, int]]], record_id: int = 4242,
           duration_s: float = 200.0) -> dict:
    """An lrclib /api/get record: paragraphs of (text, start_ms), a blank line
    between paragraphs."""
    blocks = ["\n".join(f"{stamp(t)} {text}" for text, t in para) for para in paragraphs]
    return {"id": record_id, "duration": duration_s, "instrumental": False,
            "syncedLyrics": "\n\n".join(blocks), "plainLyrics": None}


def ear(sung: list[tuple[str, int]], total_ms: int | None = None,
        ms_per_letter: int = MS_PER_LETTER, extra_tokens: list[dict] | None = None,
        model: str = "synthetic-ctc") -> dict:
    """A whole-song ear: each (text, heard start) sung at ms_per_letter."""
    tokens: list[dict] = []
    spans: list[tuple[int, int]] = []
    for text, t0 in sung:
        t = t0
        for k, word in enumerate(text.split()):
            if k:
                tokens.append({"text": "|", "start_ms": t, "end_ms": t})
            for ch in word:
                tokens.append({"text": ch, "start_ms": t, "end_ms": t + 20})
                t += ms_per_letter
        tokens.append({"text": "|", "start_ms": t, "end_ms": t})
        spans.append((t0, t))
    tokens += extra_tokens or []
    tokens.sort(key=lambda tok: tok["start_ms"])
    end = total_ms or (max(e for _, e in spans) + 5000 if spans else 10_000)
    frames = int(end / STRIDE_MS)
    prob = [0.02] * frames
    for s, e in spans:
        for i in range(max(0, int(s / STRIDE_MS)), min(frames, int(e / STRIDE_MS) + 1)):
            prob[i] = 0.9
    for tok in extra_tokens or []:
        i = int(tok["start_ms"] / STRIDE_MS)
        if 0 <= i < frames:
            prob[i] = max(prob[i], 0.6)
    return {"id": VID, "model": model, "start_ms": 0, "stride_ms": STRIDE_MS,
            "text": "", "tokens": tokens, "letter_prob": prob,
            "letter_prob_max": max(prob) if prob else 0.0, "frames": frames, "finite": True}


def letter_tokens(text: str, t0: int, ms_per_letter: int) -> list[dict]:
    """Loose ear tokens (no line), words split by '|'."""
    out, t = [], t0
    for k, word in enumerate(text.split()):
        if k:
            out.append({"text": "|", "start_ms": t, "end_ms": t})
        for ch in word:
            out.append({"text": ch, "start_ms": t, "end_ms": t + 20})
            t += ms_per_letter
    return out


def document(lines: list[tuple[str, int]], method: str = "ctc-forced-aligner/synthetic",
             source_id: int = 4242, ms_per_letter: int = MS_PER_LETTER) -> dict:
    """A word document whose words sit where (text, start) says."""
    out = []
    for text, t0 in lines:
        words, t = [], t0
        for w in text.split():
            words.append({"text": w, "start_ms": t, "end_ms": t + len(w) * ms_per_letter - 10})
            t += len(w) * ms_per_letter
        out.append({"text": text, "start_ms": t0, "end_ms": t, "score": 0.9, "words": words})
    return {"pipeline_version": "2.29.0", "sync": "word",
            "track": {"source": {"type": "youtube", "id": VID}, "title": "Synth",
                      "artist": "Synth"},
            "alignment": {"method": method, "quality_score": 0.8, "lyrics_source_id": source_id},
            "lines": out}
