"""Ground-truth acquisition and loading.

JamendoLyrics MultiLang (Durand/Stoller/Ewert, ICASSP 2023): 79 CC-licensed
songs (en 20 / fr 19 / de 20 / es 20) with human word-level annotations. The
audio's CC licenses are mostly NC/ND — the download lands in benchmarks/data/
(gitignored) and MUST NOT be vendored into the repo or an image.

Line texts are rebuilt from the word list + the word CSV's line_end markers,
NOT read from annotations/lines/*.csv: that guarantees the aligner input
tokenizes to exactly the annotated word sequence, so hypothesis and reference
words pair positionally (benchmarks.metrics.word_start_deviations).
"""

import csv
import io
import logging
import shutil
import tarfile
from dataclasses import dataclass
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

# Pinned for reproducible downloads. The GitHub repo is archived (canonical home
# is now HF jamendolyrics/jamendolyrics) but preserved, byte-identical in layout,
# and a plain tarball needs no hub client (~390 MB).
JAMENDO_COMMIT = "f093b7a74e034deac21eb5ea3fc40769b29c35b0"
JAMENDO_TARBALL = f"https://github.com/f90/jamendolyrics/archive/{JAMENDO_COMMIT}.tar.gz"

# JamendoLyrics.csv language names -> ISO-639-3 (what align() expects).
# Turkish is NOT in JamendoLyrics; it is here for our own hand-built TR eval
# set, which is written in this exact schema so it loads through the same
# reader (see benchmarks/README.md "Turkish eval set"). No word-level Turkish
# ground truth exists publicly — checked 2026-08-09 — so the set is ours and
# lives outside the repo with the rest of the copyrighted eval audio.
LANGUAGES = {
    "English": "eng",
    "French": "fra",
    "German": "deu",
    "Spanish": "spa",
    "Turkish": "tur",
}


@dataclass(frozen=True)
class JamendoSong:
    stem: str  # mp3 filename minus .mp3; keys everything
    artist: str
    title: str
    language: str  # ISO-639-3
    license_type: str
    audio_path: Path
    line_texts: list[str]
    line_starts_ms: list[int]
    words: list[tuple[int, str]]  # (start_ms, token), annotation order
    word_ends_ms: list[int]  # parallel to words — END ground truth (Faz 5 P1)
    duration_hint_s: float  # last word end — enough for wall-clock ratios
    # Which words a human actually checked (optional `verified` column). None
    # means "all of them", which is what JamendoLyrics is. Our hand-built sets
    # annotate a SAMPLE per song: measured on the first song, 96% of the
    # correction effort moved words by less than the 300 ms PCO tolerance —
    # invisible to the metric — so paying for every word buys nothing. The
    # unchecked rows keep their pre-mark and are excluded from scoring.
    verified: list[bool] | None = None


def ensure_jamendo(data_dir: Path, name: str = "jamendolyrics") -> Path:
    """Download + extract once; subsequent runs reuse the checkout.

    `name` selects which root under data/ to use. Anything other than the
    default is a LOCAL set in the same schema (our hand-built Turkish one) —
    there is nothing to download, so a missing root is an error rather than a
    390 MB surprise.
    """
    root = data_dir / name
    if (root / "JamendoLyrics.csv").exists():
        return root
    if name != "jamendolyrics":
        raise SystemExit(
            f"{root} has no JamendoLyrics.csv — a local set is assembled, not "
            "downloaded (see /home/cnr-intel/kashi-eval/make_jamendo_root.py)"
        )
    data_dir.mkdir(parents=True, exist_ok=True)
    logger.info("downloading JamendoLyrics (~390 MB) from %s", JAMENDO_TARBALL)
    with httpx.Client(follow_redirects=True, timeout=None) as http:
        response = http.get(JAMENDO_TARBALL)
        response.raise_for_status()
    with tarfile.open(fileobj=io.BytesIO(response.content), mode="r:gz") as tar:
        tar.extractall(data_dir, filter="data")
    extracted = data_dir / f"jamendolyrics-{JAMENDO_COMMIT}"
    if not extracted.exists():  # pragma: no cover - tarball layout changed
        raise RuntimeError(f"tarball did not contain {extracted.name}")
    shutil.move(str(extracted), str(root))
    return root


def _read_song(root: Path, row: dict) -> JamendoSong:
    stem = row["Filepath"].removesuffix(".mp3")
    tokens = (root / "lyrics" / f"{stem}.words.txt").read_text(encoding="utf-8").split()
    with (root / "annotations" / "words" / f"{stem}.csv").open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if len(rows) != len(tokens):
        raise ValueError(f"{stem}: {len(rows)} word rows vs {len(tokens)} tokens")

    words = [
        (round(float(r["word_start"]) * 1000), token)
        for r, token in zip(rows, tokens, strict=True)
    ]
    word_ends_ms = [round(float(r["word_end"]) * 1000) for r in rows]
    line_texts: list[str] = []
    line_starts_ms: list[int] = []
    current: list[str] = []
    for (start_ms, token), r in zip(words, rows, strict=True):
        if not current:
            line_starts_ms.append(start_ms)
        current.append(token)
        # line_end is nan for line-internal words, a timestamp for line-final ones.
        if r["line_end"] != "nan":
            line_texts.append(" ".join(current))
            current = []
    if current:  # final line lacking a line_end marker — keep it anyway
        line_texts.append(" ".join(current))

    verified = None
    if rows and "verified" in rows[0]:
        verified = [str(r.get("verified", "1")).strip() in ("1", "true", "True") for r in rows]

    return JamendoSong(
        stem=stem,
        artist=row["Artist"],
        title=row["Title"],
        language=LANGUAGES[row["Language"]],
        license_type=row["LicenseType"].strip(),
        audio_path=root / "mp3" / row["Filepath"],
        line_texts=line_texts,
        line_starts_ms=line_starts_ms,
        words=words,
        word_ends_ms=word_ends_ms,
        duration_hint_s=float(rows[-1]["word_end"]),
        verified=verified,
    )


def load_jamendo(
    root: Path,
    *,
    languages: set[str] | None = None,
    stems: set[str] | None = None,
    limit: int | None = None,
) -> list[JamendoSong]:
    with (root / "JamendoLyrics.csv").open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    songs: list[JamendoSong] = []
    for row in rows:
        song_language = LANGUAGES[row["Language"]]
        stem = row["Filepath"].removesuffix(".mp3")
        if languages and song_language not in languages:
            continue
        if stems and stem not in stems:
            continue
        songs.append(_read_song(root, row))
        if limit and len(songs) >= limit:
            break
    return songs


@dataclass(frozen=True)
class KashiCase:
    """A field case: real YouTube audio, lrclib synced starts as the line-level
    reference (no word ground truth — median-corrected line report only)."""

    id: str
    title: str
    artist: str
    youtube_id: str
    lrclib_id: int
    language: str
    window_s: tuple[float, float] | None


def load_cases(path: Path) -> list[KashiCase]:
    import yaml  # bench extra / dev group only

    cases = []
    for entry in yaml.safe_load(path.read_text(encoding="utf-8")) or []:
        window = entry.get("window_s")
        cases.append(
            KashiCase(
                id=entry["id"],
                title=entry["title"],
                artist=entry["artist"],
                youtube_id=entry["youtube_id"],
                lrclib_id=int(entry["lrclib_id"]),
                language=entry.get("language", "eng"),
                window_s=(float(window[0]), float(window[1])) if window else None,
            )
        )
    return cases
