"""The 2.0.0 pipeline contract: every default below is a MEASURED decision
(docs/research/hizalama-v2-benchmark-2026-07.md) — a drive-by change here
must consciously re-litigate the benchmark, so the values are pinned.

Asserted on the FIELD DEFAULTS (not a Settings() instance) so ambient env
vars can neither fail nor mask this test."""

from kashi_server.config import Settings
from kashi_server.version import PIPELINE_MAJOR, PIPELINE_VERSION


def _default(name: str):
    return Settings.model_fields[name].default


def test_pipeline_2_defaults():
    assert _default("separation_mode") == "always"
    assert _default("windowed_alignment") is True
    assert _default("separation_model_filename") == "mel_band_roformer_kim_ft_unwa.ckpt"
    assert _default("separation_mixback") == 0.0


def test_nightcore_defaults():
    assert _default("nightcore_detection") is True


def test_fx_defaults():
    # Pipeline 2.9.0 (Faz 6.5 P4 calibration): the embedding line-theme
    # layer is ~half wrong at EVERY threshold on the labeled archive sample
    # — it defaults OFF; env re-enables it for experimentation only. The
    # keyword layer has no flag: dependency-free, always runs, and is the
    # precision path (docs/research/embed-threshold-calibration-2026-07.md).
    assert _default("fx_embeddings") is False


def test_pipeline_major_matches_the_archive_invalidation():
    assert PIPELINE_VERSION.startswith("2.")
    assert PIPELINE_MAJOR == 2
