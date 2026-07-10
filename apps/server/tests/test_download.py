"""download_audio with an injected yt-dlp: exercises every failure path
without touching YouTube (real download lives in the manual CLI + canary)."""

import wave

import pytest

from kashi_server.pipeline.download import download_audio
from kashi_server.vdl_kit.errors import PipelineError


def _write_wav(path, seconds=6.0, rate=44100):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x01" * int(rate * seconds))


class FakeYdl:
    """Writes a file like yt-dlp would, then returns a canned info dict."""

    def __init__(self, opts, *, info, raises=None, write=True, seconds=6.0):
        self.opts = opts
        self._info = info
        self._raises = raises
        self._write = write
        self._seconds = seconds

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download=True):
        if self._raises is not None:
            raise self._raises
        if self._write:
            path = self._info["requested_downloads"][0]["filepath"]
            _write_wav(__import__("pathlib").Path(path), seconds=self._seconds)
        return self._info


def _info(tmp_path, abr=250, acodec="opus", duration=200, formats=None):
    return {
        "abr": abr,
        "acodec": acodec,
        "duration": duration,
        "formats": formats or [{"vcodec": "none", "acodec": acodec, "abr": abr}],
        "requested_downloads": [{"filepath": str(tmp_path / "audio.wav")}],
    }


def _factory(**kwargs):
    return lambda opts: FakeYdl(opts, **kwargs)


pytestmark = pytest.mark.skipif(
    __import__("subprocess").run(["which", "ffprobe"], capture_output=True, check=False).returncode
    != 0,
    reason="ffprobe not installed",
)


def test_happy_path(tmp_path):
    result = download_audio(
        "vid", tmp_path, max_duration_s=1200, ydl_factory=_factory(info=_info(tmp_path))
    )
    assert result.path.exists() and result.abr == 250 and result.acodec == "opus"
    assert result.duration_s == 200


def test_ytdlp_options_carry_the_vdl_policy(tmp_path):
    captured = {}

    def factory(opts):
        captured.update(opts)
        return FakeYdl(opts, info=_info(tmp_path))

    download_audio("vid", tmp_path, max_duration_s=1200, ydl_factory=factory)
    assert captured["js_runtimes"] == {"deno": {}, "node": {}}  # dict form, not list
    assert captured["extractor_args"]["youtube"]["player_client"][0] == "tv"
    assert captured["extractor_retries"] == 0
    assert captured["format"].startswith("bestaudio[acodec^=opus][abr>200]")


def test_bgutil_only_wired_when_configured(tmp_path, monkeypatch):
    captured = {}

    def factory(opts):
        captured.update(opts)
        return FakeYdl(opts, info=_info(tmp_path))

    download_audio("vid", tmp_path, max_duration_s=1200, ydl_factory=factory)
    assert "youtubepot-bgutilhttp" not in captured["extractor_args"]

    monkeypatch.setenv("BGUTIL_POT_PROVIDER_URL", "http://pot:4416")
    download_audio("vid", tmp_path, max_duration_s=1200, ydl_factory=factory)
    assert captured["extractor_args"]["youtubepot-bgutilhttp"]["base_url"] == ["http://pot:4416"]


def test_too_long_track_is_permanent(tmp_path):
    with pytest.raises(PipelineError) as exc:
        download_audio(
            "vid",
            tmp_path,
            max_duration_s=60,
            ydl_factory=_factory(info=_info(tmp_path, duration=3600)),
        )
    assert exc.value.error_type == "other"


def test_low_quality_download_is_transient(tmp_path):
    formats = [
        {"vcodec": "none", "acodec": "opus", "abr": 70},
        {"vcodec": "none", "acodec": "opus", "abr": 250},
    ]
    with pytest.raises(PipelineError) as exc:
        download_audio(
            "vid",
            tmp_path,
            max_duration_s=1200,
            ydl_factory=_factory(info=_info(tmp_path, abr=70, formats=formats)),
        )
    assert exc.value.error_type == "low_quality_audio"


def test_truncated_download_fails_verification(tmp_path):
    with pytest.raises(PipelineError) as exc:
        download_audio(
            "vid",
            tmp_path,
            max_duration_s=1200,
            ydl_factory=_factory(info=_info(tmp_path), seconds=0.05),
        )
    assert exc.value.error_type == "verify_failed"


def test_missing_output_file(tmp_path):
    with pytest.raises(PipelineError) as exc:
        download_audio(
            "vid",
            tmp_path,
            max_duration_s=1200,
            ydl_factory=_factory(info=_info(tmp_path), write=False),
        )
    assert exc.value.error_type in ("other", "verify_failed")


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("HTTP Error 429", "rate_limited"),
        ("Sign in to confirm you're not a bot", "cookie_expired"),
        ("Video unavailable", "video_unavailable"),
        ("connection timed out", "network"),
    ],
)
def test_ytdlp_exceptions_are_classified(tmp_path, message, expected):
    with pytest.raises(PipelineError) as exc:
        download_audio(
            "vid",
            tmp_path,
            max_duration_s=1200,
            ydl_factory=_factory(info={}, raises=RuntimeError(message)),
        )
    assert exc.value.error_type == expected


def test_remote_components_enabled_for_ejs(tmp_path):
    """A fresh container has no cached EJS solver; without this option every
    format loses its URL and downloads 403 (hit at the 3A acceptance run)."""
    captured = {}

    def factory(opts):
        captured.update(opts)
        return FakeYdl(opts, info=_info(tmp_path))

    download_audio("vid", tmp_path, max_duration_s=1200, ydl_factory=factory)
    assert captured["remote_components"] == ["ejs:github"]
