"""Runtime configuration from environment variables (pydantic-settings).

Field names map to env vars case-insensitively (database_url <- DATABASE_URL,
the house convention). `schema_path` also accepts KASHI_SCHEMA_PATH, which the
Docker image sets to the baked-in schema copy.
"""

from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings


def _default_schema_path() -> Path:
    """Walk up towards a repo checkout; fall back to the image's baked-in copy.
    A fixed parents[N] index crashed at import inside the container, where the
    module sits only 3 levels deep (/app/src/kashi_server/config.py)."""
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "packages" / "schemas" / "processed-track.v1.schema.json"
        if candidate.exists():
            return candidate
    return Path("/app/schemas/processed-track.v1.schema.json")


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://kashi:kashi@localhost:5432/kashi"
    admin_api_key: str | None = None
    data_dir: Path = Path("/scratch")
    model_cache_dir: Path = Path("/models")  # exported as HF_HOME by the worker
    # Pipeline 2.0.0 defaults — every one of these is MEASURED, not assumed
    # (9-config / 79-song matrix + field cases, 2026-07-11/12; see
    # docs/research/hizalama-v2-benchmark-2026-07.md).
    separation_mode: Literal["off", "second_pass", "always"] = "always"
    # lrclib-anchored windowed alignment (P3): a CTC lock loss cannot
    # propagate past a window edge (the dominant field failure mode).
    windowed_alignment: bool = True
    # audio-separator registry filename. Kim MelBand: best measured PCO/MAE of
    # all candidates at ~2.1x realtime on the worker (BS-RoFormer quality at a
    # third of its cost); higher-SDR models measured WORSE for alignment.
    separation_model_filename: str = "mel_band_roformer_kim_ft_unwa.ckpt"
    # Fraction of the ORIGINAL mix folded back into the vocal stem. Measured
    # HARMFUL on average (dilutes the clean-vocal advantage; ~10x MedAE) —
    # kept only as an escape hatch. 0 disables the pass.
    separation_mixback: float = 0.0
    # Nightcore auto-detection (Faz 4): titles carrying nightcore/sped-up
    # markers trigger a speed-factor probe against lrclib. Explicit ingest
    # options bypass this switch.
    nightcore_detection: bool = True
    lrclib_base_url: str = "https://lrclib.net"
    max_track_duration_s: int = 1200
    # BYO-audio staging (Faz 5 P4): multipart cap enforced while streaming
    # (64 MB covers ~1h of 128kbps audio, far past the duration cap anyway);
    # orphaned rows — job never ran or was canceled — are swept after the TTL.
    upload_max_bytes: int = 64 * 1024 * 1024
    upload_ttl_hours: int = 24
    # lrclib contribute-back (Faz 5 P6): BOTH flags must be flipped for a
    # real publish — the feature defaults hard-off, and even when enabled,
    # dry-run only LOGS the YAML until the operator is sure.
    lrclib_publish_enabled: bool = False
    lrclib_publish_dry_run: bool = True
    # FX embedding layer (Faz 6 P3, `semantics` extra): line-theme tagging
    # via multilingual-e5-small. The keyword/stem layer is dependency-free
    # and ALWAYS runs; this flag only gates the model. Self-hosters without
    # the extra set it false (warmup gates on it like the separator).
    fx_embeddings: bool = True
    queue_depth_limit: int = 200
    worker_poll_interval_s: float = 2.0
    retry_delays_s: list[int] = [60, 300, 900]
    lease_ttl_s: int = 600
    bgutil_pot_provider_url: str | None = None
    metrics_port: int = 9090
    schema_path: Path = Field(
        default_factory=_default_schema_path,
        validation_alias=AliasChoices("KASHI_SCHEMA_PATH", "SCHEMA_PATH"),
    )


settings = Settings()
