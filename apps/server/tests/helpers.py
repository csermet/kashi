"""Shared test constants/helpers (importable because pytest puts tests/ on
sys.path in prepend import mode — no package prefix needed)."""

TEST_ADMIN_KEY = "ksh_" + "ab" * 16  # 32 hex chars — passes looks_like_key


def auth(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}
