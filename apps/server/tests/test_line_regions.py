"""Timing v3, V1 (2.31.0): line regions cut into the window trellis, without torch.

The compiled ctc_forced_aligner Viterbi is not installed here; `trellis` is its
numpy copy (checked against it in the worker image, see the 2.31.0 paragraph of
the Faz 5 note), and these tests stand it in for the library.
"""

import itertools
import sys
import types
from dataclasses import dataclass

import numpy as np
import pytest

from kashi_server.pipeline import alignment
from kashi_server.pipeline.trellis import Cut, forced_align_cut, letter_bounds_cuts
from kashi_server.pipeline.windows import Window, line_regions

# --- the trellis --------------------------------------------------------------


def _paths(n_frames: int, targets: list[int]):
    """Every CTC state sequence over 2L + 1 states that starts on the first
    blank or letter, ends on the last letter or blank, and steps 0, 1 or 2 (two
    only onto a letter that differs from the one before)."""
    n_states = 2 * len(targets) + 1

    def step_ok(a: int, b: int) -> bool:
        if b in (a, a + 1):
            return True
        return b == a + 2 and b % 2 == 1 and b >= 3 and targets[b // 2] != targets[b // 2 - 1]

    def walk(prefix):
        if len(prefix) == n_frames:
            if prefix[-1] >= n_states - 2:
                yield prefix
            return
        for b in range(prefix[-1], min(prefix[-1] + 3, n_states)):
            if step_ok(prefix[-1], b):
                yield from walk(prefix + [b])

    for first in (0, 1):
        yield from walk([first])


def _brute(log_probs, targets, blank, cuts):
    labels = [blank if s % 2 == 0 else targets[s // 2] for s in range(2 * len(targets) + 1)]
    best = None
    for path in _paths(len(log_probs), targets):
        if any(c.first_state <= s <= c.last_state and c.first_frame <= t <= c.last_frame
               for t, s in enumerate(path) for c in cuts):
            continue
        score = sum(float(log_probs[t][labels[s]]) for t, s in enumerate(path))
        if best is None or score > best:
            best = score
    return best


@pytest.mark.parametrize("seed", range(40))
def test_the_trellis_finds_the_best_path_that_avoids_the_cuts(seed):
    rng = np.random.default_rng(seed)
    targets = [int(x) for x in rng.integers(1, 3, size=int(rng.integers(1, 4)))]
    n_frames = int(rng.integers(len(targets) * 2 + 1, 8))
    log_probs = np.log(rng.dirichlet(np.ones(3), size=n_frames)).astype(np.float32)
    cuts = []
    if seed % 2:
        p = int(rng.integers(0, len(targets)))
        cuts = letter_bounds_cuts(p, int(rng.integers(0, n_frames)),
                                  int(rng.integers(0, n_frames)), n_frames)
    found = forced_align_cut(log_probs, targets, 0, cuts)
    best = _brute(log_probs, targets, 0, cuts)
    if best is None:
        assert found is None
        return
    assert found is not None
    path, scores = found
    assert scores.dtype == np.float32
    assert float(scores.sum(dtype=np.float64)) == pytest.approx(best, abs=1e-4)
    assert scores.tolist() == [log_probs[t, path[t]] for t in range(n_frames)]


@pytest.mark.slow
def test_without_cuts_the_trellis_is_the_compiled_viterbi():
    """Byte for byte against ctc_forced_aligner's own, ties included (2026-11-09:
    3000 of 3000 in the worker image)."""
    compiled = pytest.importorskip("ctc_forced_aligner.ctc_forced_aligner").forced_align
    rng = np.random.default_rng(0)
    for _ in range(500):
        n_labels = int(rng.integers(3, 8))
        blank = int(rng.integers(0, n_labels))
        targets = [int(x) for x in rng.choice([c for c in range(n_labels) if c != blank],
                                              int(rng.integers(1, 25)))]
        n_frames = 2 * len(targets) + 30
        logits = np.round(rng.normal(size=(n_frames, n_labels)) * 2) / 2  # ties
        log_probs = (logits - np.log(np.exp(logits).sum(axis=1, keepdims=True))).astype(
            np.float32)
        path, scores = compiled(log_probs[None], np.asarray([targets], dtype=np.int64), blank)
        found = forced_align_cut(log_probs, targets, blank, [])
        assert found is not None
        assert found[0].tolist() == path[0].tolist()
        assert found[1].tobytes() == scores.tobytes()


def _entry(path, targets, blank, position):
    """The frame the path first emits target `position` (its letters in order)."""
    seen = -1
    previous = blank
    for t, label in enumerate(path):
        if label != blank and (label != previous or seen < 0):
            seen += 1
            if seen == position:
                return t
        previous = label
    raise AssertionError("position never emitted")


def _peaks(n_frames: int, peaks: dict[int, tuple[int, float]], n_labels: int = 3):
    """Log-probs where `blank` 0 wins every frame but `peaks` {frame: (label, p)}."""
    probs = np.full((n_frames, n_labels), 0.02)
    probs[:, 0] = 0.96
    for frame, (label, p) in peaks.items():
        probs[frame] = (1 - p) / (n_labels - 1)
        probs[frame, label] = p
    return np.log(probs / probs.sum(axis=1, keepdims=True)).astype(np.float32)


@pytest.mark.parametrize(("lo", "hi", "entry"), [
    (None, 31, 30),  # the latest allowed frame is exactly the letter's: kept
    (None, 29, 10),  # one frame earlier: the earlier peak
    (30, None, 30),  # the earliest allowed frame is exactly the letter's: kept
    (31, None, 40),  # one frame later: the next evidence inside the region
])
def test_a_letter_starts_inside_its_bounds_both_ends_inclusive(lo, hi, entry):
    # "a" sung at frames 10 and 30 (the louder) and faintly at 40.
    log_probs = _peaks(50, {10: (1, 0.6), 30: (1, 0.95), 40: (1, 0.3)})
    unbound = forced_align_cut(log_probs, [1], 0, [])
    assert unbound is not None and _entry(unbound[0], [1], 0, 0) == 30
    found = forced_align_cut(log_probs, [1], 0, letter_bounds_cuts(0, lo, hi, 50))
    assert found is not None
    assert _entry(found[0], [1], 0, 0) == entry


def test_bounds_outside_the_frames_cut_nothing():
    assert letter_bounds_cuts(2, 0, 49, 50) == []
    assert letter_bounds_cuts(2, -7, 80, 50) == []
    assert letter_bounds_cuts(2, 3, 40, 50) == [Cut(5, 5, 0, 2), Cut(0, 4, 40, 49)]


def test_cuts_no_path_can_avoid_return_none():
    log_probs = _peaks(10, {})
    assert forced_align_cut(log_probs, [1, 2], 0, letter_bounds_cuts(1, 8, 2, 10)) is None


# --- the regions --------------------------------------------------------------


def _window(lines, start=5_000):
    return Window(start - 350, start + 6_000, lines, start, start + 5_650)


def test_each_stamped_line_of_a_shared_window_gets_its_own_region():
    stamps = [5_000, 6_300, None, 8_000]
    assert line_regions(_window([0, 1, 2, 3]), stamps, 1_200) == {
        0: (3_800, 6_200), 1: (5_100, 7_500), 3: (6_800, 9_200),
    }


def test_a_window_with_one_stamped_line_and_v1_off_get_none():
    stamps = [5_000, None, 8_000]
    assert line_regions(_window([0, 1]), stamps, 1_200) == {}
    assert line_regions(_window([0, 1, 2]), stamps, None) == {}


def test_the_passes_read_the_region_on_their_own_clocks():
    # Slice from 4 650 ms; "you" carries a +20 ms windowed offset on the
    # document: an edges letter at slice time x lands at 4 650 + x + 20, a
    # segment letter is moved back by the 70 ms lead first.
    window = _window([0, 1])
    edges, segment = alignment._window_line_starts(
        window, ["you ready", "you ready"], [5_000, 6_300], 1_200,
        lambda text: 20 if text == "you" else 0, 70,
    )
    assert edges == [(3_800 - 4_670, 6_200 - 4_670), (5_100 - 4_670, 7_500 - 4_670)]
    assert segment == [(3_800 - 4_600, 6_200 - 4_600), (5_100 - 4_600, 7_500 - 4_600)]
    assert alignment._window_line_starts(window, ["a", "b"], [5_000, None], 1_200,
                                         lambda text: 0, 70) == (None, None)


# --- the library pass, held -----------------------------------------------------


@dataclass
class Segment:
    label: str
    start: int
    end: int


def _merge_repeats(path, idx_to_token_map):
    segments = []
    i = 0
    while i < len(path):
        j = i
        while j < len(path) and path[j] == path[i]:
            j += 1
        segments.append(Segment(idx_to_token_map[path[i]], i, j - 1))
        i = j
    return segments


class Tokenizer:
    """<pad> is the blank (as in the wav2vec2 checkpoints); a, b, y letters."""

    pad_token_id = 0

    def get_vocab(self):
        return {"<pad>": 0, "a": 1, "b": 2, "y": 3}


def _library_pass(log_probs, tokens_starred):
    """get_alignments' result on `log_probs` (the trellis without cuts is the
    library's Viterbi): (segments, scores, blank token)."""
    vocab = {k: v for k, v in Tokenizer().get_vocab().items()}
    vocab["<star>"] = len(vocab)
    targets = [vocab[c] for c in " ".join(tokens_starred).split(" ") if c in vocab]
    found = forced_align_cut(log_probs, targets, 0, [])
    assert found is not None
    return _merge_repeats(found[0].tolist(), {v: k for k, v in vocab.items()}), found[1], "<pad>"


@pytest.fixture
def library(monkeypatch):
    monkeypatch.setitem(sys.modules, "ctc_forced_aligner",
                        types.SimpleNamespace(merge_repeats=_merge_repeats))


# Two short lines "ab", "ab" in one window; "ab" is sung three times, at frames
# 10, 30 and 50, the first one quietly. Unheld, the Viterbi gives the lines the
# two loud ones: the first line one slot late (SexyBack 2:26 in miniature).
TOKENS = ["<star>", "a b", "a b", "<star>"]  # edges stars
SUNG = {10: (1, 0.5), 11: (2, 0.5), 30: (1, 0.97), 31: (2, 0.97), 50: (1, 0.97), 51: (2, 0.97)}


def _log_probs():
    return _peaks(70, SUNG, n_labels=5)  # <pad>, a, b, y, <star>


def _held(starts_ms, tally=None):
    log_probs = _log_probs()
    log_probs[:, 4] = 0.0  # the star column the library appends
    segments, scores, blank = _library_pass(log_probs, TOKENS)
    return segments, alignment._hold_line_starts(
        log_probs, 20.0, Tokenizer(), ["ab", "ab"], "eng", True, "edges",
        TOKENS, segments, scores, blank, starts_ms, tally,
    )


def _line_starts(segments):
    letters = [s for s in segments if s.label not in ("<pad>", "<star>")]
    return letters[0].start, letters[2].start


def test_a_line_held_near_its_stamp_takes_its_own_repeat(library):
    tally: dict = {}
    segments, held = _held([(0, 400), (400, 1_100)], tally)
    assert _line_starts(segments) == (30, 50)  # unheld: one slot late
    assert held is not None and held[0] is not segments
    assert _line_starts(held[0]) == (10, 30)
    assert held[1].dtype == np.float32 and len(held[1]) == 70
    # each bounded line: the token owning its letter, the region's first
    # frame, the letter's frame on the new path
    assert held[2] == [(1, 0, 10), (2, 20, 30)]
    assert tally == {"windows": 1, "lines": 1}


def test_a_path_inside_every_region_is_the_librarys_own(library):
    segments, held = _held([(0, 600), (400, 1_100)])
    # frame 30 = 600 ms: the latest start allowed, inclusive
    assert held is not None and held[0] is segments
    assert _line_starts(segments) == (30, 50)


@pytest.mark.parametrize(("past", "moved"), [(-1, False), (0, False), (1, True)])
def test_the_document_boundary_1199_1200_1201_ms(library, past, moved):
    # The first line's letter on the document: slice 4 650 + frame 30 x 20 ms +
    # 20 ms offset = 5 270. Its stamp sits 1 200 + `past` ms before that.
    stamp = 5_270 - 1_200 - past
    window = Window(4_650, 6_050, [0, 1], 5_000, 6_000)
    edges, _ = alignment._window_line_starts(
        window, ["ab", "ab"], [stamp, stamp + 1_500], 1_200, lambda text: 20, 70
    )
    assert edges is not None
    segments, held = _held(edges)
    assert held is not None
    assert (held[0] is not segments) is moved


@pytest.mark.parametrize(("early", "moved"), [(-1, False), (0, False), (1, True)])
def test_the_early_side_1199_1200_1201_ms(library, early, moved):
    # The second line's letter at frame 50: document 4 650 + 1 000 + 20 = 5 670;
    # its stamp sits 1 200 + `early` ms after that.
    stamp = 5_670 + 1_200 + early
    window = Window(4_650, 6_050, [0, 1], 5_000, 6_000)
    edges, _ = alignment._window_line_starts(
        window, ["ab", "ab"], [4_500, stamp], 1_200, lambda text: 20, 70
    )
    assert edges is not None
    segments, held = _held(edges)
    assert held is not None
    assert (held[0] is not segments) is moved


def _fake_pass(log_probs, record):
    """ctc_forced_aligner for one `_viterbi` call: edges stars, every character a
    letter, and the library's span rule (a word's span starts halfway into the
    blank before it; the first one takes the whole blank)."""

    def get_spans(tokens, segments, blank):
        letters = [i for i, s in enumerate(segments) if s.label != blank]
        spans, k = [], 0
        for token in tokens:
            n = len(token.split(" "))
            first, last = letters[k], letters[k + n - 1]
            k += n
            start = segments[first].start
            if first > 0 and segments[first - 1].label == blank:
                pad = segments[first - 1]
                start = pad.start if not spans else (pad.start + pad.end) // 2
            spans.append([Segment("", start, segments[last].end)])
        return spans

    def postprocess_results(text_starred, spans, stride, scores):
        return [{"text": t, "start": s[0].start * stride / 1000, "end": s[0].end * stride / 1000,
                 "score": -0.1} for t, s in zip(text_starred, spans, strict=True) if t != "<star>"]

    def get_alignments(emissions, tokens_starred, tokenizer):
        return _library_pass(emissions, tokens_starred)

    def preprocess_text(text, romanize, language, split_size, star_frequency):
        record.append(star_frequency)
        words = text.split()
        return ["<star>", *[" ".join(w) for w in words], "<star>"], ["<star>", *words, "<star>"]

    return types.SimpleNamespace(
        merge_repeats=_merge_repeats, get_spans=get_spans, get_alignments=get_alignments,
        postprocess_results=postprocess_results, preprocess_text=preprocess_text,
    )


@pytest.mark.parametrize(("earliest_ms", "start_s"), [(600, 0.7), (1_000, 1.2)])
def test_a_start_the_blank_pads_out_of_its_region_falls_back_to_its_letter(
    monkeypatch, earliest_ms, start_s
):
    # "ab" at frames 10 and 60 with silence between: the second word's span
    # starts halfway into it, frame 35 (700 ms). A region from 600 ms keeps
    # that; one from 1 000 ms (frame 50) puts the start on the letter, 1.2 s.
    log_probs = _peaks(80, {10: (1, 0.97), 11: (2, 0.97), 60: (1, 0.97), 61: (2, 0.97)},
                       n_labels=5)
    log_probs[:, 4] = -30.0  # stars at the edges only, kept out of the gap
    monkeypatch.setitem(sys.modules, "ctc_forced_aligner", _fake_pass(log_probs, []))
    tally: dict = {}
    rows = alignment._viterbi(log_probs, 20.0, Tokenizer(), ["ab", "ab"], "eng", "edges",
                              True, [(0, 400), (earliest_ms, 1_400)], tally)
    assert [r["start"] for r in rows][1] == pytest.approx(start_s)
    assert rows[1]["end"] == pytest.approx(1.22)
    assert tally.get("pads", 0) == (1 if start_s == 1.2 else 0)


def test_line_items_follow_the_library_split():
    assert alignment._line_first_items(["you ready", "", "go 'head x"], "eng", True) == [0, 2, 2]
    # per character, the join's spaces included
    assert alignment._line_first_items(["それら", "BxMxC"], "jpn", True) == [0, 4]
    assert alignment._line_first_items(["それら"], "jpn", False) is None


def test_every_valid_path_is_enumerated_once():
    # Sanity for the brute force: one letter over three frames.
    paths = list(_paths(3, [1]))
    assert len(paths) == len({tuple(p) for p in paths})
    assert [0, 0, 1] in paths and [1, 2, 2] in paths and [0, 1, 1] in paths
    assert not any(p[-1] == 0 for p in paths)
    assert all(b - a in (0, 1, 2) for p in paths for a, b in itertools.pairwise(p))


# --- the wiring -----------------------------------------------------------------


class FakeAudio:
    def __init__(self, n_samples: int):
        self.shape = (n_samples,)

    def __getitem__(self, key):
        _, window = key
        return FakeAudio(min(window.stop, self.shape[0]) - window.start)


def _align_shared_window(monkeypatch, line_region_ms):
    """Four lines stamped 2, 1 and 4 s apart: one window (the first two are
    under the 4 s minimum, the last 3 s merge back); English by-initial offsets."""
    seen: list = []
    monkeypatch.setitem(sys.modules, "ctc_forced_aligner", types.SimpleNamespace(
        load_audio=lambda *a, **k: FakeAudio(12 * 16_000)))
    model = types.SimpleNamespace(dtype=None, device="cpu")
    monkeypatch.setattr(alignment, "_load_model", lambda name: (model, object()))

    def fake_hybrid(model, tokenizer, piece, texts, language, *, romanize, exact_stride,
                    edges_starts=None, segment_starts=None, tally=None):
        seen.append((texts, edges_starts, segment_starts))
        rows = [{"text": w, "start": 0.5 + 0.3 * k, "end": 0.7 + 0.3 * k, "score": -0.1}
                for k, w in enumerate(" ".join(texts).split())]
        return rows, rows

    monkeypatch.setattr(alignment, "_align_slice_hybrid", fake_hybrid)
    monkeypatch.setattr(alignment, "_align_texts", lambda *a, **k: [])
    alignment.align(
        "x.wav", ["you ready", "you ready", "get your sexy on", "uh yes"], "eng",
        [2_000, 4_000, 5_000, 9_000],
        "m", True, -50, {"vowel": -80, "sonorant": -20, "plosive": -30, "fricative": -50},
        line_region_ms=line_region_ms,
    )
    return seen


def test_align_hands_each_pass_its_line_regions_on_its_own_clock(monkeypatch):
    (texts, edges, segment), = _align_shared_window(monkeypatch, 1_200)
    assert texts == ["you ready", "you ready", "get your sexy on", "uh yes"]
    # The slice starts 350 ms before the first stamp. "you" is a sonorant:
    # -20 + the 70 ms windowed lead = +50 on the document; "get" a plosive,
    # +40; "uh" a vowel, -10.
    you, get, uh = 1_650 + 50, 1_650 + 40, 1_650 - 10
    assert edges == [(800 - you, 3_200 - you), (2_800 - you, 5_200 - you),
                     (3_800 - get, 6_200 - get), (7_800 - uh, 10_200 - uh)]
    assert segment == [(lo + 70, hi + 70) for lo, hi in edges]


def test_v1_off_hands_the_passes_no_regions(monkeypatch):
    (_, edges, segment), = _align_shared_window(monkeypatch, None)
    assert edges is None and segment is None
