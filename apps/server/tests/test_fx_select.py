"""Which tagged words fire — the rules, not the matching.

The failure this guards against is subtle: every candidate goes exactly where
it was told, so a test that checks "did the tag survive" passes while the
user's actual complaint (too many, all in the same place) is untouched. So the
assertions here are about DISTRIBUTION — how many, how far apart, and whether
the back half of the song is represented at all.
"""

from kashi_server.pipeline.energy import Section
from kashi_server.pipeline.fx_select import (
    KEEP_ALL_BELOW,
    MAX_SONG_CAP,
    MIN_GAP_MS,
    MIN_PLAIN_WORDS_BETWEEN,
    SELECT_PLAN,
    LineFacts,
    select_fx_words,
)
from kashi_server.pipeline.semantics import WordTag

LINE_MS = 4000
WORD_MS = 400


def lines(count: int, words: int = 8) -> list[LineFacts]:
    """A song of evenly spaced lines, each `words` long."""
    return [
        LineFacts(
            words=words,
            start_ms=i * LINE_MS,
            end_ms=i * LINE_MS + LINE_MS - 200,
            word_starts=tuple(i * LINE_MS + w * WORD_MS for w in range(words)),
        )
        for i in range(count)
    ]


def tag(line: int, word: int, name: str = "love", intensity: float = 0.6) -> WordTag:
    return WordTag(line, word, name, intensity)


def test_a_rare_category_keeps_every_occurrence():
    song = lines(20)
    cands = [tag(0, 1), tag(7, 2), tag(15, 3)]
    got = select_fx_words(cands, song).words
    assert len(got) == 3


def test_a_dominant_category_is_roughly_halved():
    song = lines(42)
    cands = [tag(i, 1) for i in range(42)]
    got = select_fx_words(cands, song).words
    # Halved by density, then trimmed by the song's cadence.
    assert len(got) <= MAX_SONG_CAP
    assert len(got) >= 12


def test_the_skipped_ones_are_spread_not_front_loaded():
    # THE regression test for the old brake: it kept whatever the sort reached
    # first, so a category concentrated early ate the budget and the back half
    # of the song went silent.
    song = lines(40)
    cands = [tag(i, 1) for i in range(40)]
    got = select_fx_words(cands, song).words

    assert got[0].line == 0, "the first time a word is sung it must be seen"
    assert got[-1].line >= 30, "the end of the song must be represented"
    first_half = sum(1 for t in got if t.line < 20)
    second_half = len(got) - first_half
    assert abs(first_half - second_half) <= 2


def test_two_effect_words_are_never_adjacent():
    song = lines(1, words=12)
    cands = [tag(0, w) for w in range(5)]
    got = select_fx_words(cands, song).words
    positions = sorted(t.word for t in got)
    for earlier, later in zip(positions, positions[1:], strict=False):
        assert later - earlier > MIN_PLAIN_WORDS_BETWEEN


def test_line_length_sets_how_many_a_line_may_carry():
    for words, expected in ((5, 1), (8, 2), (14, 3)):
        song = [LineFacts(words=words, start_ms=0, end_ms=8000)]
        # Spaced far enough apart that only the quota, not the gap rule, binds.
        cands = [tag(0, w, name=f"t{w}", intensity=0.6) for w in range(0, words, 4)]
        got = select_fx_words(cands, song).words
        assert len(got) == expected, f"{words}-word line kept {len(got)}"


def test_a_line_with_five_candidates_keeps_at_most_its_quota():
    song = [LineFacts(words=14, start_ms=0, end_ms=8000)]
    cands = [
        tag(0, 0, "love", 0.6),
        tag(0, 1, "fire", 0.8),
        tag(0, 5, "water", 0.5),
        tag(0, 6, "night", 0.5),
        tag(0, 11, "shine", 0.7),
    ]
    got = select_fx_words(cands, song).words
    assert len(got) == 3
    positions = sorted(t.word for t in got)
    for earlier, later in zip(positions, positions[1:], strict=False):
        assert later - earlier > MIN_PLAIN_WORDS_BETWEEN


def test_a_chorus_with_something_to_say_is_never_left_silent():
    # The dominant category lives in the opening; the chorus is at the end.
    song = lines(30)
    cands = [tag(i, 1) for i in range(12)] + [tag(i, 1, "fire", 0.8) for i in range(25, 29)]
    chorus = Section(type="chorus", start_ms=25 * LINE_MS, end_ms=29 * LINE_MS)
    got = select_fx_words(cands, song, [chorus]).words
    assert any(25 <= t.line < 29 for t in got)


def test_the_chorus_guarantee_never_exceeds_the_cap():
    song = lines(60)
    cands = [tag(i, 1) for i in range(50)] + [tag(55, 1, "fire", 0.8)]
    chorus = Section(type="chorus", start_ms=55 * LINE_MS, end_ms=56 * LINE_MS)
    result = select_fx_words(cands, song, [chorus])
    assert len(result.words) <= result.stats.song_cap
    assert any(t.line == 55 for t in result.words)


def test_a_song_that_is_all_chorus_carries_no_information():
    # Mirrors structure.py's coverage rule: a "chorus" spanning the track is
    # the song's texture, not a section, so it must not tilt anything.
    song = lines(30)
    cands = [tag(i, 1) for i in range(30)]
    everything = Section(type="chorus", start_ms=0, end_ms=30 * LINE_MS)
    result = select_fx_words(cands, song, [everything])
    assert "chorus" in result.stats.disabled_types
    assert len(result.words) <= result.stats.song_cap
    assert result.words[-1].line >= 20, "still spread over the whole song"


def test_a_normal_chorus_survives_even_when_it_holds_most_of_the_tags():
    """The denominator matters: hook words LIVE in the chorus.

    Coverage is measured in time, like structure.py does. Counting tagged
    lines instead would put an ordinary chorus over any threshold — it would
    switch the rule off on exactly the songs it exists for.
    """
    song = lines(50)  # 50 lines x 4 s = 200 s
    chorus_lines = list(range(10, 20)) + list(range(30, 40))  # 80 s = 40% of it
    cands = [tag(i, 1) for i in chorus_lines] + [tag(i, 1) for i in (0, 3, 45, 48)]
    sections = [
        Section("chorus", 10 * LINE_MS, 20 * LINE_MS),
        Section("chorus", 30 * LINE_MS, 40 * LINE_MS),
    ]
    result = select_fx_words(cands, song, sections)
    assert "chorus" not in result.stats.disabled_types
    assert any(t.line in chorus_lines for t in result.words)


def test_a_short_song_is_not_given_a_dense_cadence():
    """The floor must not override the cadence.

    A one-minute edit under a flat floor of 12 got an effect every five
    seconds — the density the cap was introduced to prevent, restated.
    """
    minute = [
        LineFacts(words=6, start_ms=i * 3000, end_ms=i * 3000 + 2800) for i in range(20)
    ]  # 60 s
    result = select_fx_words([tag(i, 1, name=f"t{i}") for i in range(20)], minute)
    seconds_per_effect = 60 / max(1, len(result.words))
    assert seconds_per_effect >= 7, f"one effect every {seconds_per_effect:.1f}s"


def test_a_sprawling_high_does_not_discredit_an_honest_chorus():
    # The correction that mattered: judging coverage over the COMBINED set let
    # a loud master delete a perfectly good chorus signal.
    song = lines(30)
    cands = [tag(i, 1) for i in range(30)]
    sections = [
        Section(type="high", start_ms=0, end_ms=26 * LINE_MS),  # 87% of the track
        Section(type="chorus", start_ms=26 * LINE_MS, end_ms=29 * LINE_MS),
    ]
    result = select_fx_words(cands, song, sections)
    assert "high" in result.stats.disabled_types
    assert "chorus" not in result.stats.disabled_types
    assert any(26 <= t.line < 29 for t in result.words)


def test_verses_are_not_starved_by_a_chorus_heavy_category():
    # Density thinning is positional, not priority-weighted: a category that
    # lives mostly in the chorus must still appear in the verses.
    song = lines(40)
    chorus_lines = list(range(20, 38))
    verse_lines = [0, 3, 6, 9, 12, 15]
    cands = [tag(i, 1) for i in chorus_lines + verse_lines]
    chorus = Section(type="chorus", start_ms=20 * LINE_MS, end_ms=38 * LINE_MS)
    got = select_fx_words(cands, song, [chorus]).words
    assert any(t.line in verse_lines for t in got)


def test_the_song_cap_is_a_cadence_not_a_flat_count():
    # A two-minute nightcore edit and a six-minute track must not get the same
    # number of effects — that was the whole complaint, restated.
    short = [
        LineFacts(words=8, start_ms=i * 4000, end_ms=i * 4000 + 3800) for i in range(30)
    ]
    short_result = select_fx_words([tag(i, 1) for i in range(30)], short)

    long_song = [
        LineFacts(words=8, start_ms=i * 9000, end_ms=i * 9000 + 8800) for i in range(40)
    ]
    long_result = select_fx_words([tag(i, 1) for i in range(40)], long_song)

    assert short_result.stats.song_cap < long_result.stats.song_cap
    assert long_result.stats.song_cap == MAX_SONG_CAP


def test_two_effects_never_land_within_the_minimum_gap():
    """Back-to-back lines are the hole the per-line rule cannot see.

    The song is deliberately LONG and sparse so the cadence cap has slack —
    otherwise the cap alone spreads everything out and this proves nothing
    about the sweep. Two of the lines are sung 300 ms apart; exactly one of
    them may fire.
    """
    starts = [i * 12_000 for i in range(10)]
    starts.append(starts[4] + 300)  # a line landing right on top of another
    starts.sort()
    song = [
        LineFacts(words=4, start_ms=s, end_ms=s + 2000, word_starts=(s, s + 400, s + 800, s + 1200))
        for s in starts
    ]
    # Distinct categories, so density thinning keeps every one and the gap
    # rule is the only thing that can separate them.
    cands = [tag(i, 0, name=f"t{i}") for i in range(len(song))]

    result = select_fx_words(cands, song)
    assert len(result.words) < len(cands), "the crowded pair must lose one"
    assert result.stats.dropped_gap >= 1

    times = sorted(song[t.line].word_starts[t.word] for t in result.words)
    for earlier, later in zip(times, times[1:], strict=False):
        assert later - earlier >= MIN_GAP_MS


def test_no_sections_at_all_still_selects():
    song = lines(30)
    cands = [tag(i, 1) for i in range(30)]
    result = select_fx_words(cands, song, [])
    assert result.words
    assert result.stats.guard_reinserted == 0


def test_only_high_sections_is_the_default_production_case():
    # structure_sections yields nothing for most songs; energy always runs.
    song = lines(30)
    cands = [tag(i, 1) for i in range(10)] + [tag(i, 1, "fire", 0.8) for i in range(24, 28)]
    high = Section(type="high", start_ms=24 * LINE_MS, end_ms=28 * LINE_MS)
    got = select_fx_words(cands, song, [high]).words
    assert any(24 <= t.line < 28 for t in got)


def test_a_category_occurring_twice_keeps_both():
    song = lines(40)
    cands = [tag(i, 1) for i in range(30)] + [
        tag(33, 1, "poison", 0.8),
        tag(37, 1, "poison", 0.8),
    ]
    got = select_fx_words(cands, song).words
    assert sum(1 for t in got if t.tag == "poison") == 2


def test_output_does_not_depend_on_input_order():
    song = lines(25)
    cands = [tag(i, 1, name=f"t{i % 4}") for i in range(25)]
    forward = select_fx_words(cands, song).words
    backward = select_fx_words(list(reversed(cands)), song).words
    assert forward == backward


def test_output_is_a_subset_in_document_order():
    song = lines(25)
    cands = [tag(i, 1, name=f"t{i % 3}") for i in range(25)]
    got = select_fx_words(cands, song).words
    keys = [(t.line, t.word) for t in got]
    assert keys == sorted(keys)
    assert len(keys) == len(set(keys))
    assert set(got).issubset(set(cands))


def test_degenerate_input_is_dropped_not_crashed():
    song = lines(3)
    cands = [
        tag(99, 0),  # line past the end
        tag(0, 50),  # word past the end
        tag(1, 1),
        tag(1, 1),  # duplicate
    ]
    got = select_fx_words(cands, song).words
    assert got == [tag(1, 1)]


def test_empty_inputs():
    assert select_fx_words([], lines(3)).words == []
    assert select_fx_words([tag(0, 0)], []).words == []
    result = select_fx_words([], [])
    assert result.plan == SELECT_PLAN


def test_a_wordless_line_carries_nothing():
    song = [LineFacts(words=0, start_ms=0, end_ms=1000)]
    assert select_fx_words([tag(0, 0)], song).words == []


def test_zero_length_line_span_does_not_divide():
    song = [LineFacts(words=4, start_ms=5000, end_ms=5000)]
    got = select_fx_words([tag(0, 1)], song, [Section("chorus", 0, 10000)]).words
    assert len(got) == 1


def test_keep_all_below_is_honoured_exactly():
    song = lines(30)
    cands = [tag(i * 3, 1) for i in range(KEEP_ALL_BELOW)]
    assert len(select_fx_words(cands, song).words) == KEEP_ALL_BELOW
