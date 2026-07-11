"""Process one claimed job through the pipeline stages.

Contracts enforced here (reviewer checklist):
- AUDIO DELETION GUARANTEE: the per-job tmp dir is removed in `finally`, on
  every path — success, classified failure, crash, cancellation.
- Cancellation/lease checkpoints at every stage boundary.
- Transient errors retry with increasing delay; permanent ones fail once.
"""

import logging
import shutil
import subprocess
import threading
from pathlib import Path

from prometheus_client import Counter
from sqlalchemy.orm import Session

from kashi_server import queue
from kashi_server.config import settings
from kashi_server.db.models import Job
from kashi_server.pipeline.alignment import AlignResult, align
from kashi_server.pipeline.beats import extract_beats
from kashi_server.pipeline.document import build_document, persist_processed_track
from kashi_server.pipeline.download import DownloadResult, download_audio
from kashi_server.pipeline.langid import detect_language
from kashi_server.pipeline.line_qa import apply_line_qa
from kashi_server.pipeline.lrclib import LyricsText, fetch_lyrics
from kashi_server.pipeline.palette import extract_palette
from kashi_server.vdl_kit.errors import JobCanceled, PipelineError, is_transient_error

logger = logging.getLogger(__name__)

SECOND_PASS_QUALITY_GATE = 0.5

LINE_QA_DOCS = Counter(
    "kashi_line_qa_docs_total",
    "Documents by line-QA outcome",
    ["outcome"],  # clean | snapped | degraded
)
LINE_QA_SNAPPED_LINES = Counter(
    "kashi_line_qa_snapped_lines_total",
    "Lines snapped to lrclib reference times by line QA",
)


def checkpoint(s: Session, job: Job) -> None:
    """Re-read the row: a cancel or a stolen lease aborts between stages."""
    s.expire(job)
    s.refresh(job)
    if job.status == "canceled":
        raise JobCanceled
    queue.heartbeat(s, job.id)
    s.commit()


def _decode(src: Path, dest: Path, rate: int) -> Path:
    result = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(src), "-ar", str(rate), "-ac", "1", str(dest)],
        capture_output=True,
        timeout=300,
    )
    if result.returncode != 0:
        raise PipelineError("other", f"ffmpeg decode failed: {result.stderr.decode()[:500]}")
    return dest


def _separate_vocals(audio: Path, tmp: Path) -> Path:
    """htdemucs vocals via audio-separator. Imported lazily: separation_mode is
    'off' by default and the dependency stack is heavy."""
    from audio_separator.separator import Separator  # pyright: ignore[reportMissingImports]

    separator = Separator(
        output_dir=str(tmp / "separated"),
        output_single_stem="Vocals",
        model_file_dir=str(settings.model_cache_dir / "audio-separator"),
    )
    separator.load_model(model_filename="htdemucs_ft.yaml")
    outputs = separator.separate(str(audio))
    if not outputs:
        raise PipelineError("alignment_failed", "vocal separation produced no output")
    path = Path(outputs[0])
    return path if path.is_absolute() else tmp / "separated" / path.name


def _align_stage(
    s: Session, job: Job, tmp: Path, source_audio: Path, lyrics: LyricsText
) -> tuple[AlignResult, bool]:
    """Align; optionally re-align on separated vocals when the score is low."""
    language = detect_language(lyrics.full_text)
    wav = _decode(source_audio, tmp / "align.wav", rate=16000)
    result = align(wav, lyrics.line_texts, language)

    if (
        result.quality_score < SECOND_PASS_QUALITY_GATE
        and settings.separation_mode == "second_pass"
    ):
        logger.info(
            "job %s: quality %.2f below gate — second pass with separated vocals",
            job.id,
            result.quality_score,
        )
        queue.set_status(s, job, "separating")
        s.commit()
        vocals = _separate_vocals(source_audio, tmp)
        checkpoint(s, job)
        queue.set_status(s, job, "aligning")
        s.commit()
        vocal_wav = _decode(vocals, tmp / "align-vocals.wav", rate=16000)
        second = align(vocal_wav, lyrics.line_texts, language)
        if second.quality_score > result.quality_score:
            return second, True
    return result, False


def process_job(s: Session, job: Job) -> None:
    tmp = settings.data_dir / f"job-{job.id}"
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        checkpoint(s, job)

        # --- downloading (claim already set the status) ---
        download: DownloadResult = download_audio(
            job.source_id, tmp, max_duration_s=settings.max_track_duration_s
        )
        checkpoint(s, job)

        if settings.separation_mode == "always" or (job.options or {}).get("separate"):
            queue.set_status(s, job, "separating")
            s.commit()
            source_audio = _separate_vocals(download.path, tmp)
            checkpoint(s, job)
        else:
            source_audio = download.path

        # --- aligning ---
        queue.set_status(s, job, "aligning")
        s.commit()
        lyrics = fetch_lyrics(job.hints or {}, base_url=settings.lrclib_base_url)
        result, vocals_separated = _align_stage(s, job, tmp, source_audio, lyrics)
        qa = apply_line_qa(result, lyrics.line_texts, lyrics.synced_starts_ms)
        result = qa.result
        if qa.degraded_to_line or qa.flagged:
            logger.warning(
                "job %s: line QA %s %d line(s), offset %+dms",
                job.id,
                "degraded to line sync after flagging" if qa.degraded_to_line else "snapped",
                len(qa.flagged),
                qa.offset_ms,
            )
        LINE_QA_DOCS.labels(
            "degraded" if qa.degraded_to_line else ("snapped" if qa.flagged else "clean")
        ).inc()
        if not qa.degraded_to_line:
            LINE_QA_SNAPPED_LINES.inc(len(qa.flagged))
        checkpoint(s, job)

        # --- postprocessing ---
        queue.set_status(s, job, "postprocessing")
        s.commit()
        beats = extract_beats(download.path)  # full mix, not vocals
        palette = extract_palette((job.hints or {}).get("artwork_url"))
        doc = build_document(
            job,
            lyrics,
            result,
            beats,
            palette,
            vocals_separated=vocals_separated or settings.separation_mode == "always",
            fallback_duration_ms=round(download.duration_s * 1000),
        )
        persist_processed_track(s, job, doc)
        queue.mark_completed(s, job)
        s.commit()
        logger.info("job %s completed: %s quality=%.3f", job.id, doc["sync"], result.quality_score)

    except JobCanceled:
        s.rollback()
        logger.info("job %s canceled mid-flight", job.id)
    except PipelineError as exc:
        s.rollback()
        _fail_or_retry(s, job, exc.error_type, exc.message)
    except Exception as exc:  # noqa: BLE001 - the worker must survive anything
        s.rollback()
        logger.exception("job %s crashed", job.id)
        _fail_or_retry(s, job, "other", str(exc))
    finally:
        # AUDIO DELETION GUARANTEE — every path ends here.
        shutil.rmtree(tmp, ignore_errors=True)


def _fail_or_retry(s: Session, job: Job, error_type: str, message: str) -> None:
    s.refresh(job)
    if is_transient_error(error_type) and job.attempts < job.max_attempts:
        delays = settings.retry_delays_s
        delay = delays[min(job.attempts - 1, len(delays) - 1)] if delays else 60
        logger.warning(
            "job %s: transient %s (attempt %d/%d), retrying in %ds: %s",
            job.id,
            error_type,
            job.attempts,
            job.max_attempts,
            delay,
            message[:200],
        )
        queue.retry(s, job, delay_s=delay)
    else:
        logger.error("job %s failed (%s): %s", job.id, error_type, message[:500])
        queue.mark_failed(s, job, error_type, message)
    s.commit()


class HeartbeatThread:
    """Extends the lease every 60 s while a job runs — belt to the checkpoint
    suspenders (a long alignment must not lose its lease mid-stage)."""

    def __init__(self, job_id, session_factory, interval_s: float = 60.0) -> None:
        self._job_id = job_id
        self._session_factory = session_factory
        self._interval = interval_s
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                with self._session_factory() as s:
                    queue.heartbeat(s, self._job_id)
                    s.commit()
            except Exception:  # noqa: BLE001 - heartbeat is best-effort
                logger.warning("heartbeat for job %s failed", self._job_id, exc_info=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        self._thread.join(timeout=5)
