"""What is left of the aligner guard (alignment._viterbi) once the job's Viterbi is its own
(`trellis.forced_align` behind `alignment.get_alignments`, since 2026-11-19): what no Viterbi can
do is refused BEFORE it runs (fewer frames than the text needs; non-finite emissions), a path that
does not spell the text (get_spans' AssertionError, or the IndexError of a path that runs out of
tokens) becomes the same classified error, and nothing else is refused - the exact fit runs. The
library is a stand-in here; the compiled library is never called in this process."""

import sys
import types

import numpy as np
import pytest

from kashi_server.pipeline import alignment
from kashi_server.vdl_kit.errors import PipelineError, is_transient_error

LETTERS = "abcdefghijklmnopqrstuvwxyz"
VOCAB = {"<pad>": 0, **{c: i + 1 for i, c in enumerate(LETTERS)}}  # <star> = 27


class Tokenizer:
    def get_vocab(self):
        return dict(VOCAB)


def _alternating(n, repeat_at=None):
    """n targets with no repeated neighbour, or exactly one repeat (R = 1) at `repeat_at`."""
    if repeat_at is None:
        return [1 + (i % 2) for i in range(n)]
    targets = [1 + (i % 2) for i in range(n - 1)]
    targets.insert(repeat_at, targets[repeat_at - 1])
    return targets


# --- the refusal, on every boundary -----------------------------------------------------------


def _lp(T, n_cols=28, nan_at=None, inf_at=None):
    lp = np.full((T, n_cols), -3.0, dtype=np.float32)
    if nan_at is not None:
        lp[nan_at, 2] = np.nan
    if inf_at is not None:
        lp[inf_at, 2] = np.inf
    return lp


@pytest.mark.parametrize("repeat", [False, True])
@pytest.mark.parametrize("slack", [-1, 0, 1])
def test_the_refusal_at_l_plus_r_minus_one_l_plus_r_and_l_plus_r_plus_one(repeat, slack):
    L = 30
    targets = _alternating(L, repeat_at=10 if repeat else None)
    R = 1 if repeat else 0
    why = alignment.viterbi_refusal(_lp(L + R + slack), targets)
    if slack < 0:
        assert why == "the text needs more frames than the audio has"
    else:
        assert why is None  # the exact fit too: its one path is defined


def test_non_finite_emissions_are_refused_and_finite_ones_are_not():
    targets = _alternating(5, repeat_at=2)
    assert alignment.viterbi_refusal(_lp(60), targets) is None
    assert (
        alignment.viterbi_refusal(_lp(60, nan_at=7), targets) == "the emissions are not all finite"
    )
    assert (
        alignment.viterbi_refusal(_lp(60, inf_at=7), targets) == "the emissions are not all finite"
    )


def test_torch_shaped_emissions_are_read_through_the_tensor_methods():
    """What `_numpy_log_probs` does with a torch tensor (any device): detach, cpu, float, numpy."""
    calls = []

    class Tensor:
        def __init__(self, values):
            self.values = values

        def detach(self):
            calls.append("detach")
            return self

        def cpu(self):
            calls.append("cpu")
            return self

        def float(self):
            calls.append("float")
            return self

        def numpy(self):
            calls.append("numpy")
            return self.values

    out = alignment._numpy_log_probs(Tensor(_lp(4, nan_at=1)))
    assert calls == ["detach", "cpu", "float", "numpy"]
    assert out.shape == (4, 28) and alignment.viterbi_refusal(out, [1, 2]) == (
        "the emissions are not all finite"
    )


def test_the_class_is_permanent():
    assert not is_transient_error("alignment_failed")


# --- in _viterbi: refused before the Viterbi, named ----------------------------------------------


def _library(calls, *, spans_assert=False, spans_error=AssertionError):
    """A ctc_forced_aligner stand-in shaped like the real one (words of space-joined letters,
    stars between). Every call is recorded; get_spans can raise as the library does on a path
    that does not spell the text (AssertionError, or IndexError when it runs out of tokens)."""

    def preprocess_text(text, romanize, language, split_size, star_frequency):
        words = text.split()
        tokens = []
        for w in words:
            tokens += ["<star>", " ".join(w)]
        return tokens, [x for w in words for x in ("<star>", w)]

    def get_alignments(emissions, tokens, tokenizer):
        calls.append("get_alignments")
        return [types.SimpleNamespace(label="a", start=0, end=0)], np.zeros(1), "<pad>"

    def get_spans(tokens, segments, blank):
        calls.append("get_spans")
        if spans_assert:
            raise spans_error("n != i")
        return [[types.SimpleNamespace(label="a", start=0, end=0)] for _ in tokens]

    def postprocess_results(text_starred, spans, stride, scores):
        calls.append("postprocess_results")
        return [
            {"text": t, "start": 0.0, "end": 0.02, "score": 0.0}
            for t in text_starred
            if t != "<star>"
        ]

    return types.SimpleNamespace(
        preprocess_text=preprocess_text,
        get_alignments=get_alignments,
        get_spans=get_spans,
        postprocess_results=postprocess_results,
    )


def _install(monkeypatch, fake):
    monkeypatch.setitem(sys.modules, "ctc_forced_aligner", fake)
    monkeypatch.setattr(alignment, "get_alignments", fake.get_alignments)  # the job's Viterbi


def _run_viterbi(monkeypatch, texts, T, **lp_kw):
    calls = []
    _install(monkeypatch, _library(calls))
    try:
        alignment._viterbi(_lp(T, **lp_kw), 20.0, Tokenizer(), texts, "eng", "edges", False)
    except PipelineError as exc:
        return calls, exc
    return calls, None


@pytest.mark.parametrize("text,repeat", [("abcd efgh", False), ("abbd efgh", True)])
@pytest.mark.parametrize("slack", [-1, 0, 1])
def test_viterbi_refuses_only_what_no_path_can_fit(monkeypatch, text, repeat, slack):
    targets = alignment.viterbi_targets(
        ["<star>", " ".join(text.split()[0]), "<star>", " ".join(text.split()[1])], Tokenizer()
    )
    L, R = len(targets), sum(1 for i in range(1, len(targets)) if targets[i] == targets[i - 1])
    assert R == int(repeat)
    calls, error = _run_viterbi(monkeypatch, [text], L + R + slack)
    refused = slack < 0
    assert (error is not None) is refused
    if refused:
        assert calls == []  # the Viterbi was never called
        assert error is not None and error.error_type == "alignment_failed"
        assert f"(T={L + R + slack}, L={L}, R={R})" in error.message
        assert error.message.startswith("whole track, edges pass: ")
    else:
        assert calls == ["get_alignments", "get_spans", "postprocess_results"]


def test_a_nan_frame_is_refused_before_the_viterbi(monkeypatch):
    calls, error = _run_viterbi(monkeypatch, ["abbd efgh"], 200, nan_at=50)
    assert calls == [] and error is not None
    assert "the emissions are not all finite" in error.message


@pytest.mark.parametrize("error", [AssertionError, IndexError])
def test_a_path_that_does_not_spell_the_text_is_the_same_classified_error(monkeypatch, error):
    calls = []
    _install(monkeypatch, _library(calls, spans_assert=True, spans_error=error))
    with pytest.raises(alignment.AlignerRefusal) as raised:
        alignment._viterbi(_lp(200), 20.0, Tokenizer(), ["abbd efgh"], "eng", "segment", False)
    assert raised.value.error_type == "alignment_failed"
    assert "segment pass: the Viterbi path does not spell the text (T=200" in raised.value.message
    assert isinstance(raised.value.__cause__, error)
    assert isinstance(raised.value, PipelineError)  # the job path treats it as before


def test_a_window_names_itself_and_the_label_is_put_back(monkeypatch):
    seen = []

    def hybrid(model, tokenizer, piece, texts, language, **kw):
        seen.append(alignment._VITERBI_WHERE.get())
        if len(seen) == 2:
            raise PipelineError("alignment_failed", "refused")
        return [], []

    monkeypatch.setattr(alignment, "_align_slice_hybrid", hybrid)

    class Audio:
        shape = (10 * 16_000,)

        def __getitem__(self, key):
            return self

    from kashi_server.pipeline.windows import Window

    plan = [Window(2_000, 6_000, [0], 2_350, 5_650), Window(5_000, 9_000, [1], 5_350, 8_650)]
    with pytest.raises(PipelineError):
        alignment._align_windows(
            None, None, Audio(), plan, ["ab", "cd"], "eng", False, star="hybrid"
        )
    assert seen == ["window 0 (samples 32000-96000)", "window 1 (samples 80000-144000)"]
    assert alignment._VITERBI_WHERE.get() == "whole track"


def test_the_guard_s_overrun_bookkeeping_is_gone():
    """Since 2026-11-19 the job never calls the compiled Viterbi: no overrun region to log."""
    for name in ("library_overruns", "_note_overrun", "overruns", "viterbi_song", "_VITERBI_SONG"):
        assert not hasattr(alignment, name), name
