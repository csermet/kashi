"""The forced-alignment trellis: the job's Viterbi (`forced_align`, since 2026-11-19) and the
trellis with cuts (timing v3, V1, 2.31.0).

ctc_forced_aligner's Viterbi (`forced_align_impl.cpp`, 0.3.0) walks a CTC
trellis of 2L + 1 states (a blank around every target) over the emission
frames.

`forced_align` is that Viterbi in numpy, written to return EXACTLY what the
compiled one returns wherever the compiled one stays inside its buffers (same
band, same float32 sums, same tie-breaks, the same flat back-pointer layout,
read back the same way), and a defined result everywhere else. The compiled
one does not stay inside them on three kinds of input (measured with a
bounds-checked port and AddressSanitizer, docs/research/refresh-from-evidence-
2026-11.md): an exact fit (T == L + R) and, when the path starts with a blank,
T == L + R + 1 (its last back-pointer read is at a negative index, which a
build with GCC 15's libstdc++ turns into a read ~2^61 bytes away), and, without
a repeated letter, a band that claims more back-pointer bits than its buffers
hold (fewer than 2L - 2 frames, less the last word's slack). Here the last read
is not made (its value was never used) and the back-pointers have room for the
whole band, so those inputs get the path the compiled code meant.

`forced_align_cut` cannot be told the library's flat layout and does not need
it: V1 needs a state to be off limits at some frames (a line inside a merged
window may not start far from its own stamp), so it is the same Viterbi plus
forbidden (state range x frame range) rectangles. A cut state's score is -inf
at those frames, so the best path is the best one that avoids every cut; with
no cuts it returns the same path and scores as `forced_align` on every input
whose path stays in the band.

Pure: numpy only, no torch, no library import.
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


def library_band(n_frames: int, targets) -> tuple[list[int], list[int]]:
    """The compiled Viterbi's band, frame by frame: (starts, ends) for t = 1 .. T - 1, the
    states it computes at frame t being starts[t - 1] <= i < ends[t - 1]. Its bookkeeping line
    for line; the one target it can read past the end (`targets[start / 2 + 1]`, when start
    reaches 2L - 1 before the last frame, which no input reaches) is taken as different."""
    tg = [int(x) for x in targets]
    n_targets = len(tg)
    repeats = sum(1 for i in range(1, n_targets) if tg[i] == tg[i - 1])
    start = 0 if n_frames - (n_targets + repeats) > 0 else 1
    end = 1 if n_targets == 0 else 2
    starts: list[int] = []
    ends: list[int] = []
    for t in range(1, n_frames):
        if n_frames - t <= n_targets + repeats:
            if start % 2 == 1 and (
                start // 2 + 1 >= n_targets or tg[start // 2] != tg[start // 2 + 1]
            ):
                start += 1
            start += 1
        if t <= n_targets + repeats:
            if end % 2 == 0 and end < 2 * n_targets and tg[end // 2 - 1] != tg[end // 2]:
                end += 1
            end += 1
        starts.append(start)
        ends.append(end)
    return starts, ends


def forced_align(log_probs, targets, blank: int):
    """The compiled Viterbi's (path, scores) for ``log_probs`` (T x C, log-softmax, float32) and
    ``targets`` (L ids, none of them ``blank``), as `compute` in forced_align_impl.cpp returns
    them (label per frame, int64; that label's log-prob per frame, float32).

    The forward pass is the library's: the same band, the same float32 sums and comparisons (a
    letter's skip only when it differs from the one before, then x2 on a strict win, x1 on a
    strict win, else stay). Its back-pointers are kept in the library's flat layout - the
    frame t block starts where the previous frame's ended and holds states start..end - 1 - and
    the backtrace reads them at the library's index (that frame's block start + state - band
    start), so a path that leaves the band (only possible through -inf ties) reads what the
    library reads, a slot no frame wrote reading 0. The differences, all where the library
    leaves its buffers: every block has room (the library's are (2L + 2)(T - L) bits, in whole
    64-bit words), and the last read (frame 0, whose value the library never uses) is not made.
    Raises ValueError for an empty target list or fewer frames than the targets need
    (T < L + R), which the library refuses too."""
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
    skip = np.zeros(n_states, dtype=bool)
    skip[3::2] = tg[1:] != tg[:-1]
    neg = np.float32(-np.inf)
    starts, ends = library_band(n_frames, tg)

    alpha = np.full(n_states, neg, dtype=np.float32)
    first0 = 0 if n_frames - (n_targets + repeats) > 0 else 1
    alpha[first0:2] = lp[0, labels[first0:2]]
    # Frame t's block: slot (seek + i - start) for state i, seek advancing by end - start.
    seeks = [0] * n_frames  # seeks[t] for t >= 1
    blocks: list = [None] * n_frames  # packed (x1-won, x2-won) bits of frame t's block
    pad = np.full(2, neg, dtype=np.float32)
    seek = 0
    for t in range(1, n_frames):
        start, end = starts[t - 1], ends[t - 1]
        seeks[t] = seek
        row = np.full(n_states, neg, dtype=np.float32)
        first = start
        if start == 0:
            row[0] = alpha[0] + lp[t, blank]
            first = 1
        width = max(end - start, 0)
        take1_bits = np.zeros(width, dtype=bool)
        take2_bits = np.zeros(width, dtype=bool)
        if first < end:
            shifted = np.concatenate((pad, alpha))  # shifted[i + 2] == alpha[i]
            x0 = alpha[first:end]
            x1 = shifted[first + 1 : end + 1]
            x2 = np.where(skip[first:end], shifted[first:end], neg)
            take2 = (x2 > x1) & (x2 > x0)
            take1 = ~take2 & (x1 > x0) & (x1 > x2)
            row[first:end] = np.where(take2, x2, np.where(take1, x1, x0)) + lp[
                t, labels[first:end]
            ]
            take1_bits[first - start :] = take1
            take2_bits[first - start :] = take2
        blocks[t] = (np.packbits(take1_bits), np.packbits(take2_bits), width)
        seek += end - start
        alpha = row

    block_starts = np.asarray(seeks[1:], dtype=np.int64)

    def back_pointer(index: int) -> int:
        """The library's back-pointer value at flat slot ``index``: (x2-won << 1) | x1-won."""
        if index < 0 or n_frames < 2:
            return 0
        # The frame whose block holds it: the last one starting at or before it.
        owner = int(np.searchsorted(block_starts, index, side="right"))
        if owner < 1:
            return 0
        take1, take2, width = blocks[owner]
        local = index - seeks[owner]
        if local >= width:  # past the last block: a slot no frame wrote
            return 0
        byte, bit = divmod(local, 8)
        return (((int(take2[byte]) >> (7 - bit)) & 1) << 1) | ((int(take1[byte]) >> (7 - bit)) & 1)

    last = n_states - 1 if alpha[n_states - 1] > alpha[n_states - 2] else n_states - 2
    path = np.empty(n_frames, dtype=np.int64)
    state = last
    for t in range(n_frames - 1, -1, -1):
        # C++: state % 2 == 0 ? blank : targets[state / 2], `/` truncating toward zero (a state
        # below 0 is only reachable through -inf ties; one whose target is out of range reads
        # outside the library's buffer and gets the blank here).
        target = state // 2 if state >= 0 else -((-state) // 2)
        if state % 2 == 0 or not 0 <= target < n_targets:
            path[t] = blank
        else:
            path[t] = tg[target]
        if t == 0:
            break
        state -= back_pointer(seeks[t] + state - starts[t - 1])
    scores = lp[np.arange(n_frames), path]
    return path, scores
