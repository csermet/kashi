"""_separate_vocals + ensure_separator unit tests (hizalama-v2 P2).

audio-separator itself is faked via sys.modules (the `separate` extra is not
installed in the fast suite); the mixback pass runs the REAL ffmpeg when one
is on PATH and is skipped otherwise (CI runners ship without ffmpeg).
"""

import shutil
import sys
import types
import wave
from pathlib import Path

import pytest

from kashi_server.config import settings
from kashi_server.vdl_kit.errors import PipelineError
from kashi_server.worker import process as wp
from kashi_server.worker import warmup

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="no ffmpeg on PATH")


def _write_wav(path: Path, seconds: float = 0.2, rate: int = 8000) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(seconds * rate))
    return path


def _install_fake_separator(monkeypatch, record: dict, outputs=("vocals.wav",)):
    class FakeSeparator:
        def __init__(self, **kwargs):
            record["init"] = kwargs

        def load_model(self, model_filename):
            record["model"] = model_filename

        def separate(self, path, custom_output_names=None):
            record["input"] = path
            record["output_names"] = custom_output_names
            out_dir = Path(record["init"]["output_dir"])
            for name in outputs:
                _write_wav(out_dir / name)
            return list(outputs)  # relative names, like the real library

    sep_mod = types.ModuleType("audio_separator.separator")
    sep_mod.Separator = FakeSeparator  # pyright: ignore[reportAttributeAccessIssue]
    pkg = types.ModuleType("audio_separator")
    pkg.separator = sep_mod  # pyright: ignore[reportAttributeAccessIssue]
    monkeypatch.setitem(sys.modules, "audio_separator", pkg)
    monkeypatch.setitem(sys.modules, "audio_separator.separator", sep_mod)
    return record


@needs_ffmpeg
def test_separate_defaults_come_from_settings(tmp_path, monkeypatch):
    record = _install_fake_separator(monkeypatch, {})
    monkeypatch.setattr(settings, "separation_mixback", 0.15)
    audio = _write_wav(tmp_path / "audio.wav")

    out = wp._separate_vocals(audio, tmp_path)

    assert record["model"] == settings.separation_model_filename
    assert record["input"] == str(audio)
    assert record["init"]["output_single_stem"] == "Vocals"
    # mixback > 0 -> the mixback file wins over the raw stem
    assert out == tmp_path / "separated" / "vocals-mixback.wav"
    assert out.exists() and out.stat().st_size > 44  # more than a wav header


def test_separate_honours_benchmark_overrides(tmp_path, monkeypatch):
    record = _install_fake_separator(monkeypatch, {})
    audio = _write_wav(tmp_path / "audio.wav")

    out = wp._separate_vocals(audio, tmp_path, model_filename="htdemucs_ft.yaml", mixback=0)

    assert record["model"] == "htdemucs_ft.yaml"
    # mixback=0 skips the ffmpeg pass entirely and returns the raw stem
    assert out == tmp_path / "separated" / "vocals.wav"


def test_separate_no_output_is_a_pipeline_error(tmp_path, monkeypatch):
    _install_fake_separator(monkeypatch, {}, outputs=())
    audio = _write_wav(tmp_path / "audio.wav")

    with pytest.raises(PipelineError) as exc:
        wp._separate_vocals(audio, tmp_path, mixback=0)
    assert exc.value.error_type == "alignment_failed"


@needs_ffmpeg
def test_mix_back_failure_is_classified(tmp_path):
    bogus = tmp_path / "not-audio.wav"
    bogus.write_bytes(b"definitely not audio")

    with pytest.raises(PipelineError) as exc:
        wp._mix_back(bogus, bogus, tmp_path / "out.wav", 0.15)
    assert exc.value.error_type == "other"


def test_ensure_separator_without_extra_raises(monkeypatch):
    monkeypatch.setattr(settings, "separation_mode", "always")
    # None in sys.modules makes `import audio_separator...` raise ImportError.
    monkeypatch.setitem(sys.modules, "audio_separator", None)
    monkeypatch.setitem(sys.modules, "audio_separator.separator", None)

    with pytest.raises(RuntimeError, match="without --extra separate"):
        warmup.ensure_separator()


def test_ensure_separator_loads_the_configured_model(monkeypatch):
    record = _install_fake_separator(monkeypatch, {})
    monkeypatch.setattr(settings, "separation_mode", "always")

    warmup.ensure_separator()

    assert record["model"] == settings.separation_model_filename
    assert record["init"]["model_file_dir"] == str(settings.model_cache_dir / "audio-separator")
