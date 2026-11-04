"""Synthetic songs for the audit tests: invented words, a perfect greedy ear.

The ear "hears" each sung line letter by letter at MS_PER_LETTER from its true
start plus the model's raw-clock LAG_MS (``audit.score.EAR_LAG_MS``), with a
word delimiter between words, and P(some letter) high exactly while a letter
sounds. With that ear a correctly stamped line reads offset 0 and a sheet
moved by x reads exactly -x, so thresholds can be tested on their boundary.
"""

import random

from audit.anchors import SheetLine
from audit.textmatch import letters

LAG_MS = 50
MS_PER_LETTER = 90
WORD_GAP_MS = 60
STRIDE_MS = 20.0
ALPHABET = "bcdfghklmnprstvz"
VOWELS = "aeiou"


def words(rng: random.Random, n: int) -> str:
    out = []
    for _ in range(n):
        out.append("".join(rng.choice(ALPHABET) + rng.choice(VOWELS)
                           for _ in range(rng.randint(2, 3))))
    return " ".join(out)


def lines(seed: int, n: int, words_per_line: int = 4) -> list[str]:
    rng = random.Random(seed)
    return [words(rng, words_per_line) for _ in range(n)]


def starts(n: int, first_ms: int = 5000, step_ms: int = 2500) -> list[int]:
    return [first_ms + k * step_ms for k in range(n)]


def sheet(texts, stamps) -> list[SheetLine]:
    return [SheetLine(t, s) for t, s in zip(texts, stamps, strict=True)]


def ear(sung, total_ms: int, readable: bool = True) -> dict:
    """``sung``: (text, true start ms). ``readable=False`` keeps the letter
    curve but drops the letters (processed vocals the model cannot spell)."""
    frames = int(total_ms / STRIDE_MS)
    prob = [0.05] * frames
    tokens = []
    for text, t in sung:
        cursor = t + LAG_MS
        for word in text.split():
            for ch in letters(word):
                if readable:
                    tokens.append({"text": ch, "start_ms": cursor, "end_ms": cursor + 40})
                for f in range(int(cursor / STRIDE_MS), int((cursor + MS_PER_LETTER) / STRIDE_MS)):
                    if 0 <= f < frames:
                        prob[f] = 0.9
                cursor += MS_PER_LETTER
            if readable:
                tokens.append({"text": "|", "start_ms": cursor, "end_ms": cursor + 20})
            cursor += WORD_GAP_MS
    tokens.sort(key=lambda tok: tok["start_ms"])
    return {"tokens": tokens, "letter_prob": prob, "stride_ms": STRIDE_MS, "start_ms": 0,
            "frames": frames}


def song(seed: int = 1, n: int = 16):
    """(texts, true starts, ear, total ms) of a healthy synthetic song."""
    texts = lines(seed, n)
    true = starts(n)
    total = true[-1] + 15_000
    return texts, true, ear(list(zip(texts, true, strict=True)), total), total
