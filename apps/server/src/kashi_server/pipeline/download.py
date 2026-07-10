"""Download stage: yt-dlp -> a verified audio file in the job's tmp dir.

The `ydl_factory` seam keeps the unit tests off YouTube: they inject a fake that
writes a generated wav and returns a canned info dict.

CLI (manual stage test):  python -m kashi_server.pipeline.download <video_id> <dest_dir>
"""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from kashi_server.vdl_kit.errors import (
    LOW_QUALITY_AUDIO_MARKER,
    PipelineError,
    classify_ytdlp_error,
    parse_ytdlp_error,
    validate_audio_quality,
)
from kashi_server.vdl_kit.verify import verify_audio_file
from kashi_server.vdl_kit.ytdlp_opts import AUDIO_FORMAT_TIERED, common_ytdlp_opts

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DownloadResult:
    path: Path
    abr: float
    acodec: str
    duration_s: float
    info: dict[str, Any]


class _YoutubeDL(Protocol):  # pragma: no cover - typing only
    def __enter__(self) -> "_YoutubeDL": ...
    def __exit__(self, *exc: object) -> None: ...
    def extract_info(self, url: str, download: bool = True) -> dict[str, Any]: ...


def _default_factory(opts: dict[str, Any]) -> _YoutubeDL:
    import yt_dlp

    return yt_dlp.YoutubeDL(opts)  # type: ignore[return-value]


def _resolve_downloaded_path(info: dict[str, Any], dest_dir: Path) -> Path:
    requested = info.get("requested_downloads") or []
    if requested and requested[0].get("filepath"):
        return Path(requested[0]["filepath"])
    if info.get("filepath"):
        return Path(info["filepath"])
    audio_files = [p for p in dest_dir.iterdir() if p.is_file() and not p.name.endswith(".part")]
    if not audio_files:
        raise PipelineError("other", "yt-dlp reported success but produced no file")
    return max(audio_files, key=lambda p: p.stat().st_size)


def download_audio(
    video_id: str,
    dest_dir: Path,
    *,
    max_duration_s: int,
    ydl_factory=_default_factory,
) -> DownloadResult:
    dest_dir.mkdir(parents=True, exist_ok=True)
    opts = common_ytdlp_opts()
    opts.update(
        {
            "format": AUDIO_FORMAT_TIERED,
            "outtmpl": str(dest_dir / "audio.%(ext)s"),
            "paths": {"home": str(dest_dir)},
        }
    )

    url = f"https://www.youtube.com/watch?v={video_id}"
    try:
        with ydl_factory(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except PipelineError:
        raise
    except Exception as exc:  # yt-dlp raises a zoo of types; the taxonomy sorts them
        raise PipelineError(classify_ytdlp_error(exc), parse_ytdlp_error(exc)) from exc

    duration_s = float(info.get("duration") or 0.0)
    if duration_s > max_duration_s:
        # Permanent: a 2-hour mix is not a track we align.
        raise PipelineError("other", f"track too long ({duration_s:.0f}s > {max_duration_s}s)")

    ok, abr, max_abr = validate_audio_quality(info)
    if not ok:
        raise PipelineError(
            "low_quality_audio",
            f"{LOW_QUALITY_AUDIO_MARKER}: got {abr:.0f} kbps, best available {max_abr:.0f} kbps",
        )

    path = _resolve_downloaded_path(info, dest_dir)
    verified, reason, size = verify_audio_file(path)
    if not verified:
        raise PipelineError("verify_failed", f"{path.name}: {reason}")

    logger.info(
        "downloaded %s: %s %.0f kbps, %.0fs, %d bytes",
        video_id,
        info.get("acodec"),
        abr,
        duration_s,
        size,
    )
    return DownloadResult(
        path=path,
        abr=abr,
        acodec=str(info.get("acodec") or ""),
        duration_s=duration_s,
        info=info,
    )


def _main() -> int:  # pragma: no cover - manual stage test
    import argparse

    from kashi_server.config import settings

    parser = argparse.ArgumentParser(description="Download one track's audio (manual test).")
    parser.add_argument("video_id")
    parser.add_argument("dest_dir", type=Path)
    parser.add_argument("--simulate", action="store_true", help="metadata only (CI canary)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if args.simulate:
        import yt_dlp

        with yt_dlp.YoutubeDL(common_ytdlp_opts()) as ydl:
            info = ydl.extract_info(
                f"https://www.youtube.com/watch?v={args.video_id}", download=False
            )
        formats = len(info.get("formats", []))
        print(f"ok: {info.get('title')} ({info.get('duration')}s, {formats} formats)")
        return 0

    result = download_audio(
        args.video_id, args.dest_dir, max_duration_s=settings.max_track_duration_s
    )
    print(
        f"path={result.path}\nabr={result.abr}\nacodec={result.acodec}\nduration={result.duration_s}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
