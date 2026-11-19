"""The job's Viterbi (`trellis.forced_align` behind `alignment.get_alignments`, since
2026-11-19) against the REAL compiled one (ctc-forced-aligner's forced_align_impl.cpp; slow:
needs the align extra, skipped without it). Re-run on every bump of the library's pin:

    uv run pytest -m slow tests/test_viterbi_library.py

THE RULE OF THIS MODULE (and of every test that calls the compiled library): never execute it
inside its unsafe set. Measured with a bounds-checked port and AddressSanitizer (docs/research/
refresh-from-evidence-2026-11.md, "The aligner library's limits"), the compiled Viterbi reads or
writes outside its buffers when
  - T == L + R (always; with R == 0 it has no back-pointer storage at all),
  - T == L + R + 1 and the path starts with a blank,
  - R == 0 and its band claims more back-pointer bits than its buffers' 64-bit words hold
    (2L - 2 - T > (-(2L + 2)(T - L)) mod 64),
and what that does depends on the C++ library it was built against: with GCC 15's libstdc++ the
first two read ~2^61 bytes away and the process dies (the CI runner, 2026-11-16/17); with GCC 13
and 14 they read the 8 bytes before a heap block and pass. "It passed here" proves nothing there.
`library_safe` below admits only T >= L + R + 2 with a band the words hold, and finite scores
(a non-finite input is refused before any Viterbi anyway); every call here goes through it.
"""

import pytest

pytest.importorskip("ctc_forced_aligner", reason="alignment extra not installed")

import numpy as np  # noqa: E402
from ctc_forced_aligner import (
    alignment_utils,  # noqa: E402  # pyright: ignore[reportMissingImports]
)
from ctc_forced_aligner.ctc_forced_aligner import (  # noqa: E402  # pyright: ignore[reportMissingImports]
    forced_align as compiled,
)

from kashi_server.pipeline import alignment  # noqa: E402
from kashi_server.pipeline.trellis import forced_align, library_band  # noqa: E402

pytestmark = pytest.mark.slow


def library_safe(n_frames: int, targets: list[int], log_probs) -> bool:
    """Whether the compiled library stays inside its buffers on this input, by the measured rule
    (conservative: T == L + R + 1 is excluded whatever the path)."""
    L = len(targets)
    R = sum(1 for i in range(1, L) if targets[i] == targets[i - 1])
    if L == 0 or n_frames < L + R + 2 or not np.isfinite(log_probs).all():
        return False
    starts, ends = library_band(n_frames, targets)
    band = sum(e - s for s, e in zip(starts, ends, strict=True))
    nbits = (2 * L + 2) * (n_frames - L)
    return band <= 64 * ((nbits + 63) // 64)


def _run_compiled(log_probs, targets, blank=0):
    assert library_safe(log_probs.shape[0], targets, log_probs), "never run it unsafe"
    path, scores = compiled(log_probs[None], np.asarray([targets], dtype=np.int64), blank)
    return path[0], scores


def _case(rng, L, T, V, repeat_rate, kind):
    tg = [int(rng.integers(1, V))]
    for _ in range(L - 1):
        if rng.random() < repeat_rate:
            tg.append(tg[-1])
        else:
            tg.append(int(rng.choice([v for v in range(1, V) if v != tg[-1]])))
    if kind == "half-steps":  # rounded logits: exact ties after the float32 sums
        x = np.round(rng.normal(size=(T, V)) * 2) / 2
    elif kind == "ties":
        x = np.zeros((T, V))
    else:
        x = rng.normal(0, 3, size=(T, V))
    lp = (x - np.log(np.exp(x).sum(1, keepdims=True))).astype(np.float32)
    return lp, tg


def test_the_rule_admits_nothing_from_the_unsafe_set():
    lp = np.zeros((400, 5), np.float32)
    for L in range(1, 120):
        tg = [1 + (i % 2) for i in range(L)]
        for T in range(L, L + 2):
            assert not library_safe(T, tg, lp[:T])
        for T in range(L + 2, 2 * L - 2):
            nbits = (2 * L + 2) * (T - L)
            spill = 2 * L - 2 - T > (-nbits) % 64
            assert library_safe(T, tg, lp[:T]) is (not spill)
    nan = np.zeros((40, 5), np.float32)
    nan[3, 1] = np.nan
    assert not library_safe(40, [1, 2, 3], nan)


@pytest.mark.parametrize("kind", ["random", "half-steps", "ties"])
def test_every_safe_input_gets_the_compiled_path_and_scores(kind):
    rng = np.random.default_rng({"random": 1, "half-steps": 2, "ties": 3}[kind])
    checked = 0
    for _ in range(300):
        L = int(rng.integers(1, 60))
        repeat_rate = float(rng.choice([0.0, 0.05, 0.3]))
        T = int(rng.integers(L, 3 * L + 40))
        lp, tg = _case(rng, L, T, 12, repeat_rate, kind)
        if not library_safe(T, tg, lp):
            continue
        path, scores = _run_compiled(lp, tg)
        ours = forced_align(lp, tg, 0)
        assert ours[0].tolist() == path.tolist(), (L, T)
        assert ours[1].tobytes() == scores.tobytes(), (L, T)
        checked += 1
    assert checked > 150


@pytest.mark.parametrize("L", [17, 33, 100])
def test_the_boundaries_of_the_safe_set(L):
    """T == L + R + 2, the first T without a repeat whose band the words hold, and 2L - 2."""
    rng = np.random.default_rng(L)
    for repeat_rate in (0.0, 0.3):
        lp, tg = _case(rng, L, 1, 12, repeat_rate, "random")
        R = sum(1 for i in range(1, L) if tg[i] == tg[i - 1])
        first = next(T for T in range(L + R + 2, 3 * L) if library_safe(T, tg, lp[:1].repeat(T, 0)))
        for T in sorted({L + R + 2, first, 2 * L - 2, 2 * L - 1}):
            lp, _ = _case(rng, L, T, 12, 0.0, "half-steps")
            if not library_safe(T, tg, lp):
                continue
            path, scores = _run_compiled(lp, tg)
            ours = forced_align(lp, tg, 0)
            assert ours[0].tolist() == path.tolist() and ours[1].tobytes() == scores.tobytes()


def test_a_whole_track_pass():
    rng = np.random.default_rng(10)
    lp, tg = _case(rng, 1500, 5000, 40, 0.03, "random")
    path, scores = _run_compiled(lp, tg)
    ours = forced_align(lp, tg, 0)
    assert ours[0].tolist() == path.tolist() and ours[1].tobytes() == scores.tobytes()


class Tokenizer:
    pad_token_id = 0
    vocab_size = 27

    def get_vocab(self):
        return {"<pad>": 0, **{c: i + 1 for i, c in enumerate("abcdefghijklmnopqrstuvwxyz")}}


def test_get_alignments_is_the_library_s_on_safe_input():
    """alignment.get_alignments against the library's own get_alignments (which runs the compiled
    Viterbi): the same segments, scores and blank."""
    import torch

    rng = np.random.default_rng(11)
    tokens = ["<star>", "a b b d", "<star>", "e f g h"]
    x = rng.normal(0, 3, size=(60, 28))
    lp = (x - np.log(np.exp(x).sum(1, keepdims=True))).astype(np.float32)
    targets = alignment.viterbi_targets(tokens, Tokenizer())
    assert library_safe(60, targets, lp)
    theirs = alignment_utils.get_alignments(torch.from_numpy(lp), tokens, Tokenizer())
    ours = alignment.get_alignments(torch.from_numpy(lp), tokens, Tokenizer())
    assert [(s.label, s.start, s.end) for s in ours[0]] == [
        (s.label, s.start, s.end) for s in theirs[0]
    ]
    assert ours[1].tobytes() == np.asarray(theirs[1]).tobytes() and ours[2] == theirs[2]
