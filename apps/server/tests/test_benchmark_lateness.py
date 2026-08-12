"""The lateness profile has to survive two ways of fooling yourself:
aggregating words instead of songs, and reading a fitted number as a gain."""

from benchmarks.lateness import (
    best_offset,
    held_out_gain,
    lateness_stats,
    pco_at_offset,
    signed_errors_by_song,
)


def _song(stem, errors, verified=True):
    """A --dump-words song: truth fixed, hypothesis carrying the error."""
    return {
        "stem": stem,
        "word_detail": [
            {"i": i, "token": "w", "truth_ms": 1000, "hyp_ms": 1000 + e, "verified": verified}
            for i, e in enumerate(errors)
        ],
    }


def _report(*songs):
    return {"jamendo": {"songs": list(songs)}}


def test_errors_are_signed_and_keyed_by_song():
    got = signed_errors_by_song(_report(_song("a", [80, -20]), _song("b", [0])))
    assert got == {"a": [80.0, -20.0], "b": [0.0]}


def test_unverified_words_are_dropped():
    """In the Turkish set an untouched word's truth IS the model's own output.
    Counting those measures the model against itself and reports no bias."""
    report = _report(_song("a", [80]), _song("b", [500], verified=False))
    assert signed_errors_by_song(report) == {"a": [80.0]}


def test_a_run_without_dump_words_is_not_a_zero_bias_run():
    assert signed_errors_by_song(_report({"stem": "a"})) == {}


def test_lateness_stats_separate_pooled_from_per_song():
    """One badly broken song can carry the pooled median on its own; the
    per-song view is what says "every song does this"."""
    stats = lateness_stats({"a": [10.0] * 2, "b": [900.0] * 100})
    assert stats["words"] == 102
    assert stats["late_share"] == 1.0
    assert stats["median_signed_ms"] == 900.0  # the long song owns the pool
    assert stats["song_median_ms"] == 455.0  # ...but not the songs
    assert stats["songs_late"] == 2


def test_pco_averages_songs_not_words():
    """run.py reports the mean of per-song fractions (MIREX). Pooling would
    make a long song outvote a short one and produce a number that cannot be
    compared with any result file."""
    # Song A: 1 word inside tolerance. Song B: 9 words outside.
    per_song = pco_at_offset({"a": [0.0], "b": [500.0] * 9}, 0, (100,))
    assert per_song["0.1"] == 0.5  # (1.0 + 0.0) / 2
    # Pooling would have said 1/10 = 0.1.


def test_best_offset_recovers_a_planted_bias():
    """Tolerance 50 ms, bias 80 ms: the bias is what puts the words OUTSIDE,
    so correcting it is the only thing that can move the score. Every shift
    from -120 to -40 ms ties at a perfect score; the middle of that plateau is
    the bias itself."""
    planted = {f"s{i}": [80.0, 75.0, 85.0] for i in range(4)}
    assert best_offset(planted, 50) == -80


def test_zero_wins_a_tie():
    """Everything is already inside tolerance, so a wide band of offsets ties.
    Shipping any of them would be a claim about the model that the data never
    made — zero has to win the tie."""
    assert best_offset({f"s{i}": [0.0] for i in range(3)}, 300) == 0


def test_held_out_confirms_a_real_bias():
    """A bias every song shares transfers to the song that was held out."""
    planted = {f"s{i}": [70.0, 80.0, 90.0] for i in range(5)}
    cv = held_out_gain(planted, 50)
    assert cv["baseline"] == 0.0  # +80 ms against a 50 ms tolerance: all outside
    assert cv["held_out"] == 1.0
    assert cv["offset_min_ms"] == cv["offset_max_ms"] == -80


def test_held_out_refuses_to_reward_noise():
    """The trap, in two songs: one is 100 ms late, the other 100 ms early.
    Fitting on both finds a shift that rescues half the corpus and looks like
    +0.5 — but each held-out fold fits the OTHER song and lands on exactly the
    wrong shift, so the honest gain is zero."""
    noise = {"a": [100.0] * 3, "b": [-100.0] * 3}
    fitted = pco_at_offset(noise, best_offset(noise, 50), (50,))["0.05"]
    cv = held_out_gain(noise, 50)
    assert fitted == 0.5  # what fitting on the eval set claims
    assert cv["held_out"] == 0.0  # what survives cross-validation
    assert cv["gain"] <= 0.0


def test_a_single_song_cannot_be_cross_validated():
    assert held_out_gain({"a": [80.0]}, 100) == {}
