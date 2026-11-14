"""The pipeline contract: every default below is a MEASURED decision
(docs/research/hizalama-v2-benchmark-2026-07.md, lisans-temiz-zincir-2026-08.md,
the Faz 9 notes) — a drive-by change here must consciously re-litigate the
benchmark, so the values are pinned.

Asserted on the FIELD DEFAULTS (not the live `settings`) so ambient env vars
can neither fail nor mask these tests.

Since 2.33.0 the defaults ARE the production pipeline (review step B.1): the
values that lived only in gitops env and in audit/worker.env.example moved into
code. `test_the_worker_env_and_no_env_are_one_pipeline` is the proof that a
worker started from that env file and one started with no env at all resolve to
the same value for every field that shapes a document."""

import pytest
from helpers import env_file as _env_file
from helpers import settings_from as _settings_from

from kashi_server.config import (
    OPERATIONAL_FIELDS,
    PIPELINE_FIELDS,
    Settings,
    pipeline_defaults,
    pipeline_overrides,
    pipeline_value,
)
from kashi_server.version import PIPELINE_MAJOR, PIPELINE_VERSION


def _default(name: str):
    return Settings.model_fields[name].get_default(call_default_factory=True)


def test_pipeline_2_defaults():
    assert _default("separation_mode") == "always"
    assert _default("windowed_alignment") is True
    assert _default("separation_mixback") == 0.0


def test_the_production_chain_is_the_default():
    """2.33.0: the licence-clean chain production runs. Before, the defaults
    named the CC-BY-NC MMS checkpoint for every language and the unlicensed
    unwa separator, and only the env made the worker the measured pipeline."""
    assert _default("separation_model_filename") == "vocals_mel_band_roformer.ckpt"
    assert _default("structure_sections") is True
    table = pipeline_defaults()["align_models"]
    assert table == {
        "eng": {
            "checkpoint": "jonatasgrosman/wav2vec2-xls-r-1b-english",
            "romanize": None,
            "offset_ms": -50,
            "offset_by_initial": {"vowel": -80, "fricative": -50, "plosive": -30, "sonorant": -20},
        },
        "tur": {
            "checkpoint": "mpoyraz/wav2vec2-xls-r-300m-cv7-turkish",
            "romanize": False,
            "offset_ms": None,
            "offset_by_initial": None,
        },
    }
    # The fallback for every other language is unchanged (and still CC-BY-NC).
    assert _default("align_model") == "MahmoudAshraf/mms-300m-1130-forced-aligner"
    assert _default("align_romanize") is True
    assert _default("align_offset_ms") == 0


def test_the_default_table_is_a_fresh_copy_per_instance(monkeypatch):
    a, b = _settings_from(monkeypatch, {}), _settings_from(monkeypatch, {})
    a.align_models.pop("eng")
    assert "eng" in b.align_models and "eng" in pipeline_defaults()["align_models"]


def test_every_setting_is_classified_once():
    """A new field must be placed: does it shape documents or not?"""
    assert not set(PIPELINE_FIELDS) & set(OPERATIONAL_FIELDS)
    assert set(PIPELINE_FIELDS) | set(OPERATIONAL_FIELDS) == set(Settings.model_fields)


def test_the_worker_env_and_no_env_are_one_pipeline(monkeypatch):
    """The live worker's env (audit/worker.env.example, the copy of gitops this
    repository holds) and no env at all resolve every pipeline field alike."""
    env = _env_file()
    pipeline_keys = {key for key in env if key.lower() in PIPELINE_FIELDS}
    assert pipeline_keys == {"ALIGN_MODELS", "STRUCTURE_SECTIONS", "SEPARATION_MODEL_FILENAME"}
    from_env = _settings_from(monkeypatch, env)
    bare = _settings_from(monkeypatch, {})
    for name in PIPELINE_FIELDS:
        assert pipeline_value(name, getattr(from_env, name)) == pipeline_value(
            name, getattr(bare, name)
        ), name
    assert pipeline_overrides(from_env) == [] and pipeline_overrides(bare) == []


def test_an_env_value_still_overrides_and_is_named(monkeypatch):
    overridden = _settings_from(
        monkeypatch, {"STRUCTURE_SECTIONS": "false", "ALIGN_MODELS": "{}", "DATA_DIR": "/elsewhere"}
    )
    assert overridden.structure_sections is False and overridden.align_models == {}
    # Operational fields are not pipeline overrides; the order is PIPELINE_FIELDS'.
    assert pipeline_overrides(overridden) == ["align_models", "structure_sections"]


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("separation_model_filename", "mel_band_roformer_kim_ft_unwa.ckpt"),
        ("align_offset_ms", -1),
        ("windowed_alignment", False),
    ],
)
def test_one_changed_pipeline_value_is_seen(monkeypatch, name, value):
    bare = _settings_from(monkeypatch, {})
    monkeypatch.setattr(bare, name, value)
    assert pipeline_overrides(bare) == [name]


def test_a_worker_with_no_env_resolves_the_production_aligner_per_language(monkeypatch):
    """The 2026-11-13 verification (dogrulama/dg8_defaults.py): with a scrubbed
    env every language got the CC-BY-NC MMS checkpoint with no lateness
    correction, and the by-ear rung transcribed with it."""
    from kashi_server.config import settings
    from kashi_server.pipeline.alignment import resolve_aligner

    bare = _settings_from(monkeypatch, {})
    for name in PIPELINE_FIELDS:
        monkeypatch.setattr(settings, name, getattr(bare, name))
    eng, tur = resolve_aligner("eng"), resolve_aligner("tur")
    assert (eng.model_name, eng.romanize, eng.offset_ms) == (
        "jonatasgrosman/wav2vec2-xls-r-1b-english", True, -50)
    assert eng.offset_by_initial == {"vowel": -80, "fricative": -50, "plosive": -30,
                                     "sonorant": -20}
    assert (tur.model_name, tur.romanize, tur.offset_ms, tur.offset_by_initial) == (
        "mpoyraz/wav2vec2-xls-r-300m-cv7-turkish", False, 0, None)
    for other in ("deu", "spa", "jpn"):  # the fallback, as production runs it
        assert resolve_aligner(other).model_name == "MahmoudAshraf/mms-300m-1130-forced-aligner"
    # The by-ear rung's transcriber (process._lyrics_by_ear) is the English model.
    assert resolve_aligner("eng", None, None, None, None).model_name == eng.model_name


def test_the_ready_line_says_when_the_version_does_not_name_the_pipeline(monkeypatch):
    from kashi_server.config import settings
    from kashi_server.worker import main

    bare = _settings_from(monkeypatch, {})
    for name in PIPELINE_FIELDS:  # conftest switches separation and windows off
        monkeypatch.setattr(settings, name, getattr(bare, name))
    assert main._config_note() == "code-default configuration"
    monkeypatch.setattr(settings, "separation_model_filename", "other.ckpt")
    assert main._config_note() == (
        "env overrides separation_model_filename (not what the version names)"
    )


def test_nightcore_defaults():
    assert _default("nightcore_detection") is True


def test_fx_defaults():
    # Pipeline 2.9.0 (Faz 6.5 P4 calibration): the embedding line-theme
    # layer is ~half wrong at EVERY threshold on the labeled archive sample
    # — it defaults OFF; env re-enables it for experimentation only. The
    # keyword layer has no flag: dependency-free, always runs, and is the
    # precision path (docs/research/embed-threshold-calibration-2026-07.md).
    assert _default("fx_embeddings") is False


def test_telemetry_defaults():
    # Field diagnostics are ON by default, unlike the publish flags: rows only
    # appear when a client is pointed at THIS server with one of ITS keys, so
    # the data is the operator's own. Retention runs on the server clock.
    assert _default("telemetry_enabled") is True
    assert _default("telemetry_retention_days") == 30


def test_publishing_stays_off_by_default():
    """Production sets LRCLIB_PUBLISH_ENABLED=true; the code default stays off.
    Publishing writes to someone else's service: a self-host must opt in."""
    assert _default("lrclib_publish_enabled") is False
    assert _default("lrclib_publish_dry_run") is True


def test_pipeline_major_matches_the_archive_invalidation():
    assert PIPELINE_VERSION.startswith("2.")
    assert PIPELINE_MAJOR == 2
