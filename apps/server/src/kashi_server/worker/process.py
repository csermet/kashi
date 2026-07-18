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
import wave
from dataclasses import dataclass, replace
from pathlib import Path

from prometheus_client import Counter
from sqlalchemy.orm import Session

from kashi_server import queue
from kashi_server.config import settings
from kashi_server.db.models import Job, UploadedAudio
from kashi_server.pipeline.alignment import AlignResult, align, quality_from_probs
from kashi_server.pipeline.audio_source import fetch_audio
from kashi_server.pipeline.beats import extract_beats
from kashi_server.pipeline.document import build_document, persist_processed_track
from kashi_server.pipeline.download import DownloadResult, download_audio
from kashi_server.pipeline.energy import extract_energy
from kashi_server.pipeline.langid import detect_language
from kashi_server.pipeline.line_qa import LineQAOutcome, apply_line_qa
from kashi_server.pipeline.lrclib import (
    LyricsText,
    fetch_lyrics,
    lyrics_from_record,
    lyrics_from_text,
    normalize_artist,
    search_candidates,
    title_covers,
)
from kashi_server.pipeline.lyricsfile import alignresult_from_lyricsfile
from kashi_server.pipeline.nightcore import (
    detect_speed_factor,
    pick_record_for_factor,
    rescale_result,
    rubberband_filter,
    slow_duration_ok,
)
from kashi_server.pipeline.palette import extract_palette
from kashi_server.pipeline.titles import clean_title
from kashi_server.vdl_kit.errors import JobCanceled, PipelineError, is_transient_error

logger = logging.getLogger(__name__)

SECOND_PASS_QUALITY_GATE = 0.5
# Wrong-song gate for DETECTED nightcore lyrics (field: "Come On Now" aligned
# against "Come On Eileen" at anchor-agreement 0.54 and would have shown wrong
# word karaoke). The calibrated CTC prob ramp separates right from wrong
# lyrics cleanly (measured: correct 0.675 / wrong 0.185); anchor agreement is
# self-fulfilling on the windowed path and cannot. Below this, the document
# is NOT persisted — the job fails honest. Caller-supplied lyrics_text skips
# the gate (trusted source; stretch artifacts alone could dip the prob).
NIGHTCORE_PROB_GATE = 0.3
# Windowed anchors assume the lrclib clock ≈ the audio clock (search rungs
# enforce ±3s). The duration-less q= last chance and title-only paths can
# legitimately pick a record from a DIFFERENT edit (field: a "video" upload
# with a lyricless intro runs minutes longer than the song) — there the
# stamps are globally shifted, windows search the wrong places and the whole
# doc comes out warped. Past this disagreement the anchors are dropped:
# whole-audio alignment absorbs a global offset naturally, and line QA's
# median-offset still snaps against the same (shifted) reference.
ANCHOR_CLOCK_TOLERANCE_S = 5.0
# Client/server EDIT mismatch (field, Sinsirella wUjSOU0p6f8): YTM played a
# 451s VIDEO while the downloadable audio for the same id was the 216s song
# — YouTube's music player clients substitute the song stream for video ids.
# A document timed to audio the browser never plays is confident nonsense;
# past this gap the job fails honest, naming both numbers. 30s: stale-hint
# jitter is seconds, a different edit is minutes.
CLIENT_EDIT_MISMATCH_S = 30.0

LINE_QA_DOCS = Counter(
    "kashi_line_qa_docs_total",
    "Documents by line-QA outcome",
    ["outcome"],  # clean | snapped | degraded
)
LINE_QA_SNAPPED_LINES = Counter(
    "kashi_line_qa_snapped_lines_total",
    "Lines snapped to lrclib reference times by line QA",
)
LINE_QA_DENSITY_DROPPED_LINES = Counter(
    "kashi_line_qa_density_dropped_lines_total",
    "Neighbour lines whose words were dropped by the border-case gate (QA v2)",
)
LINE_QA_ADLIB_SHIFTED_LINES = Counter(
    "kashi_line_qa_adlib_shifted_lines_total",
    "Nonlexical lines block-shifted onto their lrclib anchor",
)
LINE_QA_ADLIB_REDERIVED_LINES = Counter(
    "kashi_line_qa_adlib_rederived_lines_total",
    "Ad-lib lines whose word spans were redistributed across the line (Faz 4)",
)
NIGHTCORE_JOBS = Counter(
    "kashi_nightcore_jobs_total",
    "Jobs that entered the nightcore branch, by how the factor was resolved",
    ["outcome"],  # explicit | detected | reverted | explicit_failed
)
WORD_END_TRIMS = Counter(
    "kashi_line_qa_word_end_trims_total",
    "Word ends capped by the sustain trim (Faz 5 P1 ear-test fix)",
)
LYRICSFILE_DOCS = Counter(
    "kashi_lyricsfile_docs_total",
    "Documents built from human word-sync Lyricsfile data (CTC skipped)",
)


def _drop_staged_upload(s: Session, job: Job) -> None:
    """BYO-audio rows die with their job (Faz 5 P4): the AUDIO DELETION
    GUARANTEE extends to the database copy. Called inside the terminal
    transaction (completed / permanent fail); retries keep the row — the
    next attempt still needs the bytes. No-op for non-upload sources."""
    if job.source_type != "upload":
        return
    from sqlalchemy import delete

    s.execute(delete(UploadedAudio).where(UploadedAudio.id == job.source_id))


def checkpoint(s: Session, job: Job) -> None:
    """Re-read the row: a cancel or a stolen lease aborts between stages."""
    s.expire(job)
    s.refresh(job)
    if job.status == "canceled":
        raise JobCanceled
    queue.heartbeat(s, job.id)
    s.commit()


def _decode(src: Path, dest: Path, rate: int, *, tempo: float = 1.0) -> Path:
    """Decode to mono wav; tempo != 1 additionally rubberband-stretches BOTH
    tempo and pitch (nightcore slow-down; librubberband is in the image).
    Rubberband runs near realtime, hence its own timeout (20-min cap tracks
    would blow the plain 300 s budget)."""
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(src)]
    if tempo != 1.0:
        cmd += ["-af", rubberband_filter(tempo)]
    cmd += ["-ar", str(rate), "-ac", "1", str(dest)]
    result = subprocess.run(
        cmd,
        capture_output=True,
        timeout=300 if tempo == 1.0 else 1800,
    )
    if result.returncode != 0:
        raise PipelineError("other", f"ffmpeg decode failed: {result.stderr.decode()[:500]}")
    return dest


def _wav_duration_s(path: Path) -> float:
    with wave.open(str(path), "rb") as f:
        rate = f.getframerate()
        return f.getnframes() / rate if rate else 0.0


def _separate_vocals(
    audio: Path,
    tmp: Path,
    *,
    model_filename: str | None = None,
    mixback: float | None = None,
) -> Path:
    """Vocal stem via audio-separator (needs the `separate` extra). Imported
    lazily: separation_mode is 'off' by default and the dependency stack is
    heavy. The keyword overrides exist for the benchmark harness; the worker
    always runs on settings."""
    from audio_separator.separator import Separator  # pyright: ignore[reportMissingImports]

    separator = Separator(
        output_dir=str(tmp / "separated"),
        output_single_stem="Vocals",
        model_file_dir=str(settings.model_cache_dir / "audio-separator"),
        # Default 0.9 attenuates any stem peaking above it — keep original
        # levels so the mixback blend stays faithful (amplification_threshold
        # stays 0.0, so nothing is boosted either).
        normalization_threshold=1.0,
    )
    separator.load_model(model_filename=model_filename or settings.separation_model_filename)
    outputs = separator.separate(str(audio), custom_output_names={"Vocals": "vocals"})
    if not outputs:
        raise PipelineError("alignment_failed", "vocal separation produced no output")
    path = Path(outputs[0])
    vocals = path if path.is_absolute() else tmp / "separated" / path.name
    weight = settings.separation_mixback if mixback is None else mixback
    if weight <= 0:
        return vocals
    return _mix_back(vocals, audio, tmp / "separated" / "vocals-mixback.wav", weight)


def _mix_back(vocals: Path, mix: Path, dest: Path, weight: float) -> Path:
    """vocals + `weight` x original mix. Rates may differ (stem models emit
    44.1k, YouTube opus is 48k), so both inputs go through aresample first."""
    graph = (
        f"[0:a]aresample=48000,aformat=channel_layouts=stereo[v];"
        f"[1:a]aresample=48000,aformat=channel_layouts=stereo[m];"
        f"[v][m]amix=inputs=2:duration=first:weights='1 {weight}':normalize=0"
    )
    result = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(vocals), "-i", str(mix),
         "-filter_complex", graph, "-c:a", "pcm_s16le", str(dest)],
        capture_output=True,
        timeout=600,
    )
    if result.returncode != 0:
        raise PipelineError("other", f"ffmpeg mixback failed: {result.stderr.decode()[:500]}")
    return dest


def _align_stage(
    s: Session,
    job: Job,
    tmp: Path,
    source_audio: Path,
    lyrics: LyricsText,
    *,
    align_wav: Path | None = None,
    tempo: float = 1.0,
) -> tuple[AlignResult, bool]:
    """Align; optionally re-align on separated vocals when the score is low.

    `align_wav` skips the first decode when the caller already produced it
    (the nightcore branch decodes early for the duration sanity check);
    `tempo` carries the same slow-down into the second-pass vocal decode so
    both passes align the same clock."""
    language = detect_language(lyrics.full_text)
    # Windowing needs the lrclib stamps; the flag is the single rollout switch.
    anchors = lyrics.synced_starts_ms if settings.windowed_alignment else None
    wav = align_wav or _decode(source_audio, tmp / "align.wav", rate=16000, tempo=tempo)
    if anchors is not None and lyrics.record_duration_s is not None:
        wav_s = _wav_duration_s(wav)
        if abs(wav_s - lyrics.record_duration_s) > ANCHOR_CLOCK_TOLERANCE_S:
            logger.info(
                "job %s: anchors disabled — record edit %.0fs vs audio %.0fs "
                "(different edit; whole-audio alignment absorbs the offset)",
                job.id,
                lyrics.record_duration_s,
                wav_s,
            )
            anchors = None
    result = align(wav, lyrics.line_texts, language, synced_starts_ms=anchors)

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
        vocal_wav = _decode(vocals, tmp / "align-vocals.wav", rate=16000, tempo=tempo)
        second = align(vocal_wav, lyrics.line_texts, language, synced_starts_ms=anchors)
        if second.quality_score > result.quality_score:
            return second, True
    return result, False


def _detect_nightcore(job: Job, download: DownloadResult) -> tuple[float, dict | None, str | None]:
    """(speed_factor, detection_record, outcome) for this job.

    Explicit `options.speed_factor` wins (no record — lyrics resolve later);
    otherwise, when detection is on and the title (or `original_title`)
    points at an original song, the lrclib duration-ratio probe runs. Any
    miss → (1.0, None, None) = today's flow, byte for byte.
    """
    options = job.options or {}
    hints = job.hints or {}
    explicit = options.get("speed_factor")
    if isinstance(explicit, int | float) and float(explicit) > 1.0:
        return float(explicit), None, "explicit"
    if not settings.nightcore_detection:
        return 1.0, None, None
    query_title = options.get("original_title") or clean_title(hints.get("title") or "")
    if not query_title:
        return 1.0, None, None
    artist = normalize_artist(hints.get("artist") or "")
    detected = detect_speed_factor(
        _original_song_candidates(query_title, artist), download.duration_s
    )
    if detected is None:
        return 1.0, None, None
    r, record = detected
    logger.info("job %s: nightcore detected r=%.3f via %r", job.id, r, query_title)
    return r, record, "detected"


def _original_song_candidates(query_title: str, artist: str) -> list[dict]:
    """Plausibility-filtered lrclib candidates for the ORIGINAL song behind a
    nightcore upload. The hint artist is usually a CHANNEL name ("Syrex") —
    it may help the query but is never required for plausibility (title
    overlap + the duration-ratio band carry the signal; reviewer guard,
    2.2.1/2.2.2). A channel-polluted query that yields nothing plausible
    retries once with the title alone."""

    def plausible(records: list[dict]) -> list[dict]:
        # Containment, not overlap: the artist axis is a channel name here, so
        # the title must carry ALL its significant tokens ("Come On Now" must
        # not accept "Come On Eileen" — field failure 2026-07-13).
        return [rec for rec in records if title_covers(rec.get("trackName") or "", query_title)]

    candidates = plausible(
        search_candidates(f"{artist} {query_title}".strip(), base_url=settings.lrclib_base_url)
    )
    if not candidates and artist:
        candidates = plausible(search_candidates(query_title, base_url=settings.lrclib_base_url))
    return candidates


def _caller_lyrics(job: Job) -> LyricsText | None:
    """Caller-supplied `options.lyrics_text`, or None. Honored on EVERY path —
    the escape hatch must work exactly when detection failed or reverted
    (retro finding: it was dead in the r=1 flow)."""
    text = (job.options or {}).get("lyrics_text")
    if isinstance(text, str) and text.strip():
        return lyrics_from_text(text)
    return None


def _plain_lyrics(job: Job) -> LyricsText:
    """Lyrics for the r=1 flow. `options.original_title` repairs a polluted
    upload title for the lookup (the duration-less q= last chance absorbs the
    duration mismatch when the audio really is a reupload)."""
    caller = _caller_lyrics(job)
    if caller is not None:
        return caller
    hints = dict(job.hints or {})
    original = (job.options or {}).get("original_title")
    if isinstance(original, str) and original.strip():
        hints["title"] = original.strip()
    return fetch_lyrics(hints, base_url=settings.lrclib_base_url)


def _nightcore_lyrics(
    job: Job, download: DownloadResult, record: dict | None, r: float
) -> LyricsText:
    """Lyrics for a CONFIRMED nightcore job: caller-supplied text beats the
    detection record; the explicit-factor path (no record yet) searches for
    the original by duration ratio."""
    options = job.options or {}
    hints = job.hints or {}
    caller = _caller_lyrics(job)
    if caller is not None:
        return caller
    if record is not None:
        return lyrics_from_record(record)
    query_title = (
        options.get("original_title")
        or clean_title(hints.get("title") or "")
        or hints.get("title")
        or ""
    )
    artist = normalize_artist(hints.get("artist") or "")
    picked = pick_record_for_factor(
        _original_song_candidates(query_title, artist), download.duration_s, r
    )
    if picked is None:
        raise PipelineError(
            "lyrics_not_found",
            f"no original-song lyrics for nightcore job ({artist} - {query_title})",
        )
    return lyrics_from_record(picked)


@dataclass(frozen=True)
class NightcorePlan:
    """One job's resolved nightcore decision: the clock alignment runs on,
    the lyrics to align, the pre-decoded wav (the branch decodes early for
    the duration sanity gate) and how the branch resolved (metric label)."""

    speed_factor: float
    lyrics: LyricsText
    align_wav: Path | None
    outcome: str | None  # explicit | detected | reverted | None = plain r=1


def resolve_nightcore(
    job: Job,
    download: DownloadResult,
    source_audio: Path,
    tmp: Path,
    *,
    detection: tuple[float, dict | None, str | None] | None = None,
    plain_lyrics: LyricsText | None = None,
) -> NightcorePlan:
    """Resolve the whole nightcore branch up front: detection, lyrics, the
    slowed decode and its duration sanity gate in one place. Every miss lands
    on the plain r=1 plan; an explicit factor that fails the sanity gate
    raises instead of silently reverting — the caller stated it and cannot
    see a wrong clock. `detection`/`plain_lyrics` accept the caller's already
    -computed values (the lyricsfile fast-path probe) so no lrclib request is
    ever paid twice."""
    speed_factor, detection_record, nc_outcome = (
        detection if detection is not None else _detect_nightcore(job, download)
    )
    if speed_factor == 1.0 or nc_outcome is None:
        return NightcorePlan(1.0, plain_lyrics or _plain_lyrics(job), None, None)
    # Lyrics resolve BEFORE the near-realtime rubberband stretch: a doomed
    # lyrics_not_found must not cost a 30-minute decode first (retro finding
    # — the explicit-r path decoded, then searched).
    lyrics = _nightcore_lyrics(job, download, detection_record, speed_factor)
    align_wav = _decode(source_audio, tmp / "align.wav", rate=16000, tempo=1.0 / speed_factor)
    if slow_duration_ok(_wav_duration_s(align_wav), download.duration_s, speed_factor):
        NIGHTCORE_JOBS.labels(nc_outcome).inc()
        return NightcorePlan(speed_factor, lyrics, align_wav, nc_outcome)
    if nc_outcome == "explicit":
        # The caller STATED this factor; a document silently produced on the
        # r=1 clock would be wrong in a way they cannot see.
        NIGHTCORE_JOBS.labels("explicit_failed").inc()
        raise PipelineError(
            "alignment_failed",
            f"explicit speed_factor {speed_factor:g} fails the slowed-copy "
            f"duration sanity check (slowed {_wav_duration_s(align_wav):.1f}s "
            f"vs expected {download.duration_s * speed_factor:.1f}s)",
        )
    logger.warning(
        "job %s: slowed copy fails the duration sanity check — "
        "nightcore r=%.3f reverted to the normal flow",
        job.id,
        speed_factor,
    )
    NIGHTCORE_JOBS.labels("reverted").inc()
    align_wav = _decode(source_audio, tmp / "align.wav", rate=16000)
    return NightcorePlan(1.0, plain_lyrics or _plain_lyrics(job), align_wav, "reverted")


def _tag_fx(result, lyrics):
    """FX tagging over the FINAL line/word structure (indices reference what
    the client renders). Best-effort like palette/beats — never fails a job.
    Tags are (line, word) indices, so the nightcore rescale is irrelevant."""
    try:
        from kashi_server.pipeline.langid import detect_language
        from kashi_server.pipeline.semantics import get_embedder, tag_words

        embedder = None
        if settings.fx_embeddings:
            try:
                embedder = get_embedder(cache_dir=str(settings.model_cache_dir))
            except ImportError:
                logger.warning("fx_embeddings on but semantics extra missing — keywords only")
        language = {"eng": "en", "tur": "tr"}.get(
            detect_language(lyrics.full_text), "default"
        )
        return tag_words(
            [[w.text for w in chunk] for chunk in result.words_per_line],
            [line.text for line in result.lines],
            language=language,
            embedder=embedder,
        )
    except Exception as exc:  # noqa: BLE001 - enrichment, never a job failure
        logger.warning("fx tagging failed (%s) — document ships without fx", exc)
        return None


def process_job(s: Session, job: Job) -> None:
    tmp = settings.data_dir / f"job-{job.id}"
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        checkpoint(s, job)

        # --- downloading (claim already set the status) ---
        # youtube_fetch is passed from THIS module so the download_audio
        # monkeypatch seam survives the source dispatch (Faz 5 P4).
        download: DownloadResult = fetch_audio(job, tmp, s, youtube_fetch=download_audio)
        hinted_ms = (job.hints or {}).get("duration_ms")
        if (
            isinstance(hinted_ms, int)
            and not isinstance(hinted_ms, bool)
            and hinted_ms > 0
            and abs(hinted_ms / 1000 - download.duration_s) > CLIENT_EDIT_MISMATCH_S
        ):
            raise PipelineError(
                "alignment_failed",
                f"the client reports a {hinted_ms // 1000}s edit but the downloadable "
                f"audio is {download.duration_s:.0f}s — the browser plays a different "
                f"video/song edit than the pipeline can fetch; play the song entry "
                f"instead, or upload the audio you actually hear (reprocess accepts "
                f"corrected hints)",
            )
        checkpoint(s, job)

        # --- aligning (lyrics resolve FIRST — Faz 5 P3) ---
        queue.set_status(s, job, "aligning")
        s.commit()
        # Lyricsfile fast path: when the chosen lrclib record carries HUMAN
        # word timings, alignment has nothing to add — separation, langid,
        # CTC and line QA are all skipped (the word clock becomes human-
        # accurate and the job saves the double-digit-minute separation
        # bill). Only the plain r=1 flow qualifies: nightcore MUST keep the
        # CTC wrong-song gate. Resolving lyrics before separation also means
        # a doomed lyrics_not_found no longer pays for separation first
        # (the 2.2.4 lyrics-before-decode lesson, one stage earlier).
        detection = _detect_nightcore(job, download)
        plain_lyrics = _plain_lyrics(job) if detection[0] == 1.0 else None
        fast_result = (
            alignresult_from_lyricsfile(plain_lyrics.lyricsfile_raw, download.duration_s)
            if plain_lyrics is not None and plain_lyrics.source == "lrclib"
            else None
        )
        checkpoint(s, job)
        qa: LineQAOutcome | None = None
        if fast_result is not None:
            assert plain_lyrics is not None
            lyrics = replace(plain_lyrics, source="lyricsfile")
            result = fast_result
            vocals_separated = False
            speed_factor = 1.0
            LYRICSFILE_DOCS.inc()
            logger.info(
                "job %s: lyricsfile fast path (%d lines, %d with words) — CTC skipped",
                job.id,
                len(result.lines),
                sum(bool(chunk) for chunk in result.words_per_line),
            )
        else:
            separate_first = settings.separation_mode == "always" or bool(
                (job.options or {}).get("separate")
            )
            if separate_first:
                queue.set_status(s, job, "separating")
                s.commit()
                source_audio = _separate_vocals(download.path, tmp)
                checkpoint(s, job)
                queue.set_status(s, job, "aligning")
                s.commit()
            else:
                source_audio = download.path

            # Nightcore branch (Faz 4): slow the (possibly separated) audio
            # back down for alignment, then rescale the output onto the
            # played clock. Every failure reverts to the plain r=1 flow.
            plan = resolve_nightcore(
                job, download, source_audio, tmp, detection=detection, plain_lyrics=plain_lyrics
            )
            if plan.outcome is not None:  # the branch decoded — same cadence as before
                checkpoint(s, job)
            lyrics = plan.lyrics
            speed_factor = plan.speed_factor
            result, second_pass_separated = _align_stage(
                s,
                job,
                tmp,
                source_audio,
                lyrics,
                align_wav=plan.align_wav,
                tempo=1.0 / speed_factor,
            )
            vocals_separated = second_pass_separated or separate_first
            qa = apply_line_qa(result, lyrics.line_texts, lyrics.synced_starts_ms)
            result = qa.result
            if speed_factor != 1.0:
                if lyrics.source != "caller":
                    # Wrong-song gate: detection can only vouch for
                    # title+duration ratio; the CTC probs are the honest
                    # lyrics-identity signal.
                    probs = [w.prob for chunk in result.words_per_line for w in chunk]
                    prob_quality = quality_from_probs(probs) if probs else 0.0
                    if prob_quality < NIGHTCORE_PROB_GATE:
                        raise PipelineError(
                            "lyrics_not_found",
                            f"nightcore lyrics failed the wrong-song gate "
                            f"(ctc prob {prob_quality:.3f} < {NIGHTCORE_PROB_GATE}; "
                            f"lrclib id {lyrics.source_id})",
                        )
                # QA ran on the slowed (≈ original) clock where the lrclib
                # stamps live; ONE rescale lands everything on the nightcore
                # clock.
                result = rescale_result(result, speed_factor)
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
                LINE_QA_DENSITY_DROPPED_LINES.inc(len(qa.density_dropped))
            LINE_QA_ADLIB_SHIFTED_LINES.inc(len(qa.adlib_shifted))
            LINE_QA_ADLIB_REDERIVED_LINES.inc(len(qa.adlib_rederived))
            WORD_END_TRIMS.inc(qa.trimmed_ends)
            checkpoint(s, job)

        # --- postprocessing ---
        queue.set_status(s, job, "postprocessing")
        s.commit()
        beats = extract_beats(download.path)  # full mix — the PLAYED audio, never rescaled
        energy_and_sections = extract_energy(download.path)  # same clock as beats
        fx = _tag_fx(result, lyrics)  # AFTER QA/rescale: indices must match the doc
        palette = extract_palette((job.hints or {}).get("artwork_url"))
        doc = build_document(
            job,
            lyrics,
            result,
            beats,
            palette,
            vocals_separated=vocals_separated,
            speed_factor=speed_factor,
            fallback_duration_ms=round(download.duration_s * 1000),
            qa=qa,
            fx=fx,
            energy=energy_and_sections[0] if energy_and_sections else None,
            sections=energy_and_sections[1] if energy_and_sections else None,
        )
        persist_processed_track(s, job, doc)
        queue.mark_completed(s, job)
        _drop_staged_upload(s, job)  # same transaction as the completion
        s.commit()
        logger.info(
            "job %s completed: %s quality=%.3f basis=%s",
            job.id,
            doc["sync"],
            result.quality_score,
            doc["alignment"].get("quality_basis", "?"),
        )

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
        _drop_staged_upload(s, job)  # permanent fail is terminal too
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
