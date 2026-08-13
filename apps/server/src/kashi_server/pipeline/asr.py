"""Pick a lyric record by what the audio actually SINGS.

The gap this fills (measured against lrclib, 2026-08-13): for covers, remixes,
nightcore and fan edits, the text almost always exists — as the ORIGINAL song's
record — but nothing in the metadata points at it. The upload is credited to the
cover artist ("Eiden XII - Heathens (But It hits different)"), so an artist-
scoped search finds nothing, and a title-only search returns a pile of records
whose top hit is routinely the wrong song (Carousel -> Michael Jackson, Mad Love
-> Linda Ronstadt). The problem is not availability. It is DISAMBIGUATION.

The words being sung settle it, and they cost almost nothing to obtain: the
alignment model IS a CTC acoustic model, so a greedy decode over the emissions
it already knows how to produce yields a rough transcript. No new dependency, no
new licence question (jonatasgrosman XLS-R is Apache-2.0).

Two deliberate limits:

  * ROUGH IS THE POINT. A greedy CTC decode with no language model, over a full
    MIX rather than separated vocals, produces something closer to phonetics
    than to text ("olmy frendsar hethens"). It is never shown to anyone and
    never becomes lyrics — it only has to rank candidates, and character n-grams
    survive that kind of damage where word matching would not.
  * MIX, NOT VOCALS, because lyrics deliberately resolve BEFORE separation
    (worker/process.py: a doomed lyrics_not_found must not pay the double-digit-
    minute separation bill first). One CTC pass over a slice costs seconds;
    separating to improve it would cost more than the rung is worth.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence

#: Characters that carry identity. Everything else (punctuation, digits,
#: bracketed credits) differs between a transcript and a written lyric sheet for
#: reasons that have nothing to do with whether they are the same song.
_KEEP = re.compile(r"[^a-z\s]")

#: Accept a candidate only above this. MEASURED, not chosen (2026-08-13, worker
#: pod, jg-1b on the full mix, one 45 s slice):
#:
#:   correct sheets   Shape of You 0.545 · Stressed Out 0.738 · Uptown Funk 0.351
#:                    Heathens (COVER -> original) 0.296 · I'm Good (COVER) 0.436
#:   wrong sheets     36 decoy records from common-word titles, tail tops out
#:                    at 0.238 (and the same decoy wins every time, so it is a
#:                    property of that sheet rather than of a song)
#:
#: 0.26 sits in the 0.238–0.296 gap. The gap is NARROW and rests on five songs,
#: so this is a starting value to be moved by field data — every decision logs
#: its score for that reason. Two things make a permissive value affordable:
#: the rung runs ONLY after every metadata rung failed (a false accept replaces
#: NO lyrics, never good ones), and a wrong song cannot survive downstream —
#: CTC alignment against the wrong words scores near zero and the quality gate
#: catches it.
#:
#: A cover scores LOWER than a canonical upload (0.296 vs 0.545): different
#: singer, different arrangement, rougher decode. Since covers are exactly what
#: this rung exists for, a threshold tuned on canonical tracks alone would have
#: rejected its own use case — 0.30 looked reasonable until Heathens was
#: measured at 0.296.
DEFAULT_MATCH_THRESHOLD = 0.26

#: One 45 s slice beats three 30 s slices spread across the song (narrowest
#: margin +0.134 vs +0.088, same five songs). Spreading reaches instrumental
#: stretches, and what a CTC decode produces there is noise that dilutes the
#: evidence rather than adding to it.
SLICE_SECONDS = 45.0

#: Long enough that a chorus repeat cannot carry the whole score on its own,
#: short enough to survive the substitutions a language-model-free decode makes.
#: 4 is the standard choice for noisy-text matching and it is not tuned to this
#: dataset — deliberately, since there is no labelled set to tune it on.
NGRAM = 4


#: Letters NFKD refuses to decompose, because they are letters in their own
#: right rather than an accented base — Turkish dotless ı chief among them, and
#: Turkish is the second priority language. Without this it fell through the
#: ASCII filter and every "ı" became a SPACE, so "sarkı" and "sarki" scored as
#: different songs. Found by test, not by reading.
_UNDECOMPOSABLE = str.maketrans(
    {"ı": "i", "İ": "i", "ø": "o", "Ø": "o", "đ": "d", "Đ": "d", "ł": "l", "Ł": "l", "ß": "s"}
)


def normalize_for_match(text: str) -> str:
    """Fold to the letters both sides can agree on.

    Accents go through NFKD so a transcript that never emits "ö" still matches a
    lyric sheet that does — the ASR vocabulary is ASCII-ish, the sheet is not,
    and that mismatch is not evidence about the song.
    """
    folded = unicodedata.normalize("NFKD", text.translate(_UNDECOMPOSABLE).lower())
    stripped = "".join(c for c in folded if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", _KEEP.sub(" ", stripped)).strip()


def char_ngrams(text: str, n: int = NGRAM) -> set[str]:
    """Character n-grams over the SPACE-JOINED text (spaces included).

    Keeping spaces means word boundaries count as evidence, which is most of
    what separates "hold on" from "old one" — and word boundaries are one of
    the few things a CTC decode gets right even when the letters are wrong.
    """
    normalized = normalize_for_match(text)
    if len(normalized) < n:
        return set()
    return {normalized[i : i + n] for i in range(len(normalized) - n + 1)}


def similarity(transcript: str, lyrics: str) -> float:
    """How much of the TRANSCRIPT the lyric sheet accounts for, in [0, 1].

    Containment, not Jaccard, and the asymmetry is the whole design: the
    transcript covers one slice of the song while the sheet covers all of it, so
    a symmetric measure would punish the correct record for being complete. The
    question is "does this sheet explain what I heard", not "are these the same
    length".
    """
    heard = char_ngrams(transcript)
    if not heard:
        return 0.0
    written = char_ngrams(lyrics)
    if not written:
        return 0.0
    return len(heard & written) / len(heard)


def usable_columns(emission_width: int, vocab_size: int) -> int:
    """How many emission columns are real tokens.

    ctc_forced_aligner APPENDS a <star> column to the emissions it returns, and
    that column wins every single frame: argmax over the full width returned the
    star id 2250 times out of 2250 on the first probe run (2026-08-13) and the
    transcript came back empty. The vocabulary size is the truth about how wide
    the real distribution is; anything past it belongs to the aligner.
    """
    return min(emission_width, vocab_size)


def greedy_ctc_decode(
    ids: Sequence[int],
    id_to_token: Mapping[int, str],
    blank_id: int = 0,
) -> str:
    """Standard CTC collapse: drop repeats, then drop blanks.

    Order matters and is not interchangeable. Repeats collapse FIRST, so a
    genuine double letter survives only when a blank was emitted between the two
    frames — which is exactly what the blank symbol is for. Dropping blanks first
    would silently turn "hello" into "helo".
    """
    out: list[str] = []
    previous: int | None = None
    for token_id in ids:
        if token_id != previous:
            if token_id != blank_id:
                out.append(id_to_token.get(token_id, ""))
            previous = token_id
    return "".join(out).replace("|", " ").strip()


def slice_window(duration_s: float, want_s: float = 45.0) -> tuple[float, float]:
    """Which seconds to transcribe: (start, length).

    Starts a quarter of the way in. Songs open with instrumental intros and
    close with outros and fades; a quarter in is reliably inside the first verse
    or chorus for anything with a conventional structure, and it needs no
    analysis of the audio to find. A short track just gets the middle of what it
    has.
    """
    if duration_s <= want_s:
        return 0.0, max(duration_s, 0.0)
    start = duration_s * 0.25
    if start + want_s > duration_s:
        start = duration_s - want_s
    return start, want_s


def pick_by_transcript(
    transcript: str,
    candidates: Iterable[tuple[object, str]],
    *,
    threshold: float,
    margin: float = 0.0,
) -> object | None:
    """The candidate the audio agrees with, or None.

    Two independent bars, because they answer different failure modes:

      * `threshold` rejects the case where NONE of the candidates is the song —
        the common one, since a title-only search happily returns twenty records
        for a title that merely resembles this one;
      * `margin` rejects the case where two candidates score alike, which is what
        a genuine duplicate (album vs single, clean vs explicit) looks like. Both
        are usually right, so picking either is fine — but so is a near-tie
        between the right song and a wrong one that shares a chorus, and from the
        scores alone those are indistinguishable. Refusing costs a lyric sheet;
        accepting costs a wrong one on screen.
    """
    scored = sorted(
        ((similarity(transcript, text), item) for item, text in candidates),
        key=lambda pair: pair[0],
        reverse=True,
    )
    if not scored:
        return None
    best_score, best = scored[0]
    if best_score < threshold:
        return None
    if len(scored) > 1 and best_score - scored[1][0] < margin:
        return None
    return best
