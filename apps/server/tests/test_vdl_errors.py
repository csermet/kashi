"""The 12-type taxonomy is a contract (worker retries, API field, dashboards)."""

import pytest

from kashi_server.vdl_kit.errors import (
    COOKIE_EXPIRED_ERROR_MARKER,
    RATE_LIMIT_ERROR_MARKER,
    TRACK_ERROR_TYPES,
    audio_codec_family,
    classify_error_message,
    is_transient_error,
    parse_ytdlp_error,
    validate_audio_quality,
)


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("ERROR: This content isn't available, try again later", "rate_limited"),
        ("HTTP Error 429: Too Many Requests", "rate_limited"),
        ("Sign in to confirm you're not a bot", "cookie_expired"),
        ("Use --cookies-from-browser to pass cookies", "cookie_expired"),
        ("Low-quality audio (Premium expected): 70 kbps", "low_quality_audio"),
        ("Video unavailable. This video has been removed by the uploader", "video_unavailable"),
        ("The uploader has not made this video available in your country", "geo_blocked"),
        ("Video unavailable: the owner blocked it on copyright grounds", "copyright"),
        # VDL's ordering: the cookie markers win over the age/private keywords,
        # because "sign in to confirm" is what a stale cookie actually produces.
        ("Sign in to confirm your age. This video may be inappropriate", "cookie_expired"),
        ("This video is age restricted", "age_restricted"),
        ("Private video. Sign in if you've been granted access", "private"),
        ("This video is private", "private"),
        ("[Errno 28] No space left on device", "disk_full"),
        ("Unable to download: connection timed out", "network"),
        ("Something entirely unexpected happened", "other"),
    ],
)
def test_classify_error_message(message, expected):
    assert classify_error_message(message) == expected
    assert expected in TRACK_ERROR_TYPES


def test_marker_strings_round_trip_through_parse():
    assert parse_ytdlp_error(Exception("http error 429")) == RATE_LIMIT_ERROR_MARKER
    assert parse_ytdlp_error(Exception("please sign in")) == COOKIE_EXPIRED_ERROR_MARKER
    assert parse_ytdlp_error(Exception("weird failure")) == "weird failure"


def test_transient_set_excludes_disk_full():
    assert is_transient_error("network")
    assert is_transient_error("verify_failed")
    assert not is_transient_error("disk_full")  # retrying a full disk is futile
    assert not is_transient_error("copyright")
    assert not is_transient_error(None)


def test_audio_codec_family():
    assert audio_codec_family("opus") == "opus"
    assert audio_codec_family("libopus") == "opus"
    assert audio_codec_family("mp4a.40.2") == "aac"
    assert audio_codec_family("aac") == "aac"
    assert audio_codec_family(None) == ""
    assert audio_codec_family("flac") == ""


def _fmt(abr, acodec, **extra):
    return {"vcodec": "none", "acodec": acodec, "abr": abr, **extra}


def test_quality_gate_accepts_premium_download():
    info = {"abr": 250, "acodec": "opus", "formats": [_fmt(250, "opus"), _fmt(256, "mp4a.40.2")]}
    ok, abr, max_abr = validate_audio_quality(info)
    assert ok and abr == 250 and max_abr == 250  # compared within the opus family


def test_quality_gate_rejects_downgrade_within_family():
    info = {"abr": 70, "acodec": "opus", "formats": [_fmt(70, "opus"), _fmt(250, "opus")]}
    ok, abr, max_abr = validate_audio_quality(info)
    assert not ok and abr == 70 and max_abr == 250


def test_quality_gate_ignores_other_codec_family():
    """Premium AAC existing must not condemn a good Opus download."""
    info = {"abr": 128, "acodec": "opus", "formats": [_fmt(128, "opus"), _fmt(256, "mp4a.40.2")]}
    ok, _, max_abr = validate_audio_quality(info)
    assert ok and max_abr == 128


def test_quality_gate_accepts_low_quality_source():
    info = {"abr": 96, "acodec": "opus", "formats": [_fmt(96, "opus")]}
    assert validate_audio_quality(info)[0]


def test_quality_gate_skips_drm_formats():
    info = {
        "abr": 128,
        "acodec": "opus",
        "formats": [_fmt(128, "opus"), _fmt(900, "opus", has_drm=True)],
    }
    assert validate_audio_quality(info)[0]


def test_quality_gate_reads_requested_formats():
    info = {
        "requested_formats": [{"vcodec": "none", "acodec": "opus", "abr": 250}],
        "formats": [_fmt(250, "opus")],
    }
    ok, abr, _ = validate_audio_quality(info)
    assert ok and abr == 250


def test_quality_gate_trusts_download_without_bitrate_info():
    assert validate_audio_quality({"formats": [_fmt(250, "opus")]})[0]
