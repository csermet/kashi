"""The aligner guard (alignment._viterbi): what ctc-forced-aligner's compiled Viterbi cannot do
at all is refused BEFORE the library is called (fewer frames than the text needs; the exact fit
without a repeated letter, where it segfaults; non-finite emissions), a path that does not spell
the text (get_spans' AssertionError, or the IndexError of a path that runs out of tokens) becomes
the same classified error, and a pass in the wider region where the library writes past its
buffers is logged with its song and collected, not refused. The library is a stand-in here (the
real one: tests/test_viterbi_guard_library.py, slow); no segfaulting call runs in this process."""

import random
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


def _brute_overruns(targets, T):
    """forced_align_impl.cpp's band and back-pointer writes, index by index (the slow way)."""
    L = len(targets)
    S = 2 * L + 1
    R = sum(1 for i in range(1, L) if targets[i] == targets[i - 1])
    size = (S + 1) * (T - L)
    start = 0 if T - (L + R) > 0 else 1
    end = 1 if S == 1 else 2
    seek = 0
    written = []
    for t in range(1, T):
        if T - t <= L + R:
            after = targets[start // 2 + 1] if start // 2 + 1 < L else None
            if start % 2 == 1 and targets[start // 2] != after:
                start += 1
            start += 1
        if t <= L + R:
            if end % 2 == 0 and end < 2 * L and targets[end // 2 - 1] != targets[end // 2]:
                end += 1
            end += 1
        first = start
        if start == 0:
            first += 1
            seek += 1
        for i in range(first, end):
            written.append(seek + i - first)
        seek += end - first
    return any(index >= size for index in written)


def _alternating(n, repeat_at=None):
    """n targets with no repeated neighbour, or exactly one repeat (R = 1) at `repeat_at`."""
    if repeat_at is None:
        return [1 + (i % 2) for i in range(n)]
    targets = [1 + (i % 2) for i in range(n - 1)]
    targets.insert(repeat_at, targets[repeat_at - 1])
    return targets


# --- the buffer bookkeeping ---------------------------------------------------------------------


def test_the_one_pass_bookkeeping_equals_the_index_by_index_one():
    rng = random.Random(611)
    for _ in range(600):
        n = rng.randint(1, 40)
        targets = [rng.choice([1, 2, 3]) if rng.random() < 0.3 else 1 + (i % 2) for i in range(n)]
        R = sum(1 for i in range(1, n) if targets[i] == targets[i - 1])
        for T in {n + R, n + R + 1, 2 * n - 3, 2 * n - 2, 2 * n, 3 * n} - {
            x for x in range(0, n + R)
        }:
            assert alignment.library_overruns(T, targets) == _brute_overruns(targets, T), (
                targets,
                T,
            )


@pytest.mark.parametrize("L", [3, 4, 10, 57, 300])
def test_without_a_repeat_the_buffers_hold_from_two_frames_a_target_less_two(L):
    targets = _alternating(L)
    assert alignment.library_overruns(2 * L - 3, targets) is True
    assert alignment.library_overruns(2 * L - 2, targets) is False
    assert alignment.library_overruns(2 * L - 1, targets) is False
    assert alignment.library_overruns(L, targets) is True  # the exact fit that segfaults


@pytest.mark.parametrize("L", [3, 10, 57, 300])
def test_one_repeat_keeps_the_buffers_whole_down_to_the_exact_fit(L):
    targets = _alternating(L, repeat_at=L // 2)
    assert not any(alignment.library_overruns(T, targets) for T in range(L + 1, 3 * L))


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
    elif slack == 0 and not repeat:
        assert (
            why == "the text fills the audio exactly with no repeated letter (the library crashes)"
        )
    else:
        assert why is None  # the library completes these today


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


# --- in _viterbi: refused before the library, named --------------------------------------------


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


def _run_viterbi(monkeypatch, texts, T, **lp_kw):
    calls = []
    monkeypatch.setitem(sys.modules, "ctc_forced_aligner", _library(calls))
    monkeypatch.setattr(alignment.logger, "disabled", False)  # alembic's fileConfig disables it
    try:
        alignment._viterbi(_lp(T, **lp_kw), 20.0, Tokenizer(), texts, "eng", "edges", False)
    except PipelineError as exc:
        return calls, exc
    return calls, None


@pytest.mark.parametrize("text,repeat", [("abcd efgh", False), ("abbd efgh", True)])
@pytest.mark.parametrize("slack", [-1, 0, 1])
def test_viterbi_refuses_before_the_library_or_calls_it(monkeypatch, caplog, text, repeat, slack):
    targets = alignment.viterbi_targets(
        ["<star>", " ".join(text.split()[0]), "<star>", " ".join(text.split()[1])], Tokenizer()
    )
    L, R = len(targets), sum(1 for i in range(1, len(targets)) if targets[i] == targets[i - 1])
    assert R == int(repeat)
    with caplog.at_level("WARNING", logger=alignment.logger.name):
        calls, error = _run_viterbi(monkeypatch, [text], L + R + slack)
    refused = slack < 0 or (slack == 0 and not repeat)
    assert (error is not None) is refused
    overrun = not repeat and slack > 0  # L + 1 < 2L - 2: logged, not refused
    assert ("writes past its buffers" in caplog.text) is overrun
    if refused:
        assert calls == []  # the library was never called: no segfault can happen
        assert error is not None and error.error_type == "alignment_failed"
        assert f"(T={L + R + slack}, L={L}, R={R})" in error.message
        assert error.message.startswith("whole track, edges pass: ")
    else:
        assert calls == ["get_alignments", "get_spans", "postprocess_results"]


def test_the_overrun_region_is_logged_and_the_library_still_runs(monkeypatch, caplog):
    """k89YEt8hocI's window 21 (a pilot song, segment pass): T = 286, L = 147, R = 0 overruns the
    library's buffers by its own bookkeeping, and main stores the document it made. Logged only."""
    targets = _alternating(147)
    assert alignment.library_overruns(286, targets)
    assert alignment.viterbi_refusal(_lp(286), targets) is None
    text = "abcd efgh"  # 10 targets, no repeat: T = 15 is inside 2L - 2 = 18
    with caplog.at_level("WARNING", logger=alignment.logger.name):
        calls, error = _run_viterbi(monkeypatch, [text], 15)
    assert error is None and calls[0] == "get_alignments"
    assert (
        "whole track, edges pass: the aligner library writes past its buffers here" in caplog.text
    )
    assert "(T=15, L=10, R=0)" in caplog.text
    caplog.clear()
    with caplog.at_level("WARNING", logger=alignment.logger.name):
        _run_viterbi(monkeypatch, [text], 18)  # 2L - 2: inside the buffers
    assert "writes past its buffers" not in caplog.text


def test_a_nan_frame_is_refused_before_the_library(monkeypatch):
    calls, error = _run_viterbi(monkeypatch, ["abbd efgh"], 200, nan_at=50)
    assert calls == [] and error is not None
    assert "the emissions are not all finite" in error.message


@pytest.mark.parametrize("error", [AssertionError, IndexError])
def test_a_path_that_does_not_spell_the_text_is_the_same_classified_error(monkeypatch, error):
    calls = []
    monkeypatch.setitem(
        sys.modules, "ctc_forced_aligner", _library(calls, spans_assert=True, spans_error=error)
    )
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


def test_the_overrun_line_names_the_song_and_the_real_repeat_count(monkeypatch, caplog):
    """The log line carries the song (and job) the job code names, and R as counted: the
    bookkeeping does not itself require R == 0, so neither may the line."""
    monkeypatch.setattr(alignment, "library_overruns", lambda n_frames, targets: True)
    with (
        caplog.at_level("WARNING", logger=alignment.logger.name),
        alignment.viterbi_song("youtube:abc (job 42)"),
        alignment.overruns() as hits,
    ):
        _run_viterbi(monkeypatch, ["abbd efgh"], 40)
    assert "youtube:abc (job 42): whole track, edges pass: the aligner library writes past" in (
        caplog.text
    )
    assert "(T=40, L=10, R=1)" in caplog.text
    assert hits == [{"where": "whole track", "pass": "edges", "T": 40, "L": 10, "R": 1}]
    assert alignment._VITERBI_SONG.get() == ""  # put back


def test_without_a_sink_nothing_is_collected_and_with_one_only_overruns(monkeypatch):
    with alignment.overruns() as hits:
        _run_viterbi(monkeypatch, ["abbd efgh"], 40)  # R = 1: inside its buffers
    assert hits == []
    _run_viterbi(monkeypatch, ["abcd efgh"], 15)  # an overrun, no sink open: nothing breaks
    assert alignment._OVERRUN_SINK.get() is None
