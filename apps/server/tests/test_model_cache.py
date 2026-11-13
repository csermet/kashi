"""The alignment model cache is BOUNDED (2026-08-12 audit).

Weights never unloaded themselves; a mixed-language day could hold three
checkpoints plus the separation peak against a 12 Gi limit. Two stay resident
(the routed EN+TR pair); a third arrival evicts the least recently used.
And a download hiccup on a cold model must RETRY, not permanent-fail the
song for seven days.
"""

import sys
import types

import pytest

from kashi_server.pipeline import alignment
from kashi_server.vdl_kit.errors import PipelineError


class _FakeModel:
    def __init__(self, name: str):
        self.name = name

    def to(self, device):
        return self

    def eval(self):
        return self


def _transformers(calls: list, model_load=None):
    """A stand-in for the two `from_pretrained` calls the loader makes."""

    def load_model(name, **kwargs):
        calls.append(("model", name, kwargs))
        if model_load is not None:
            model_load(name)
        return _FakeModel(name)

    def load_tokenizer(name, **kwargs):
        calls.append(("tokenizer", name, kwargs))
        return f"tok:{name}"

    return types.SimpleNamespace(
        AutoModelForCTC=types.SimpleNamespace(from_pretrained=load_model),
        AutoTokenizer=types.SimpleNamespace(from_pretrained=load_tokenizer),
    )


@pytest.fixture
def load_calls(monkeypatch):
    """Stub torch + transformers; every from_pretrained call is recorded."""
    calls: list[tuple] = []
    monkeypatch.setitem(sys.modules, "torch", types.SimpleNamespace(float32="f32"))
    monkeypatch.setitem(sys.modules, "transformers", _transformers(calls))
    monkeypatch.setattr(alignment, "_loaded", {})
    return calls


def _model_loads(calls: list) -> list[str]:
    """The real loads, in order (one model call per load)."""
    return [name for kind, name, _ in calls if kind == "model"]


def test_two_models_stay_resident(load_calls):
    alignment._load_model("eng-model")
    alignment._load_model("tur-model")
    alignment._load_model("eng-model")
    alignment._load_model("tur-model")
    assert _model_loads(load_calls) == ["eng-model", "tur-model"]  # no reloads


def test_a_third_model_evicts_the_least_recently_used(load_calls):
    alignment._load_model("eng-model")
    alignment._load_model("tur-model")
    alignment._load_model("eng-model")  # refresh eng: tur is now LRU
    alignment._load_model("mms-fallback")  # must evict tur, NOT eng
    assert set(alignment._loaded) == {"eng-model", "mms-fallback"}
    alignment._load_model("eng-model")
    assert _model_loads(load_calls).count("eng-model") == 1  # survived the eviction
    alignment._load_model("tur-model")  # comes back at the cost of a reload
    assert _model_loads(load_calls).count("tur-model") == 2


# --- 2.33.0: the weights come from a pinned hub revision ----------------------


def test_a_pinned_checkpoint_loads_its_revision_and_an_unlisted_one_loads_main(
        load_calls, monkeypatch):
    """Both from_pretrained calls carry the pin; an unpinned load is the
    library's own call, argument for argument (no revision at all)."""
    from kashi_server.config import settings

    monkeypatch.setattr(settings, "align_model_revisions", {"eng-model": "0123abc"})
    alignment._load_model("eng-model")
    alignment._load_model("bakeoff/model")
    assert load_calls == [
        ("model", "eng-model", {"dtype": "f32", "revision": "0123abc"}),
        ("tokenizer", "eng-model", {"word_delimiter_token": None, "revision": "0123abc"}),
        ("model", "bakeoff/model", {"dtype": "f32"}),
        ("tokenizer", "bakeoff/model", {"word_delimiter_token": None}),
    ]


def test_every_shipped_checkpoint_is_pinned_to_a_commit():
    import re

    from kashi_server.config import PRODUCTION_ALIGN_MODELS, Settings

    def default(name):
        return Settings.model_fields[name].get_default(call_default_factory=True)

    pins = default("align_model_revisions")
    shipped = {choice["checkpoint"] for choice in PRODUCTION_ALIGN_MODELS.values()}
    shipped.add(default("align_model"))  # the fallback for every other language
    assert shipped <= set(pins)
    assert all(re.fullmatch(r"[0-9a-f]{40}", pins[name]) for name in shipped)


def test_the_pin_table_is_settable_from_the_env(monkeypatch):
    from helpers import settings_from

    unpinned = settings_from(monkeypatch, {"ALIGN_MODEL_REVISIONS": '{"x/y": "main"}'})
    assert unpinned.align_model_revisions == {"x/y": "main"}


def test_a_network_shaped_load_failure_is_transient(load_calls, monkeypatch):
    def failing_load(name):
        raise RuntimeError("Connection reset by peer while downloading model.safetensors")

    monkeypatch.setitem(sys.modules, "transformers", _transformers([], failing_load))
    with pytest.raises(PipelineError) as err:
        alignment._load_model("cold-model")
    assert err.value.error_type == "network"  # transient -> retried, no 7-day block
    assert "cold-model" not in alignment._loaded


def test_a_bad_checkpoint_name_stays_a_permanent_failure(load_calls, monkeypatch):
    """A misspelled checkpoint is not weather — retrying it forever would
    burn attempts on a config error that only an operator can fix."""

    def failing_load(name):
        raise OSError("nonexistent/model is not a valid model identifier")

    monkeypatch.setitem(sys.modules, "transformers", _transformers([], failing_load))
    with pytest.raises(OSError):
        alignment._load_model("nonexistent/model")
