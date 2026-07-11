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
    separation_mode: Literal["off", "second_pass", "always"] = "off"
    # lrclib-anchored windowed alignment (P3). Ships dark: flips to True in
    # the 2.0.0 rollout together with the separation default.
    windowed_alignment: bool = False
    # audio-separator registry filename. BS-RoFormer: best measured vocal SDR of
    # the CPU-viable models (hizalama-v2 research); Voc_FT is the fallback if
    # its wall-clock blows up on the worker (see benchmarks/).
    separation_model_filename: str = "model_bs_roformer_ep_317_sdr_12.9755.ckpt"
    # Fraction of the ORIGINAL mix folded back into the vocal stem before
    # alignment — insurance against separation artefacts eating quiet words.
    # 0 disables the mixback pass.
    separation_mixback: float = 0.15
    lrclib_base_url: str = "https://lrclib.net"
    max_track_duration_s: int = 1200
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
