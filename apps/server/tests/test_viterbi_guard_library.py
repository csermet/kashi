"""The aligner guard against the REAL compiled library (slow: needs the align extra; skipped
without it): on every boundary of `alignment.viterbi_refusal` (T = L + R - 1, L + R, L + R + 1,
with and without a repeated letter) and a NaN frame, through `alignment._viterbi` with synthetic
log-probabilities and a small vocabulary. A refused input never reaches the library's
`forced_align` (a spy says so): the exact fit without a repeat, where the unguarded library
segfaults, is never executed. An accepted input is aligned, and the targets the library builds
are `alignment.viterbi_targets`'. Re-run on every bump of the library's pin:

    uv run pytest -m slow tests/test_viterbi_guard_library.py
"""

import pytest

pytest.importorskip("ctc_forced_aligner", reason="alignment extra not installed")
torch = pytest.importorskip("torch")

import numpy as np  # noqa: E402
from ctc_forced_aligner import (  # noqa: E402  # pyright: ignore[reportMissingImports]
    alignment_utils,
    preprocess_text,
)

from kashi_server.pipeline import alignment  # noqa: E402

pytestmark = pytest.mark.slow

LETTERS = "abcdefghijklmnopqrstuvwxyz"
VOCAB = {"<pad>": 0, **{c: i + 1 for i, c in enumerate(LETTERS)}}


class Tokenizer:
    pad_token_id = 0
    vocab_size = len(VOCAB)

    def get_vocab(self):
        return dict(VOCAB)


@pytest.fixture
def library_targets(monkeypatch):
    """Every call that reaches the library's compiled Viterbi, with the targets it was given."""
    seen = []
    real = alignment_utils.forced_align

    def spy(log_probs, targets, *a, **k):
        seen.append([int(t) for t in np.asarray(targets).reshape(-1)])
        return real(log_probs, targets, *a, **k)

    monkeypatch.setattr(alignment_utils, "forced_align", spy)
    return seen


def _emissions(T, nan=False):
    rng = np.random.default_rng(7)
    logits = rng.normal(0, 3, size=(T, len(VOCAB))).astype(np.float32)
    lp = logits - np.log(np.exp(logits).sum(1, keepdims=True))
    if nan:
        lp[T // 2, 3] = np.nan
    star = np.zeros((T, 1), np.float32)
    return torch.from_numpy(np.concatenate([lp, star], 1).astype(np.float32))


def _sizes(text):
    tokens, _ = preprocess_text(
        text, romanize=False, language="eng", split_size="word", star_frequency="segment"
    )
    targets = alignment.viterbi_targets(tokens, Tokenizer())
    repeats = sum(1 for i in range(1, len(targets)) if targets[i] == targets[i - 1])
    return targets, len(targets), repeats


@pytest.mark.parametrize("text", ["abcd efgh ijkl", "abbd efgh ijkl"])
@pytest.mark.parametrize("slack", [-1, 0, 1])
def test_every_boundary_on_the_compiled_library(library_targets, text, slack):
    targets, L, R = _sizes(text)
    T = L + R + slack
    refused = slack < 0 or (slack == 0 and R == 0)
    if refused:
        with pytest.raises(alignment.AlignerRefusal) as raised:
            alignment._viterbi(_emissions(T), 20.0, Tokenizer(), [text], "eng", "segment", False)
        assert f"(T={T}, L={L}, R={R})" in raised.value.message
        assert library_targets == []  # refused before the library: no RuntimeError, no segfault
    else:
        rows = alignment._viterbi(_emissions(T), 20.0, Tokenizer(), [text], "eng", "segment", False)
        assert [r["text"] for r in rows] == text.split()
        assert library_targets == [targets]  # the library's own targets are viterbi_targets'


def test_a_nan_frame_never_reaches_the_compiled_library(library_targets):
    text = "abbd efgh ijkl"
    targets, L, R = _sizes(text)
    with pytest.raises(alignment.AlignerRefusal, match="not all finite"):
        alignment._viterbi(
            _emissions(3 * L, nan=True), 20.0, Tokenizer(), [text], "eng", "segment", False
        )
    assert library_targets == []


def test_the_torch_tensor_is_read_as_the_library_reads_it():
    em = _emissions(12)
    assert np.array_equal(alignment._numpy_log_probs(em), em.numpy())
