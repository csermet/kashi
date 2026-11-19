"""Shared test constants/helpers (importable because pytest puts tests/ on
sys.path in prepend import mode — no package prefix needed)."""

import os
from pathlib import Path

TEST_ADMIN_KEY = "ksh_" + "ab" * 16  # 32 hex chars — passes looks_like_key


def auth(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


WORKER_ENV = Path(__file__).resolve().parents[1] / "audit" / "worker.env.example"


def env_file(path: Path = WORKER_ENV) -> dict[str, str]:
    """docker --env-file semantics: KEY=VALUE taken literally, # comments."""
    pairs = {}
    for raw in path.read_text().splitlines():
        if raw.strip() and not raw.lstrip().startswith("#"):
            key, _, value = raw.partition("=")
            pairs[key.strip()] = value
    return pairs


def settings_from(monkeypatch, env: dict[str, str]):
    """A Settings built from exactly `env`: every variable a field could read is
    removed first (pydantic-settings matches names case-insensitively)."""
    from kashi_server.config import Settings

    names = set(Settings.model_fields) | {"kashi_schema_path"}
    for key in list(os.environ):
        if key.lower() in names:
            monkeypatch.delenv(key)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings()


def library_safe(n_frames: int, targets: list[int], log_probs) -> bool:
    """Whether ctc-forced-aligner's COMPILED Viterbi stays inside its buffers on this input, by
    the measured rule (docs/research/refresh-from-evidence-2026-11.md, "The aligner library's
    limits"; conservative: T == L + R + 1 is excluded whatever the path). Every test that calls
    the compiled library goes through it (the rule: tests/test_viterbi_library.py)."""
    import numpy as np

    from kashi_server.pipeline.trellis import library_band

    L = len(targets)
    R = sum(1 for i in range(1, L) if targets[i] == targets[i - 1])
    if L == 0 or n_frames < L + R + 2 or not np.isfinite(log_probs).all():
        return False
    starts, ends = library_band(n_frames, targets)
    band = sum(e - s for s, e in zip(starts, ends, strict=True))
    nbits = (2 * L + 2) * (n_frames - L)
    return band <= 64 * ((nbits + 63) // 64)
