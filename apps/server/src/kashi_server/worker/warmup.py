"""Alignment smoke gate.

`ctc-forced-aligner` pins no torch version and builds a C++ extension, so a
green install proves nothing. This module loads the model and aligns a 6-second
speech fixture; it runs at image build, in the CI slow job, and at worker
startup — a worker that cannot align must not claim jobs.

CLI: python -m kashi_server.worker.warmup
"""

import logging
import os
from pathlib import Path

from kashi_server.pipeline.alignment import align

logger = logging.getLogger(__name__)


def _fixture_dir() -> Path:
    """The image ships the fixtures at /app/fixtures (tests/ is not copied);
    a source checkout has them under apps/server/tests/fixtures."""
    candidates = [
        Path(os.environ["KASHI_FIXTURE_DIR"]) if os.environ.get("KASHI_FIXTURE_DIR") else None,
        Path(__file__).resolve().parents[3] / "tests" / "fixtures",
        Path("/app/fixtures"),
    ]
    for candidate in candidates:
        if candidate and (candidate / "speech-5s.wav").exists():
            return candidate
    return candidates[1]  # report the source-tree path in the error message


FIXTURE_WAV = _fixture_dir() / "speech-5s.wav"
FIXTURE_TXT = _fixture_dir() / "speech-5s.txt"

MIN_WORDS = 5


def ensure_model(wav: Path = FIXTURE_WAV, transcript: Path = FIXTURE_TXT) -> float:
    """Download (once) and exercise the model. Returns the smoke quality score."""
    if not wav.exists() or not transcript.exists():
        raise RuntimeError(f"warmup fixtures missing under {wav.parent}")

    line_texts = [line.strip() for line in transcript.read_text().splitlines() if line.strip()]
    result = align(wav, line_texts, "eng")

    words = [word for chunk in result.words_per_line for word in chunk]
    if result.sync != "word" or len(words) < MIN_WORDS:
        raise RuntimeError(f"warmup produced {len(words)} words (sync={result.sync})")
    first, last = words[0], words[-1]
    if not 0 <= first.start_ms < 5_000 or last.end_ms <= first.start_ms:
        raise RuntimeError(f"warmup timings implausible: {first} .. {last}")

    logger.info(
        "warmup ok: %d words, %.0f-%.0f ms, quality %.2f",
        len(words),
        first.start_ms,
        last.end_ms,
        result.quality_score,
    )
    return result.quality_score


def ensure_separator() -> None:
    """Download (once) and load the separation model. Called only when
    separation_mode != off — a worker configured to separate must not claim
    jobs it cannot process. The first download leans on the k8s startupProbe
    budget, exactly like the alignment model."""
    from kashi_server.config import settings

    try:
        from audio_separator.separator import Separator  # pyright: ignore[reportMissingImports]
    except ImportError as exc:  # the `separate` extra is not installed
        raise RuntimeError(
            f"separation_mode={settings.separation_mode} but audio-separator is not "
            "installed (image built without --extra separate)"
        ) from exc

    separator = Separator(model_file_dir=str(settings.model_cache_dir / "audio-separator"))
    separator.load_model(model_filename=settings.separation_model_filename)
    logger.info("separator warmup ok: %s", settings.separation_model_filename)


def ensure_semantics() -> "object":
    """Load (downloading once to the model cache) the fx embedding model and
    run a fixed-sentence sanity check. Called only when fx_embeddings is on —
    same contract as the separator: a worker configured for semantics must
    not claim jobs it cannot tag. Returns the ready PrototypeEmbedder so the
    worker reuses the loaded weights."""
    from kashi_server.config import settings
    from kashi_server.pipeline.semantics import get_embedder

    try:
        embedder = get_embedder(cache_dir=str(settings.model_cache_dir))
    except ImportError as exc:  # sentence_transformers imports lazily
        raise RuntimeError(
            "fx_embeddings=true but sentence-transformers is not installed "
            "(image built without --extra semantics)"
        ) from exc
    sim = embedder.smoke()
    logger.info("semantics warmup ok (smoke cosine %.3f)", sim)
    return embedder


def _main() -> int:  # pragma: no cover - CLI
    from kashi_server.config import settings

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    quality = ensure_model()
    if settings.separation_mode != "off":
        ensure_separator()
    if settings.fx_embeddings:
        ensure_semantics()
    print(f"warmup ok (quality {quality:.3f})")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
