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


# --- Faz 9 P1: the measured lateness correction ---------------------------


def test_the_offset_defaults_to_no_correction(configure):
    """Shipping a shift by default would move every timing in the archive on
    an upgrade. It is measured per language and set on purpose."""
    assert Settings.model_fields["align_offset_ms"].default == 0
    configure()
    assert resolve_aligner("eng").offset_ms == 0


def test_the_offset_travels_with_its_language(configure):
    configure(
        {"eng": {"checkpoint": EN_MODEL, "offset_ms": -80}, "tur": TR_MODEL},
        romanize=True,
    )
    assert resolve_aligner("eng").offset_ms == -80
    # Turkish has no measurement of its own yet — the eval set is only valid at
    # 300 ms, which cannot see an 80 ms bias. It must NOT inherit English's.
    assert resolve_aligner("tur").offset_ms == 0
    assert resolve_aligner("jpn").offset_ms == 0


def test_an_explicit_offset_argument_wins(configure):
    """How the correction is fitted in the first place: measure with it off."""
    configure({"eng": {"checkpoint": EN_MODEL, "offset_ms": -80}})
    assert resolve_aligner("eng", offset_ms=0).offset_ms == 0
    assert resolve_aligner("eng", offset_ms=-120).offset_ms == -120


def test_a_global_offset_covers_unlisted_languages(monkeypatch, configure):
    configure(MEASURED)
    monkeypatch.setattr(settings, "align_offset_ms", -50)
    assert resolve_aligner("jpn").offset_ms == -50
    assert resolve_aligner("eng").offset_ms == -50  # entry says nothing -> global


def test_shift_moves_whole_spans_and_keeps_durations():
    from kashi_server.pipeline.alignment import AlignedWord, AlignResult, LineTiming, shift_result

    result = AlignResult(
        sync="word",
        lines=[LineTiming(start_ms=1000, end_ms=1800, text="gel", score=0.9)],
        words_per_line=[[AlignedWord(start_ms=1000, end_ms=1800, text="gel", prob=0.9)]],
        quality_score=0.9,
    )
    shifted = shift_result(result, -80)
    assert (shifted.lines[0].start_ms, shifted.lines[0].end_ms) == (920, 1720)
    word = shifted.words_per_line[0][0]
    assert (word.start_ms, word.end_ms) == (920, 1720)
    # Duration is preserved: the model did not mishear the word's LENGTH.
    assert word.end_ms - word.start_ms == 800
    assert word.text == "gel" and word.prob == 0.9


def test_shift_of_zero_changes_nothing():
    from kashi_server.pipeline.alignment import AlignResult, LineTiming, shift_result

    result = AlignResult(
        sync="line",
        lines=[LineTiming(start_ms=10, end_ms=20, text="a", score=0.5)],
        words_per_line=[],
        quality_score=0.5,
    )
    assert shift_result(result, 0) is result


def test_a_span_shifted_past_zero_is_clamped_not_negative():
    """The first word of a song that starts at 40 ms cannot move to -40."""
    from kashi_server.pipeline.alignment import AlignedWord, AlignResult, LineTiming, shift_result

    result = AlignResult(
        sync="word",
        lines=[LineTiming(start_ms=40, end_ms=60, text="a", score=0.5)],
        words_per_line=[[AlignedWord(start_ms=40, end_ms=60, text="a", prob=0.5)]],
        quality_score=0.5,
    )
    shifted = shift_result(result, -80)
    assert (shifted.lines[0].start_ms, shifted.lines[0].end_ms) == (0, 0)
    assert shifted.words_per_line[0][0].start_ms == 0


def test_align_applies_the_offset_it_resolved(monkeypatch, configure, tmp_path):
    """The resolver can be right while align() forgets to apply it."""
    import sys
    import types

    from kashi_server.pipeline import alignment

    configure({"tur": {"checkpoint": TR_MODEL, "romanize": False, "offset_ms": -80}})
    monkeypatch.setitem(
        sys.modules,
        "ctc_forced_aligner",
        types.SimpleNamespace(load_audio=lambda *a, **k: object()),
    )
    fake_model = types.SimpleNamespace(dtype=None, device="cpu")
    monkeypatch.setattr(alignment, "_load_model", lambda name: (fake_model, object()))
    monkeypatch.setattr(
        alignment,
        "_align_texts",
        lambda *a, **k: [{"text": "gel", "start": 1.0, "end": 1.8, "score": -0.1}],
    )

    result = alignment.align(tmp_path / "x.wav", ["gel"], "tur")

    assert result.words_per_line[0][0].start_ms == 920
    assert result.lines[0].start_ms == 920


# --- Faz 9 P2: per-sound refinement of the correction ----------------------

EN_BY_INITIAL = {"vowel": -110, "fricative": -100, "plosive": -80, "sonorant": -60}


def test_the_class_table_is_per_language_only(configure, monkeypatch):
    """Which sound a letter makes is a fact about a LANGUAGE. A global table
    would apply English phonetics to whatever else turned up."""
    configure({"eng": {"checkpoint": EN_MODEL, "offset_ms": -80,
                       "offset_by_initial": EN_BY_INITIAL}})
    assert resolve_aligner("eng").offset_by_initial == EN_BY_INITIAL
    assert resolve_aligner("tur").offset_by_initial is None
    monkeypatch.setattr(settings, "align_offset_ms", -80)
    assert resolve_aligner("jpn").offset_by_initial is None  # no global path


def test_words_get_their_own_offset_by_first_sound():
    from kashi_server.pipeline.alignment import AlignedWord, AlignResult, LineTiming, shift_result

    words = [
        AlignedWord(start_ms=1000, end_ms=1200, text="apple", prob=0.9),   # vowel  -110
        AlignedWord(start_ms=2000, end_ms=2200, text="baby", prob=0.9),    # plosive -80
        AlignedWord(start_ms=3000, end_ms=3200, text="♪", prob=0.9),       # no class -> base
    ]
    result = AlignResult(
        sync="word",
        lines=[LineTiming(start_ms=1000, end_ms=3200, text="apple baby ♪", score=0.9)],
        words_per_line=[words],
        quality_score=0.9,
    )
    shifted = shift_result(result, -50, EN_BY_INITIAL)
    assert [w.start_ms for w in shifted.words_per_line[0]] == [890, 1920, 2950]
    # The line follows its words rather than the base offset, so it can never
    # claim to start before the first word inside it.
    assert shifted.lines[0].start_ms == 890


def test_per_class_offsets_cannot_invert_word_order():
    """Two words 10 ms apart, pulled 30 ms apart by their classes, would swap:
    baby lands at 1000-80 = 920 while apple lands at 1010-110 = 900, i.e. the
    second word would start BEFORE the first. The renderer's active-word
    search assumes starts never go backwards."""
    from kashi_server.pipeline.alignment import AlignedWord, AlignResult, LineTiming, shift_result

    words = [
        AlignedWord(start_ms=1000, end_ms=1005, text="baby", prob=0.9),   # plosive -80
        AlignedWord(start_ms=1010, end_ms=1200, text="apple", prob=0.9),  # vowel  -110
    ]
    result = AlignResult(
        sync="word",
        lines=[LineTiming(start_ms=1000, end_ms=1200, text="baby apple", score=0.9)],
        words_per_line=[words],
        quality_score=0.9,
    )
    starts = [w.start_ms for w in shift_result(result, -80, EN_BY_INITIAL).words_per_line[0]]
    assert starts == sorted(starts)
    assert starts == [920, 920]  # the vowel word is held at the plosive's start


def test_a_word_never_runs_past_the_next_words_start():
    """The second word can be pulled left harder than the first, so the first
    one's end has to give way — shortening a span is safe, moving a start is
    not."""
    from kashi_server.pipeline.alignment import AlignedWord, AlignResult, LineTiming, shift_result

    words = [
        AlignedWord(start_ms=1000, end_ms=1500, text="baby", prob=0.9),   # plosive -80
        AlignedWord(start_ms=1500, end_ms=1900, text="apple", prob=0.9),  # vowel  -110
    ]
    result = AlignResult(
        sync="word",
        lines=[LineTiming(start_ms=1000, end_ms=1900, text="baby apple", score=0.9)],
        words_per_line=[words],
        quality_score=0.9,
    )
    shifted = shift_result(result, -80, EN_BY_INITIAL).words_per_line[0]
    assert shifted[0].end_ms <= shifted[1].start_ms == 1390


def test_align_applies_the_class_table(monkeypatch, configure, tmp_path):
    import sys
    import types

    from kashi_server.pipeline import alignment

    configure({"eng": {"checkpoint": EN_MODEL, "offset_ms": -80,
                       "offset_by_initial": EN_BY_INITIAL}})
    monkeypatch.setitem(
        sys.modules,
        "ctc_forced_aligner",
        types.SimpleNamespace(load_audio=lambda *a, **k: object()),
    )
    fake_model = types.SimpleNamespace(dtype=None, device="cpu")
    monkeypatch.setattr(alignment, "_load_model", lambda name: (fake_model, object()))
    monkeypatch.setattr(
        alignment,
        "_align_texts",
        lambda *a, **k: [{"text": "apple", "start": 1.0, "end": 1.2, "score": -0.1}],
    )

    result = alignment.align(tmp_path / "x.wav", ["apple"], "eng")

    assert result.words_per_line[0][0].start_ms == 890  # vowel class, not -80


# --- 2026-08-12 audit hardening -------------------------------------------


def test_a_typoed_field_is_a_startup_error_not_a_silent_drop():
    """Pydantic's default silently DROPS unknown fields. "offsetms" would
    disable a measured correction; "romanize_" on the Turkish entry would hand
    mpoyraz's model uroman text — the exact failure AlignerChoice exists to
    prevent. A typo must crash at startup."""
    with pytest.raises(ValidationError):
        _table({"eng": {"checkpoint": EN_MODEL, "offsetms": -80}})
    with pytest.raises(ValidationError):
        _table({"tur": {"checkpoint": TR_MODEL, "romanize_": False}})


def test_a_typoed_class_key_is_rejected_not_silently_ignored():
    """"vowels" (plural) would match nothing and quietly revert the LATEST
    class (+112 ms) to the base offset — invisible outside a benchmark."""
    with pytest.raises(ValidationError):
        _table({"eng": {"checkpoint": EN_MODEL, "offset_by_initial": {"vowels": -110}}})


def test_fat_fingered_offsets_are_rejected():
    """Every measured bias sits in the 59-112 ms band; -800 for -80 would
    shift every word nearly a second of silent garbage."""
    with pytest.raises(ValidationError):
        _table({"eng": {"checkpoint": EN_MODEL, "offset_ms": -800}})
    with pytest.raises(ValidationError):
        _table({"eng": {"checkpoint": EN_MODEL, "offset_by_initial": {"vowel": -1100}}})
    with pytest.raises(ValidationError):
        Settings(align_offset_ms=-800)


def test_an_unmapped_detected_language_misses_the_routing_table(configure):
    """detect_language passes unmapped codes through raw (langid.py), so a
    Chinese song must NOT take the English checkpoint and English lateness
    corrections — that would be a regression from the MMS fallback."""
    from kashi_server.pipeline.langid import _ISO_639_1_TO_3

    assert _ISO_639_1_TO_3.get("zh") is None  # precondition: zh is unmapped
    configure(
        {"eng": {"checkpoint": EN_MODEL, "offset_ms": -80, "offset_by_initial": EN_BY_INITIAL}},
        model="multilingual/fallback",
    )
    spec = resolve_aligner("zh")
    assert spec.model_name == "multilingual/fallback"
    assert spec.offset_ms == 0
    assert spec.offset_by_initial is None
    # And the detector really does pass such codes through when it detects them.
    import kashi_server.pipeline.langid as langid_module

    class _FakeResult(list):
        pass

    def fake_detect(text, model, k):
        return [{"lang": "zh"}]

    import types

    fake_mod = types.SimpleNamespace(detect=fake_detect)
    import sys

    monkey = pytest.MonkeyPatch()
    monkey.setitem(sys.modules, "fast_langdetect", fake_mod)
    try:
        assert langid_module.detect_language("你好世界") == "zh"
    finally:
        monkey.undo()


def test_arbiter_judges_on_the_acoustic_clock():
    """The lateness shift moves words 60-110 ms away from the vocal onsets
    they were measured against; onset support was calibrated on RAW starts.
    A word at raw 1000 ms (onset 1150 within tolerance) shifted to 890 must
    still count as supported — its evidence lives on the acoustic clock."""
    from kashi_server.pipeline.alignment import AlignedWord
    from kashi_server.pipeline.arbiter import onset_support

    shifted = [
        AlignedWord(start_ms=890, end_ms=1100, text="apple", prob=0.9, shift_ms=-110),
        AlignedWord(start_ms=1920, end_ms=2100, text="baby", prob=0.9, shift_ms=-80),
        AlignedWord(start_ms=2950, end_ms=3100, text="moon", prob=0.9, shift_ms=-60),
    ]
    onsets = [1150, 2150, 3160]  # each within 200 ms of the RAW start only
    assert onset_support(shifted, onsets) == 1.0
    # And the raw clock is genuinely what saves it: judged on the shifted
    # starts these words would all be unsupported.
    unshifted_view = [
        AlignedWord(start_ms=w.start_ms, end_ms=w.end_ms, text=w.text, prob=w.prob)
        for w in shifted
    ]
    assert onset_support(unshifted_view, onsets) == 0.0


def test_shift_result_records_the_actual_displacement():
    """shift_ms must be what HAPPENED, clamps included — raw recovery
    (start_ms - shift_ms) has to be exact even for the zero-clamped word."""
    from kashi_server.pipeline.alignment import AlignedWord, AlignResult, LineTiming, shift_result

    words = [AlignedWord(start_ms=40, end_ms=200, text="on", prob=0.9)]
    result = AlignResult(
        sync="word",
        lines=[LineTiming(start_ms=40, end_ms=200, text="on", score=0.9)],
        words_per_line=[words],
        quality_score=0.9,
    )
    shifted = shift_result(result, -80).words_per_line[0][0]
    assert shifted.start_ms == 0
    assert shifted.shift_ms == -40  # clamped: only 40 of the 80 happened
    assert shifted.start_ms - shifted.shift_ms == 40  # raw recovered exactly
