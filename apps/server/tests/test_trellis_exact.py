"""The job's own Viterbi (`trellis.forced_align`, since 2026-11-19) against what
ctc-forced-aligner's compiled one returns, without the library: golden answers it gave on inputs
inside its safe set (every family that matters: exact ties, -inf columns, inputs with no finite
path at all, where the library's flat back-pointer reads decide the path), and its band, sizes and
defined behaviour where the library leaves its buffers (the exact fit, a path that starts with a
blank at T == L + R + 1, a band larger than the buffers' words). The real library: the slow
`tests/test_viterbi_library.py`; the unsafe set's ground truth: docs/research/
refresh-from-evidence-2026-11.md, "The aligner library's limits"."""

import json
import sys
import types
from pathlib import Path

import numpy as np
import pytest

from kashi_server.pipeline import alignment
from kashi_server.pipeline.trellis import forced_align, forced_align_cut, library_band

GOLDEN = Path(__file__).parent / "fixtures" / "viterbi_library_golden.json"


def _golden():
    data = json.loads(GOLDEN.read_text())
    for case in data["cases"]:
        lp = np.array(
            [[-np.inf if v is None else v for v in row] for row in case["log_probs"]],
            dtype=np.float32,
        )
        scores = np.array([-np.inf if v is None else v for v in case["scores"]], dtype=np.float32)
        yield lp, case["targets"], case["blank"], case["path"], scores


def test_the_compiled_library_s_own_answers():
    cases = list(_golden())
    assert len(cases) >= 60
    for lp, targets, blank, path, scores in cases:
        ours_path, ours_scores = forced_align(lp, targets, blank)
        assert ours_path.tolist() == path, (targets, lp.shape)
        assert ours_scores.dtype == np.float32
        assert ours_scores.tobytes() == scores.tobytes()


def test_the_golden_file_covers_ties_neg_inf_and_inputs_without_a_finite_path():
    cases = list(_golden())
    assert any(not np.isfinite(sc).all() for *_, sc in cases)  # no finite path
    assert any(not np.isfinite(lp).all() and np.isfinite(sc).all() for lp, *_, sc in cases)
    assert any((lp == 0).all() for lp, *_ in cases)  # every value tied
    assert any(blank != 0 for _, _, blank, _, _ in cases)  # a blank other than column 0


def _random(T, L, V=8, seed=0, repeat_rate=0.0):
    rng = np.random.default_rng(seed)
    tg = [int(rng.integers(1, V))]
    for _ in range(L - 1):
        tg.append(
            tg[-1]
            if rng.random() < repeat_rate
            else int(rng.choice([v for v in range(1, V) if v != tg[-1]]))
        )
    x = rng.normal(0, 3, size=(T, V))
    lp = (x - np.log(np.exp(x).sum(1, keepdims=True))).astype(np.float32)
    return lp, tg


@pytest.mark.parametrize("seed", range(20))
def test_on_finite_scores_the_path_is_the_trellis_s_best(seed):
    """Finite scores: the path never leaves the band, so the cut-free trellis (a 2-D table) and
    the flat layout agree - here inside the library's unsafe set too (T from L + R on)."""
    rng = np.random.default_rng(seed)
    L = int(rng.integers(1, 30))
    lp, tg = _random(1, L, seed=seed, repeat_rate=0.2)
    # Any column may be the blank: swap it with 0 in the targets (repeats stay where they were).
    blank = int(rng.integers(0, 8))
    tg = [{0: blank, blank: 0}.get(t, t) for t in tg]
    R = sum(1 for i in range(1, L) if tg[i] == tg[i - 1])
    for T in (L + R, L + R + 1, L + R + 2, 2 * L - 3, 2 * L, 3 * L):
        if T < L + R or T < 1:
            continue
        lp, _ = _random(T, L, seed=seed + 100)
        path, scores = forced_align(lp, tg, blank)
        cut = forced_align_cut(lp, tg, blank, [])
        assert cut is not None
        assert path.tolist() == cut[0].tolist() and scores.tobytes() == cut[1].tobytes()


@pytest.mark.parametrize("L", [1, 2, 3, 7, 40])
def test_the_exact_fit_without_a_repeat_has_its_one_path(L):
    """T == L, R == 0: the library has no back-pointer storage and writes through a null pointer
    (it segfaults on every build). The only path: every target for one frame."""
    lp, tg = _random(L, L, seed=L)
    path, scores = forced_align(lp, tg, 0)
    assert path.tolist() == tg
    assert scores.tolist() == [lp[t, tg[t]] for t in range(L)]


def test_the_exact_fit_with_repeats_puts_a_blank_between_them():
    """T == L + R: the library's last back-pointer read is at a negative index (GCC 15's
    libstdc++ turns it into a read 2^61 bytes away). Its value is never used: not made here."""
    tg = [1, 2, 2, 3, 3, 3]
    lp, _ = _random(9, 6, seed=3)
    path, _ = forced_align(lp, tg, 0)
    assert path.tolist() == [1, 2, 0, 2, 3, 0, 3, 0, 3]


@pytest.mark.parametrize("L", [3, 10, 57, 300])
def test_the_band_of_every_frame_and_its_size(L):
    """The band never empties, ends on the last two states, and claims (T - L)(2L + 1) + L - 2
    - 2RL bits (+ 1 at the exact fit with a repeat): more than the library's (2L + 2)(T - L) only
    without a repeat, below 2L - 2 frames."""
    for repeat_at in (None, L // 2):
        tg = [1 + (i % 2) for i in range(L)]
        if repeat_at is not None and L > 1:
            tg[repeat_at] = tg[repeat_at - 1]
        R = sum(1 for i in range(1, L) if tg[i] == tg[i - 1])
        for T in range(max(L + R, 2), 3 * L + 3):
            starts, ends = library_band(T, tg)
            assert all(e - s >= 1 for s, e in zip(starts, ends, strict=True))
            # The last frame: the last target and, unless the fit is exact, the blank after it.
            assert starts[-1] == 2 * L - 1
            assert ends[-1] == (2 * L + 1 if T > L + R else 2 * L)
            band = sum(e - s for s, e in zip(starts, ends, strict=True))
            if T > L:
                expected = (T - L) * (2 * L + 1) + L - 2 - 2 * R * L + (R >= 1 and T == L + R)
                assert band == expected
            logical = (2 * L + 2) * (T - L)
            assert (band > logical) == (R == 0 and T < 2 * L - 2)


@pytest.mark.parametrize("targets,blank", [([1, -1], 0), ([1, 8], 0), ([1, 2], 8), ([1, 2], -1)])
def test_an_id_that_is_not_a_column_is_refused(targets, blank):
    """The caller's contract, checked: the library would read outside its input there."""
    lp, _ = _random(10, 2, V=8, seed=2)
    with pytest.raises(ValueError, match="column"):
        forced_align(lp, targets, blank)
    assert forced_align(lp, [1, 7], 0)[0].shape == (10,)  # the last column is one


def test_fewer_frames_than_the_text_needs_or_no_text_raise():
    lp, tg = _random(5, 5, seed=1, repeat_rate=0.0)
    with pytest.raises(ValueError, match="too long for CTC"):
        forced_align(lp[:4], tg, 0)
    with pytest.raises(ValueError, match="Empty transcript"):
        forced_align(lp, [], 0)


def test_a_huge_band_costs_bits_not_bytes():
    """The back-pointers are kept packed (two bits a slot, as the library's two vector<bool>):
    a whole-track pass of 10 000 frames and 3 000 targets runs."""
    lp, tg = _random(10_000, 3_000, V=30, seed=9, repeat_rate=0.03)
    path, scores = forced_align(lp, tg, 0)
    assert path.shape == (10_000,) and scores.dtype == np.float32
    p = path.tolist()
    runs = [label for i, label in enumerate(p) if i == 0 or label != p[i - 1]]
    assert [label for label in runs if label != 0] == tg  # it spells the targets, in order


# --- alignment.get_alignments: the library's wrapper around it ------------------------------------


class Tokenizer:
    pad_token_id = 0
    vocab_size = 4

    def get_vocab(self):
        return {"<pad>": 0, "A": 1, "b": 2, "c": 3}


def _merge_repeats(path, idx_to_token_map):
    out, i = [], 0
    while i < len(path):
        j = i
        while j < len(path) and path[j] == path[i]:
            j += 1
        out.append(types.SimpleNamespace(label=idx_to_token_map[path[i]], start=i, end=j - 1))
        i = j
    return out


@pytest.fixture
def library(monkeypatch):
    monkeypatch.setitem(
        sys.modules, "ctc_forced_aligner", types.SimpleNamespace(merge_repeats=_merge_repeats)
    )


def test_get_alignments_builds_the_library_s_targets_and_blank(library):
    lp, _ = _random(30, 1, V=5, seed=4)  # 4 vocabulary columns + <star>
    segments, scores, blank = alignment.get_alignments(lp, ["<star>", "a b", "c"], Tokenizer())
    assert blank == "<pad>"
    letters = [s.label for s in segments if s.label != "<pad>"]
    assert letters == ["<star>", "a", "b", "c"]  # lower-cased vocabulary, <star> after it
    assert scores.dtype == np.float32 and scores.shape == (30,)
    path, expected = forced_align(lp, [4, 1, 2, 3], 0)
    assert scores.tobytes() == expected.tobytes()


def test_get_alignments_keeps_the_library_s_checks(library):
    lp, _ = _random(30, 1, V=5, seed=4)

    class Big(Tokenizer):
        vocab_size = 3

    with pytest.raises(ValueError, match="more tokens than expected"):
        alignment.get_alignments(lp, ["a"], Big())
    with pytest.raises(ValueError, match="targets values must be within"):
        alignment.get_alignments(lp[:, :3], ["c"], Tokenizer())
    with pytest.raises(AssertionError, match="Empty transcript"):
        alignment.get_alignments(lp, [], Tokenizer())


def test_get_alignments_takes_the_vocabulary_s_own_blank(library):
    """`<blank>` when the vocabulary has one (MMS), else the pad, as the library does."""

    class WithBlank(Tokenizer):
        vocab_size = 5

        def get_vocab(self):
            return {"<pad>": 0, "a": 1, "b": 2, "c": 3, "<blank>": 4}

    lp, _ = _random(30, 1, V=6, seed=5)  # 5 vocabulary columns + <star>
    segments, scores, blank = alignment.get_alignments(lp, ["a b"], WithBlank())
    assert blank == "<blank>"
    assert scores.tobytes() == forced_align(lp, [1, 2], 4)[1].tobytes()
    assert {s.label for s in segments} <= {"<blank>", "a", "b"}
