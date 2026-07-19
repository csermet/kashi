"""lexicon_lint rules (Faz 6.5 P4) + the shipped lexicon must pass its own gate."""

import yaml

from kashi_server.pipeline.lexicon_lint import lint_lexicon
from kashi_server.pipeline.semantics import LEXICON_PATH


def _cat(**overrides):
    base = {
        "id": "fire",
        "icon": "local_fire_department",
        "base_intensity": 0.8,
        "keywords_en": ["fire"],
        "stems_tr": ["yang"],
        "prototypes_en": ["everything burns in a great fire"],
        "prototypes_tr": ["her şey büyük bir ateşte yanıyor"],
    }
    base.update(overrides)
    return base


def _doc(*cats):
    return {"version": "kashi-fx/1.2.0", "categories": list(cats)}


def test_valid_document_is_clean():
    report = lint_lexicon(_doc(_cat()))
    assert report.ok
    assert report.warnings == []


def test_shipped_lexicon_passes_its_own_gate():
    raw = yaml.safe_load(LEXICON_PATH.read_text(encoding="utf-8"))
    report = lint_lexicon(raw)
    assert report.errors == []


def test_short_turkish_stem_is_an_error():
    report = lint_lexicon(_doc(_cat(stems_tr=["yan"])))
    assert any("shorter than" in e for e in report.errors)


def test_non_normalized_entry_is_an_error():
    # The İ/I trap: uppercase Turkish İ must be written pre-normalized.
    report = lint_lexicon(_doc(_cat(keywords_tr=["İhanet"])))
    assert any("not normalized" in e for e in report.errors)


def test_cross_category_keyword_collision_is_an_error():
    report = lint_lexicon(
        _doc(_cat(), _cat(id="shine", keywords_en=["fire"], stems_tr=["parl"]))
    )
    assert any("claimed by both" in e for e in report.errors)


def test_cross_category_stem_prefix_shadowing_is_a_warning():
    report = lint_lexicon(
        _doc(
            _cat(keywords_en=["fireball"]),
            _cat(id="shine", keywords_en=["fireband"], stems_tr=["parl"]),
        )
    )
    # 'fireband' (shine) starts with no foreign stem; give fire a stem that
    # shadows shine's keyword instead.
    report = lint_lexicon(
        _doc(
            _cat(stems_en=["fireb"]),
            _cat(id="shine", keywords_en=["fireband"], stems_tr=["parl"]),
        )
    )
    assert report.ok
    assert any("also matches stem" in w for w in report.warnings)


def test_bad_id_and_unknown_field_are_errors():
    report = lint_lexicon(_doc(_cat(id="Fire!", bogus_field=[1])))
    assert any("id must match" in e for e in report.errors)
    assert any("unknown fields" in e for e in report.errors)


def test_duplicate_ids_and_missing_prototypes_are_errors():
    report = lint_lexicon(_doc(_cat(), _cat(prototypes_tr=[])))
    assert any("duplicate category id" in e for e in report.errors)
    assert any("prototypes_tr" in e for e in report.errors)


def test_bare_word_prototype_is_a_warning():
    report = lint_lexicon(_doc(_cat(prototypes_en=["fire burns"])))
    assert report.ok
    assert any("bare word" in w for w in report.warnings)
