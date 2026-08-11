"""Per-language aligner selection (Faz 8.1).

The licence-clean chain is not one checkpoint: English measured best on
jonatasgrosman's XLS-R 1B (Apache-2.0) and Turkish on mpoyraz's cv7 (CC-BY-4.0)
— and the Turkish one wants its text NOT romanized, because its vocabulary is
Turkish. So the routing has to carry two things per language, and the whole
point of these tests is that it can never carry one without the other.

Pure decision, no torch: `resolve_aligner` only reads config.
"""

import pytest
from pydantic import ValidationError

from kashi_server.config import Settings, settings
from kashi_server.pipeline.alignment import (
    MODEL_NAME,
    AlignerSpec,
    resolve_aligner,
    resolve_model_name,
)

EN_MODEL = "jonatasgrosman/wav2vec2-xls-r-1b-english"
TR_MODEL = "mpoyraz/wav2vec2-xls-r-300m-cv7-turkish"

MEASURED = {"eng": EN_MODEL, "tur": {"checkpoint": TR_MODEL, "romanize": False}}


def _table(raw: dict) -> dict:
    """The table as pydantic builds it — key normalization included."""
    return Settings(align_models=raw).align_models


@pytest.fixture
def configure(monkeypatch):
    """Point the live settings at a table without touching the environment."""

    def apply(table: dict | None = None, model: str = MODEL_NAME, romanize: bool = True):
        monkeypatch.setattr(settings, "align_models", _table(table or {}))
        monkeypatch.setattr(settings, "align_model", model)
        monkeypatch.setattr(settings, "align_romanize", romanize)

    return apply


def test_an_empty_table_is_todays_behaviour(configure):
    """The default has to be a no-op: every Faz 8 measurement was taken with
    the single-model path, and they stay valid only if it still answers."""
    assert Settings.model_fields["align_models"].default == {}
    configure()
    for language in ("eng", "tur", "jpn"):
        assert resolve_aligner(language) == resolve_aligner()
    assert resolve_aligner("tur").model_name == MODEL_NAME
    assert resolve_aligner("tur").romanize is True


def test_the_configured_language_gets_its_own_checkpoint(configure):
    configure(MEASURED)
    assert resolve_aligner("eng").model_name == EN_MODEL
    assert resolve_aligner("tur").model_name == TR_MODEL


def test_romanize_travels_with_the_checkpoint(configure):
    """The trap this design exists for. Turkish is routed to a model whose
    vocabulary is Turkish; if the checkpoint arrived while `romanize` kept the
    global answer, uroman would hand it "cgiosu" — the measurement that made
    this the shipped Turkish model would not reproduce."""
    configure(MEASURED, romanize=True)
    assert resolve_aligner("tur") == AlignerSpec(TR_MODEL, romanize=False)
    # ...and the inverse: a global flip must not detach the pair either.
    configure(MEASURED, romanize=False)
    assert resolve_aligner("tur") == AlignerSpec(TR_MODEL, romanize=False)


def test_a_bare_string_entry_inherits_the_global_romanize(configure):
    """Shorthand for "this checkpoint, nothing special about its text"."""
    configure(MEASURED, romanize=True)
    assert resolve_aligner("eng") == AlignerSpec(EN_MODEL, romanize=True)
    configure(MEASURED, romanize=False)
    assert resolve_aligner("eng") == AlignerSpec(EN_MODEL, romanize=False)


def test_an_unlisted_language_falls_back_to_the_global_model(configure):
    """Japanese has no permissive candidate yet; it must keep working, not
    borrow whatever checkpoint happens to be first in the table."""
    configure(MEASURED, model="some/global-default", romanize=True)
    assert resolve_aligner("jpn") == AlignerSpec("some/global-default", romanize=True)
    assert resolve_aligner("deu") == AlignerSpec("some/global-default", romanize=True)


def test_two_letter_keys_reach_the_aligners_three_letter_codes(configure):
    """`detect_language` yields ISO-639-3, so a config written "en"/"tr" would
    silently never match — the exact class of failure this project keeps
    paying for. Both spellings normalize to the same entry."""
    assert set(_table({"en": EN_MODEL, "tr": TR_MODEL})) == {"eng", "tur"}
    configure({"tr": {"checkpoint": TR_MODEL, "romanize": False}})
    assert resolve_aligner("tur") == AlignerSpec(TR_MODEL, romanize=False)
    assert resolve_aligner("TUR") == AlignerSpec(TR_MODEL, romanize=False)  # nor is case


def test_two_spellings_of_one_language_are_rejected():
    """No silent winner: a table saying both has no answer, and guessing one
    would route jobs to a checkpoint nobody chose."""
    with pytest.raises(ValidationError, match="two entries for 'tur'"):
        _table({"tr": EN_MODEL, "tur": TR_MODEL})


def test_an_empty_checkpoint_is_rejected():
    with pytest.raises(ValidationError):
        _table({"tur": ""})


def test_an_explicit_model_argument_bypasses_the_table(configure):
    """A bake-off names one checkpoint and means it. Letting the table supply
    `romanize` would pair a hand-picked model with another model's text form."""
    configure(MEASURED, romanize=True)
    assert resolve_aligner(
        "tur", "voidful/wav2vec2-xlsr-multilingual-56"
    ) == AlignerSpec("voidful/wav2vec2-xlsr-multilingual-56", romanize=True)


def test_an_explicit_romanize_argument_wins(configure):
    """...so one checkpoint can still be measured both ways."""
    configure(MEASURED)
    assert resolve_aligner("tur", romanize=True) == AlignerSpec(TR_MODEL, romanize=True)
    assert resolve_aligner("eng", romanize=False) == AlignerSpec(EN_MODEL, romanize=False)


def test_resolve_model_name_still_names_the_language_agnostic_default(configure):
    """The benchmark's report header calls it without a language."""
    configure(MEASURED, model="some/global-default")
    assert resolve_model_name() == "some/global-default"
    assert resolve_model_name("Qwen/Qwen3-ForcedAligner-0.6B") == "Qwen/Qwen3-ForcedAligner-0.6B"
    assert resolve_model_name("") == "some/global-default"  # empty is not a selection


def test_the_table_is_settable_as_one_env_var(monkeypatch):
    """The operator-facing surface: this is how the deployment sets it."""
    import json

    monkeypatch.setenv(
        "ALIGN_MODELS",
        json.dumps({"eng": EN_MODEL, "tr": {"checkpoint": TR_MODEL, "romanize": False}}),
    )
    table = Settings().align_models
    assert table["eng"].checkpoint == EN_MODEL
    assert table["eng"].romanize is None  # inherit
    assert (table["tur"].checkpoint, table["tur"].romanize) == (TR_MODEL, False)


def test_align_actually_routes_by_language(monkeypatch, configure, tmp_path):
    """The unit above can be right while `align()` ignores it. The real call
    needs torch, so the aligner internals are stubbed and only the routing is
    observed: which weights get loaded, and which text form is asked for."""
    import sys
    import types

    from kashi_server.pipeline import alignment

    configure(MEASURED, romanize=True)
    monkeypatch.setitem(
        sys.modules,
        "ctc_forced_aligner",
        types.SimpleNamespace(load_audio=lambda *a, **k: object()),
    )
    seen: dict = {}
    fake_model = types.SimpleNamespace(dtype=None, device="cpu")

    def fake_load_model(name):
        seen["model"] = name
        return fake_model, object()

    def fake_align_texts(*args, romanize=True, **kwargs):
        seen["romanize"] = romanize
        return [{"text": "gel", "start": 0.0, "end": 0.5, "score": -0.1}]

    monkeypatch.setattr(alignment, "_load_model", fake_load_model)
    monkeypatch.setattr(alignment, "_align_texts", fake_align_texts)

    result = alignment.align(tmp_path / "x.wav", ["gel"], "tur")

    assert seen == {"model": TR_MODEL, "romanize": False}
    assert result.model_name == TR_MODEL
