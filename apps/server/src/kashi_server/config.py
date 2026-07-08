"""Runtime configuration from environment variables (pydantic-settings).

Field names map to env vars case-insensitively (database_url <- DATABASE_URL,
the house convention). `schema_path` also accepts KASHI_SCHEMA_PATH, which the
Docker image sets to the baked-in schema copy.
"""

from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings

_REPO_ROOT = Path(__file__).resolve().parents[4]


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://kashi:kashi@localhost:5432/kashi"
    admin_api_key: str | None = None
    data_dir: Path = Path("/scratch")
    model_cache_dir: Path = Path("/models")  # exported as HF_HOME by the worker
    separation_mode: Literal["off", "second_pass", "always"] = "off"
    lrclib_base_url: str = "https://lrclib.net"
    max_track_duration_s: int = 1200
    queue_depth_limit: int = 200
    worker_poll_interval_s: float = 2.0
    retry_delays_s: list[int] = [60, 300, 900]
    lease_ttl_s: int = 600
    bgutil_pot_provider_url: str | None = None
    metrics_port: int = 9090
    schema_path: Path = Field(
        default=_REPO_ROOT / "packages" / "schemas" / "processed-track.v1.schema.json",
        validation_alias=AliasChoices("KASHI_SCHEMA_PATH", "SCHEMA_PATH"),
    )


settings = Settings()
