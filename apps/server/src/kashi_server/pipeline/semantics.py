"""Word→effect-category tagging (Faz 6 P3) — the fx data foundation.

Two layers, KEYWORDS ON TOP (deterministic accuracy beats coverage — a
wrong effect on a wrong word is the worst outcome):

1. Curated keyword/stem layer (pipeline/data/fx_lexicon.yaml): exact
   full-word matches plus >=4-char prefix stems (Turkish agglutination:
   "patl" catches patlama/patlıyor/patlamasında). Bilingual, model-free,
   microseconds. Produces WORD-level tags.
2. Embedding layer (optional `semantics` extra, settings.fx_embeddings):
   lines with NO keyword hit are embedded ("query: " prefix, E5 contract)
   against per-category prototype centroids ("passage: " prefix). Below
   the conservative per-language threshold → NO tag (never force). A line
   hit yields a LINE-level theme tag only — word attribution stays the
   keyword layer's job (single-word embeddings are unreliable; research
   round R2/model raporu).

Turkish İ/I trap: ordinary str.lower() turns "İ" into "i̇" (2 codepoints)
and "I" into "i" (wrong — Turkish wants "ı"); we translate İ→i and I→ı
BEFORE lower() and never use casefold() (same recipe as titles._tokens).

Failure posture mirrors palette/beats: any error → no fx block, the
document still ships.
"""

import logging
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

LEXICON_PATH = Path(__file__).parent / "data" / "fx_lexicon.yaml"

# Embedding engine (the `semantics` extra) — pinned end to end.
MODEL_NAME = "intfloat/multilingual-e5-small"
MODEL_REVISION = "614241f622f53c4eeff9890bdc4f31cfecc418b3"

MIN_STEM_LEN = 4
MAX_WORD_TAGS = 60  # per document — the DG6 noise brake starts server-side
MAX_LINE_TAGS = 24
# Calibrated 2026-07-20 (Faz 6.5 P4, 200-line labeled archive sample —
# docs/research/embed-threshold-calibration-2026-07.md): E5 cosines are so
# compressed here that NO threshold separates right from wrong (precision
# plateaus ~24% strict / ~50% lenient even at 0.90+). The old values sat at
# the score MEDIAN and tagged half of all uncovered lines, mostly wrongly.
# 0.90 is the harm-reduction floor for anyone who enables the layer anyway;
# the real verdict is settings.fx_embeddings defaulting OFF (same commit).
EMBED_THRESHOLD = {"en": 0.90, "tr": 0.90, "default": 0.90}

_TR_TRANSLATE = str.maketrans({"İ": "i", "I": "ı"})
_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


@dataclass(frozen=True)
class FxCategory:
    id: str
    icon: str
    base_intensity: float
    keywords: frozenset[str]  # both languages, normalized
    stems: tuple[str, ...]  # both languages, normalized, len >= MIN_STEM_LEN
    prototypes: tuple[str, ...]  # both languages, raw text for embedding


@dataclass(frozen=True)
class Lexicon:
    version: str
    categories: tuple[FxCategory, ...]


@dataclass(frozen=True)
class WordTag:
    line: int
    word: int
    tag: str
    intensity: float


@dataclass(frozen=True)
class LineTag:
    line: int
    tag: str


@dataclass(frozen=True)
class FxTags:
    lexicon_version: str
    engine: str  # "keywords" or "keywords+<model>@<rev[:12]>"
    words: list[WordTag]
    lines: list[LineTag]


def normalize(text: str) -> str:
    """Turkish-safe lowercase + NFKC. Applied to lexicon AND candidates."""
    return unicodedata.normalize("NFKC", text.translate(_TR_TRANSLATE)).lower()


def _tokens(text: str) -> list[str]:
    return _WORD_RE.findall(normalize(text))


@lru_cache(maxsize=1)
def load_lexicon(path: Path = LEXICON_PATH) -> Lexicon:
    import yaml

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    categories = []
    for cat in raw["categories"]:
        keywords = frozenset(
            normalize(k)
            for k in (cat.get("keywords_en") or [])
            + (cat.get("keywords_tr") or [])
            # variants_tr (v1.2): irregular Turkish inflections a stem cannot
            # catch (consonant softening: melek→meleğim) — exact-match space.
            + (cat.get("variants_tr") or [])
        )
        stems = tuple(
            normalize(s) for s in (cat.get("stems_en") or []) + (cat.get("stems_tr") or [])
        )
        categories.append(
            FxCategory(
                id=cat["id"],
                icon=cat["icon"],
                base_intensity=float(cat["base_intensity"]),
                keywords=keywords,
                stems=stems,
                prototypes=tuple(
                    (cat.get("prototypes_en") or []) + (cat.get("prototypes_tr") or [])
                ),
            )
        )
    return Lexicon(version=str(raw["version"]), categories=tuple(categories))


def _keyword_category(token: str, lexicon: Lexicon) -> FxCategory | None:
    """Exact keyword first, then >=4-char stem prefix; ties break toward the
    higher base_intensity, then lexicon file order (both deterministic)."""
    best: FxCategory | None = None
    for cat in lexicon.categories:
        hit = token in cat.keywords or any(
            len(stem) >= MIN_STEM_LEN and token.startswith(stem) for stem in cat.stems
        )
        if hit and (best is None or cat.base_intensity > best.base_intensity):
            best = cat
    return best


def tag_words(
    line_word_texts: list[list[str]],
    line_texts: list[str],
    *,
    language: str | None = None,
    embedder: "PrototypeEmbedder | None" = None,
) -> FxTags:
    """Deterministic tagging over the FINAL document line/word structure
    (indices must reference what the client renders — run after line-QA)."""
    lexicon = load_lexicon()
    word_tags: list[WordTag] = []
    lines_with_hits: set[int] = set()

    for li, words in enumerate(line_word_texts):
        for wi, text in enumerate(words):
            token_list = _tokens(text)
            if not token_list:
                continue
            cat = _keyword_category(token_list[0], lexicon)
            if cat is not None:
                word_tags.append(WordTag(li, wi, cat.id, cat.base_intensity))
                lines_with_hits.add(li)

    if len(word_tags) > MAX_WORD_TAGS:
        # Deterministic brake: strongest first, then document order.
        word_tags.sort(key=lambda t: (-t.intensity, t.line, t.word))
        word_tags = sorted(word_tags[:MAX_WORD_TAGS], key=lambda t: (t.line, t.word))

    line_tags: list[LineTag] = []
    engine = "keywords"
    if embedder is not None:
        threshold = EMBED_THRESHOLD.get(language or "default", EMBED_THRESHOLD["default"])
        candidates = [
            (li, text)
            for li, text in enumerate(line_texts)
            if li not in lines_with_hits and _tokens(text)
        ]
        if candidates:
            hits = embedder.classify([text for _, text in candidates], threshold)
            line_tags = [
                LineTag(candidates[i][0], tag) for i, tag in enumerate(hits) if tag is not None
            ][:MAX_LINE_TAGS]
        engine = f"keywords+{MODEL_NAME.split('/')[-1]}@{MODEL_REVISION[:12]}"

    return FxTags(
        lexicon_version=lexicon.version, engine=engine, words=word_tags, lines=line_tags
    )


_EMBEDDER: "PrototypeEmbedder | None" = None


def get_embedder(cache_dir: str | None = None) -> "PrototypeEmbedder":
    """Worker-lifetime singleton (weights ~470MB — loaded once, like the
    alignment model). Warmup forces the first load."""
    global _EMBEDDER
    if _EMBEDDER is None:
        _EMBEDDER = PrototypeEmbedder(cache_dir=cache_dir)
    return _EMBEDDER


class PrototypeEmbedder:
    """E5 category-prototype classifier — loaded once per worker (warmup),
    prototypes embedded once at construction."""

    def __init__(self, cache_dir: str | None = None):
        # The `semantics` extra — absent in plain dev installs, present in the image.
        from sentence_transformers import (  # pyright: ignore[reportMissingImports]
            SentenceTransformer,
        )

        self._model = SentenceTransformer(
            MODEL_NAME, revision=MODEL_REVISION, cache_folder=cache_dir, device="cpu"
        )
        lexicon = load_lexicon()
        self._ids = [cat.id for cat in lexicon.categories]
        self._centroids = self._embed_prototypes(lexicon)

    def _embed_prototypes(self, lexicon: Lexicon):
        import numpy as np

        centroids = []
        for cat in lexicon.categories:
            vecs = self._model.encode(
                [f"passage: {p}" for p in cat.prototypes], normalize_embeddings=True
            )
            centroid = np.asarray(vecs).mean(axis=0)
            centroids.append(centroid / (np.linalg.norm(centroid) or 1.0))
        return np.vstack(centroids)

    def classify(self, lines: list[str], threshold: float) -> list[str | None]:
        import numpy as np

        vecs = self._model.encode(
            [f"query: {normalize(text)}" for text in lines], normalize_embeddings=True
        )
        sims = np.asarray(vecs) @ self._centroids.T
        out: list[str | None] = []
        for row in sims:
            best = int(np.argmax(row))
            out.append(self._ids[best] if float(row[best]) >= threshold else None)
        return out

    def smoke(self) -> float:
        """Warmup sanity: two fixed sentences must land in a sane cosine
        range (catches broken weights/tokenizer without asserting exact
        floats across library versions)."""
        import numpy as np

        vecs = self._model.encode(
            ["query: the bomb explodes tonight", "query: sessiz sakin bir sabah"],
            normalize_embeddings=True,
        )
        sim = float(np.asarray(vecs[0]) @ np.asarray(vecs[1]))
        if not (-0.2 <= sim <= 0.95):
            raise RuntimeError(f"semantics smoke failed: unexpected cosine {sim:.3f}")
        return sim
