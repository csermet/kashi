"""Faz 6 P3: word→effect-category tagging.

Keyword/stem layer tests run in the fast CI job (dependency-free). The
embedding layer needs the `semantics` extra → marked slow, skipped when
sentence-transformers is absent.
"""

import pytest

from kashi_server.pipeline.semantics import (
    MAX_WORD_TAGS,
    MIN_STEM_LEN,
    load_lexicon,
    normalize,
    tag_words,
)


def test_lexicon_shape_and_stem_discipline():
    lex = load_lexicon()
    assert lex.version == "kashi-fx/1.0.0"
    assert 15 <= len(lex.categories) <= 25
    ids = [cat.id for cat in lex.categories]
    assert len(ids) == len(set(ids))
    for cat in lex.categories:
        assert cat.icon
        assert 0 < cat.base_intensity <= 1
        assert len(cat.prototypes) >= 2  # both languages must contribute
        assert cat.keywords or cat.stems
        for stem in cat.stems:
            assert len(stem) >= MIN_STEM_LEN, f"{cat.id}: stem {stem!r} < {MIN_STEM_LEN} chars"


def test_turkish_casefold_trap():
    # İ→i, I→ı BEFORE lower(); never casefold (titles._tokens recipe).
    assert normalize("KISA") == "kısa"
    assert normalize("İstanbul") == "istanbul"
    assert normalize("DİZİ") == "dizi"
    assert len(normalize("İ")) == 1  # not "i" + combining dot


# (word, expected category) — the curated golden set. Negatives guard the
# known Turkish traps: yanında (beside ≠ burn), karar (≠ kar/snow),
# söyle (say ≠ sing), sevimli (cute ≠ love), paragraf (≠ para/money).
GOLDEN = [
    ("bomb", "explosion"),
    ("Explosion", "explosion"),
    ("exploded", "explosion"),
    ("patlama", "explosion"),
    ("PATLIYOR", "explosion"),
    ("patlamasında", "explosion"),
    ("fire", "fire"),
    ("burning", "fire"),
    ("alev", "fire"),
    ("yanıyor", "fire"),
    ("poison", "poison"),
    ("zehir", "poison"),
    ("zehri", "poison"),
    ("zehirli", "poison"),
    ("love", "love"),
    ("aşk", "love"),
    ("seviyorum", "love"),
    ("kalbim", "love"),
    ("heartbroken", "heartbreak"),
    ("ağlıyorum", "heartbreak"),
    ("rain", "water"),
    ("yağmur", "water"),
    ("drowning", "water"),
    ("stars", "night"),
    ("yıldız", "night"),
    ("gece", "night"),
    ("shine", "shine"),
    ("parlıyor", "shine"),
    ("diamond", "shine"),
    ("dance", "dance"),
    ("dans", "dance"),
    ("oynuyoruz", "dance"),
    ("money", "money"),
    ("para", "money"),
    ("flying", "fly"),
    ("uçuyorum", "fly"),
    ("faster", "speed"),
    ("koşuyorum", "speed"),
    ("lightning", "electric"),
    ("şimşek", "electric"),
    ("ice", "cold"),
    ("dondum", "cold"),
    ("üşüyorum", "cold"),
    ("shadows", "dark"),
    ("karanlık", "dark"),
    ("karanlığın", "dark"),
    ("die", "death"),
    ("ölüyorum", "death"),
    ("öldürdün", "death"),
    ("queen", "crown"),
    ("kraliçe", "crown"),
    ("phone", "phone"),
    ("telefon", "phone"),
    ("war", "fight"),
    ("savaş", "fight"),
    ("music", "music"),
    ("şarkı", "music"),
    # negatives — no tag, ever:
    ("yanında", None),
    ("karar", None),
    ("söyle", None),
    ("sevimli", None),
    ("paragraf", None),
    ("masa", None),
    ("table", None),
    ("the", None),
    ("ve", None),
]


def test_keyword_layer_golden_set():
    words = [w for w, _ in GOLDEN]
    tags = tag_words([words], [" ".join(words)])
    got = {t.word: t.tag for t in tags.words}
    for idx, (word, expected) in enumerate(GOLDEN):
        assert got.get(idx) == expected, f"{word!r}: got {got.get(idx)!r}, want {expected!r}"
    assert tags.engine == "keywords"  # no embedder passed
    assert tags.lines == []


def test_tagging_is_deterministic_and_capped():
    words = ["bomb"] * (MAX_WORD_TAGS + 40) + ["love"] * 10
    lines = [words[i : i + 10] for i in range(0, len(words), 10)]
    texts = [" ".join(chunk) for chunk in lines]
    first = tag_words(lines, texts)
    second = tag_words(lines, texts)
    assert first == second
    assert len(first.words) == MAX_WORD_TAGS
    # The brake keeps the STRONGEST tags (explosion 0.9 > love 0.6) and
    # re-sorts survivors into document order.
    assert all(t.tag == "explosion" for t in first.words)
    ordering = [(t.line, t.word) for t in first.words]
    assert ordering == sorted(ordering)


def test_intensity_tie_breaks_are_stable():
    # A word matching two categories takes the higher base_intensity.
    tags = tag_words([["ateş"]], ["ateş"])  # fire 0.8 — unambiguous sanity
    assert tags.words[0].tag == "fire"
    assert tags.words[0].intensity == 0.8


@pytest.mark.slow
def test_embedding_layer_ranks_and_separates():
    pytest.importorskip("sentence_transformers")
    from kashi_server.pipeline.semantics import PrototypeEmbedder

    embedder = PrototypeEmbedder()
    embedder.smoke()

    positive = "the dynamite goes off and everything blows up tonight"
    negative = "just a quiet ordinary tuesday morning walk"
    # Ranking check (threshold-independent): with the gate wide open, the
    # positive line's best category must be explosion, with a real gap
    # over the negative line's best score.
    open_gate = embedder.classify([positive, negative], threshold=-1.0)
    assert open_gate[0] == "explosion"

    import numpy as np

    vecs = embedder._model.encode(
        [f"query: {normalize(positive)}", f"query: {normalize(negative)}"],
        normalize_embeddings=True,
    )
    sims = np.asarray(vecs) @ embedder._centroids.T
    assert float(sims[0].max()) - float(sims[1].max()) > 0.02

    tags = tag_words(
        [[], []], [positive, negative], language="en", embedder=embedder
    )
    assert tags.engine.startswith("keywords+multilingual-e5-small@")
