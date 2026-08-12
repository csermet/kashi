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


@pytest.fixture
def fake_torch_stack(monkeypatch):
    """Stub torch + ctc_forced_aligner and count real loads per model."""
    loads: list[str] = []

    def load_alignment_model(device, model_path, dtype):
        loads.append(model_path)
        return (f"model:{model_path}", f"tok:{model_path}")

    monkeypatch.setitem(sys.modules, "torch", types.SimpleNamespace(float32="f32"))
    monkeypatch.setitem(
        sys.modules,
        "ctc_forced_aligner",
        types.SimpleNamespace(load_alignment_model=load_alignment_model),
    )
    monkeypatch.setattr(alignment, "_loaded", {})
    return loads


def test_two_models_stay_resident(fake_torch_stack):
    alignment._load_model("eng-model")
    alignment._load_model("tur-model")
    alignment._load_model("eng-model")
    alignment._load_model("tur-model")
    assert fake_torch_stack == ["eng-model", "tur-model"]  # no reloads


def test_a_third_model_evicts_the_least_recently_used(fake_torch_stack):
    alignment._load_model("eng-model")
    alignment._load_model("tur-model")
    alignment._load_model("eng-model")  # refresh eng: tur is now LRU
    alignment._load_model("mms-fallback")  # must evict tur, NOT eng
    assert set(alignment._loaded) == {"eng-model", "mms-fallback"}
    alignment._load_model("eng-model")
    assert fake_torch_stack.count("eng-model") == 1  # survived the eviction
    alignment._load_model("tur-model")  # comes back at the cost of a reload
    assert fake_torch_stack.count("tur-model") == 2


def test_a_network_shaped_load_failure_is_transient(fake_torch_stack, monkeypatch):
    def failing_load(device, model_path, dtype):
        raise RuntimeError("Connection reset by peer while downloading model.safetensors")

    monkeypatch.setitem(
        sys.modules,
        "ctc_forced_aligner",
        types.SimpleNamespace(load_alignment_model=failing_load),
    )
    with pytest.raises(PipelineError) as err:
        alignment._load_model("cold-model")
    assert err.value.error_type == "network"  # transient -> retried, no 7-day block
    assert "cold-model" not in alignment._loaded


def test_a_bad_checkpoint_name_stays_a_permanent_failure(fake_torch_stack, monkeypatch):
    """A misspelled checkpoint is not weather — retrying it forever would
    burn attempts on a config error that only an operator can fix."""

    def failing_load(device, model_path, dtype):
        raise OSError("nonexistent/model is not a valid model identifier")

    monkeypatch.setitem(
        sys.modules,
        "ctc_forced_aligner",
        types.SimpleNamespace(load_alignment_model=failing_load),
    )
    with pytest.raises(OSError):
        alignment._load_model("nonexistent/model")
