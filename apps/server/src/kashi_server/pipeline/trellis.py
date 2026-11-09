"""The forced-alignment trellis with cuts (timing v3, V1, 2.31.0).

ctc_forced_aligner's Viterbi (`forced_align_impl.cpp`, 0.3.0) walks a CTC
trellis of 2L + 1 states (a blank around every target) over the emission
frames. It cannot be told that a state is off limits at some frames, which is
what V1 needs: a line inside a merged window may not start far from its own
stamp. This module is that Viterbi in numpy, line for line, plus forbidden
(state range x frame range) rectangles. A cut state's score is -inf at those
frames, so the best path is the best one that avoids every cut; with no cuts
it returns the library's path and scores exactly (same float32 sums, same
tie-breaks: the harness checks it against the compiled one).

Pure: numpy only, no torch, no library import. The caller runs the library
first and calls this only when the library's path breaks a bound, so every
window the bounds do not bind keeps the library's own output byte for byte.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Cut:
    """States ``first_state..last_state`` are forbidden at frames
    ``first_frame..last_frame`` (all inclusive)."""

    first_state: int
    last_state: int
    first_frame: int
    last_frame: int


def letter_bounds_cuts(position: int, lo_frame: int | None, hi_frame: int | None,
                       n_frames: int) -> list[Cut]:
    """Cuts that make target ``position`` (its letter is state 2p + 1) START
    within ``lo_frame..hi_frame``.

    Lower bound: the letter may not be emitted before ``lo_frame``; every path
    visits it, so its first frame is then >= ``lo_frame``. Upper bound: every
    state before the letter, blanks included, is forbidden from ``hi_frame``
    on, so the path is on the letter or past it by then. A bound outside the
    frames cuts nothing and is dropped."""
    cuts: list[Cut] = []
    letter = 2 * position + 1
    if lo_frame is not None and lo_frame > 0:
        cuts.append(Cut(letter, letter, 0, min(lo_frame, n_frames) - 1))
    if hi_frame is not None and hi_frame < n_frames - 1:
        cuts.append(Cut(0, letter - 1, max(hi_frame, 0), n_frames - 1))
    return cuts


def forced_align_cut(log_probs, targets: list[int], blank: int, cuts: list[Cut]):
    """CTC Viterbi over ``log_probs`` (T x C, log-softmax, float32) for
    ``targets``, avoiding ``cuts``. Returns (path, scores) as the library does
    (label per frame, int64; the label's log-prob per frame, float32), or None
    when the cuts leave no path."""
    import numpy as np

    lp = np.ascontiguousarray(log_probs, dtype=np.float32)
    n_frames = lp.shape[0]
    tg = np.asarray(targets, dtype=np.int64)
    n_targets = len(tg)
    if n_targets == 0:
        raise ValueError("Empty transcript")
    n_states = 2 * n_targets + 1
    repeats = int(np.count_nonzero(tg[1:] == tg[:-1]))
    if n_frames < n_targets + repeats:
        raise ValueError("targets length is too long for CTC.")
    labels = np.full(n_states, blank, dtype=np.int64)
    labels[1::2] = tg
    # A letter may be entered from two states back (skipping the blank)
    # unless it repeats the letter before it.
    skip = np.zeros(n_states, dtype=bool)
    skip[3::2] = tg[1:] != tg[:-1]
    neg = np.float32(-np.inf)
    by_frame: dict[int, list[Cut]] = {}
    for cut in cuts:
        for t in range(max(cut.first_frame, 0), min(cut.last_frame, n_frames - 1) + 1):
            by_frame.setdefault(t, []).append(cut)

    def apply_cuts(t: int, row) -> None:
        for cut in by_frame.get(t, ()):
            row[cut.first_state : cut.last_state + 1] = neg

    alpha = np.full(n_states, neg, dtype=np.float32)
    start = 0 if n_frames - (n_targets + repeats) > 0 else 1
    end = 2
    alpha[start:end] = lp[0, labels[start:end]]
    apply_cuts(0, alpha)
    back = np.zeros((n_frames, n_states), dtype=np.int8)
    pad = np.full(2, neg, dtype=np.float32)
    for t in range(1, n_frames):
        # The library's band: states that can still reach the end, or that
        # the start can already reach.
        if n_frames - t <= n_targets + repeats:
            if start % 2 == 1 and tg[start // 2] != tg[start // 2 + 1]:
                start += 1
            start += 1
        if t <= n_targets + repeats:
            if end % 2 == 0 and end < 2 * n_targets and tg[end // 2 - 1] != tg[end // 2]:
                end += 1
            end += 1
        row = np.full(n_states, neg, dtype=np.float32)
        first = start
        if start == 0:
            row[0] = alpha[0] + lp[t, blank]
            first = 1
        if first < end:
            shifted = np.concatenate((pad, alpha))  # shifted[i + 2] == alpha[i]
            x0 = alpha[first:end]
            x1 = shifted[first + 1 : end + 1]
            x2 = np.where(skip[first:end], shifted[first:end], neg)
            # The library's order: x2 on a strict win, then x1, else stay.
            take2 = (x2 > x1) & (x2 > x0)
            take1 = ~take2 & (x1 > x0) & (x1 > x2)
            row[first:end] = np.where(take2, x2, np.where(take1, x1, x0)) + lp[
                t, labels[first:end]
            ]
            back[t, first:end] = np.where(take2, 2, np.where(take1, 1, 0))
        apply_cuts(t, row)
        alpha = row
    last = n_states - 1 if alpha[n_states - 1] > alpha[n_states - 2] else n_states - 2
    if not np.isfinite(alpha[last]):
        return None
    path = np.empty(n_frames, dtype=np.int64)
    state = last
    for t in range(n_frames - 1, -1, -1):
        path[t] = labels[state]
        state -= int(back[t, state])
    scores = lp[np.arange(n_frames), path]
    return path, scores
