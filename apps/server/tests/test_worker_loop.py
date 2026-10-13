"""The worker loop rides out a lost database instead of restarting (2026-10).

Field: every shared-Postgres restart crash-looped the worker — each restart
reloaded every model, and the outage turned into CrashLoopBackOff noise.
"""

import pytest
from sqlalchemy.exc import OperationalError

from kashi_server.worker.main import DB_RETRY_FIRST_S, DB_RETRY_MAX_S, db_retry_delay_s, serve


def _db_down() -> OperationalError:
    return OperationalError("SELECT 1", {}, Exception("connection refused"))


class Script:
    """An iteration that plays a fixed script of outcomes, then asks to stop."""

    def __init__(self, *outcomes: str) -> None:
        self.outcomes = list(outcomes)
        self.calls = 0
        self.slept: list[float] = []

    def iteration(self) -> None:
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if outcome == "db_down":
            raise _db_down()
        if outcome == "bug":
            raise RuntimeError("a real bug")

    def should_stop(self) -> bool:
        return not self.outcomes

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)


def test_backoff_doubles_from_the_first_wait_and_caps_at_a_minute():
    assert db_retry_delay_s(1) == DB_RETRY_FIRST_S
    assert db_retry_delay_s(2) == 2 * DB_RETRY_FIRST_S
    assert db_retry_delay_s(6) == 32.0
    assert db_retry_delay_s(7) == DB_RETRY_MAX_S  # 64 s, capped
    assert db_retry_delay_s(40) == DB_RETRY_MAX_S


def test_a_lost_database_is_waited_out_not_raised():
    script = Script("db_down", "db_down", "ok")
    serve(script.iteration, script.should_stop, script.sleep)
    assert script.calls == 3
    assert sum(script.slept) == 1.0 + 2.0


def test_a_success_resets_the_backoff():
    script = Script("db_down", "ok", "db_down", "ok")
    serve(script.iteration, script.should_stop, script.sleep)
    assert sum(script.slept) == 1.0 + 1.0


def test_waits_in_short_slices_so_a_stop_is_not_held_up():
    script = Script(*["db_down"] * 7, "ok")
    serve(script.iteration, script.should_stop, script.sleep)
    assert max(script.slept) <= 1.0
    assert sum(script.slept) == 1 + 2 + 4 + 8 + 16 + 32 + 60


def test_a_stop_during_the_wait_ends_the_loop(monkeypatch):
    from kashi_server.worker import main

    monkeypatch.setattr(main, "DB_RETRY_FIRST_S", 5.0)  # a wait of several slices
    stopping = {"flag": False}
    calls = []
    slept = []

    def iteration() -> None:
        calls.append(1)
        raise _db_down()

    def sleep(seconds: float) -> None:
        slept.append(seconds)
        stopping["flag"] = True  # SIGTERM arrives mid-wait

    serve(iteration, lambda: stopping["flag"], sleep)
    assert calls == [1]
    assert slept == [1.0]  # the rest of the wait is skipped


def test_anything_else_still_ends_the_process():
    script = Script("bug", "ok")
    with pytest.raises(RuntimeError):
        serve(script.iteration, script.should_stop, script.sleep)
    assert script.slept == []
