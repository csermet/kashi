"""ffprobe verification of a downloaded file (VDL kit, DB-free core).

Catches silent failures yt-dlp reports as success: truncated files, missing
audio streams, unparseable containers, or a bitrate far below the tier we asked
for. Thresholds are MEASURED values (actual stream bitrate), distinct from the
nominal ones in errors.py.
"""

import json
import subprocess
from pathlib import Path

_MIN_PREMIUM_AAC_KBPS = 200  # AAC is CBR — actual ≈ nominal
_MIN_PREMIUM_OPUS_KBPS = 100  # Opus is VBR — average runs below nominal
_MIN_AUDIO_FILE_BYTES = 200 * 1024
_FFPROBE_TIMEOUT = 10.0

VERIFY_FAILED_ERROR_TYPE = "verify_failed"


def run_ffprobe(file_path: Path) -> dict | None:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                str(file_path),
            ],
            capture_output=True,
            timeout=_FFPROBE_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def verify_audio_file(file_path: Path) -> tuple[bool, str | None, int]:
    """(ok, reason, size_bytes) for an audio-only download."""
    try:
        size = file_path.stat().st_size
    except OSError:
        return False, "file missing", 0
    if size < _MIN_AUDIO_FILE_BYTES:
        return False, f"file too small ({size} bytes)", size

    probe = run_ffprobe(file_path)
    if probe is None:
        return False, "ffprobe failed", size

    audio_streams = [s for s in probe.get("streams", []) if s.get("codec_type") == "audio"]
    if not audio_streams:
        return False, "no audio stream", size

    stream = audio_streams[0]
    codec = (stream.get("codec_name") or "").lower()
    floor = _MIN_PREMIUM_OPUS_KBPS if codec in ("opus", "vorbis") else _MIN_PREMIUM_AAC_KBPS

    bitrate_bps = _first_number(stream.get("bit_rate"), probe.get("format", {}).get("bit_rate"))
    if bitrate_bps is None:
        # Containers that omit bit_rate: derive it from size/duration instead of
        # rejecting an otherwise healthy file.
        duration = _first_number(probe.get("format", {}).get("duration"))
        if not duration:
            return True, None, size
        bitrate_bps = size * 8 / duration

    kbps = bitrate_bps / 1000
    if kbps < floor:
        return False, f"bitrate {kbps:.0f} kbps below {floor} kbps floor ({codec})", size
    return True, None, size


def _first_number(*values: object) -> float | None:
    for value in values:
        if value in (None, "", "N/A"):
            continue
        try:
            return float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
    return None
