"""Which tagged words fire — the rules, not the matching.

The failure this guards against is subtle: every candidate goes exactly where
it was told, so a test that checks "did the tag survive" passes while the
user's actual complaint (too many, all in the same place) is untouched. So the
assertions here are about DISTRIBUTION — how many, how far apart, and whether
the back half of the song is represented at all.
"""

import math

from kashi_server.pipeline.energy import Section
from kashi_server.pipeline.fx_select import (
    KEEP_ALL_BELOW,
    MAX_RUN_WORDS,
    MAX_SONG_CAP,
    MIN_GAP_MS,
    MIN_PLAIN_WORDS_BETWEEN,
    NON_CLASS_RESERVE,
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


# --- P7: repeat consistency, gestures and position -------------------------


def sung(count: int, tokens: list[str], start_at: int = 0) -> list[LineFacts]:
    """`count` lines that all sing the same `tokens`, evenly spaced."""
    return [
        LineFacts(
            words=len(tokens),
            start_ms=(start_at + i) * LINE_MS,
            end_ms=(start_at + i) * LINE_MS + LINE_MS - 200,
            word_starts=tuple(
                (start_at + i) * LINE_MS + w * WORD_MS for w in range(len(tokens))
            ),
            norm_tokens=tuple(tokens),
        )
        for i in range(count)
    ]


# Synthetic tokens: the selector only ever compares them for equality.
CHORUS = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel", "india"]
VERSE = ["kilo", "lima", "mike", "november", "oscar", "papa", "quebec", "romeo"]


def spread(count: int, tokens: list[str], every_ms: int = 30_000) -> list[LineFacts]:
    """Identical lines spaced widely enough that the cadence cap has room."""
    return [
        LineFacts(
            words=len(tokens),
            start_ms=i * every_ms,
            end_ms=i * every_ms + 3000,
            word_starts=tuple(i * every_ms + w * WORD_MS for w in range(len(tokens))),
            norm_tokens=tuple(tokens),
        )
        for i in range(count)
    ]


def test_every_repeat_of_a_chorus_fires_on_the_SAME_word():
    """The field complaint, in one assertion.

    Measured on the real archive: one chorus line repeated seven times chose
    word 7 three times and word 4 once, and only four of the seven fired at
    all. Both halves of that have to go.
    """
    song = spread(7, CHORUS)  # 7 repeats over ~3 minutes
    cands = [tag(i, 4, "music", 0.6) for i in range(7)]
    got = select_fx_words(cands, song).words

    assert len(got) == 7, "every repeat fires"
    assert {t.word for t in got} == {4}, "and always on the same word"


def test_a_repeat_that_lost_its_candidate_stays_silent_rather_than_inventing_one():
    # Transcript variance can leave one repeat without the word the pattern
    # names. Emitting it anyway would mean a tag that was never a candidate.
    # Line 2 IS in the class (it has a candidate of its own) but not at the
    # word the pattern names — transcript variance does this.
    song = spread(4, CHORUS)
    cands = [tag(i, 4, "music", 0.6) for i in (0, 1, 3)] + [tag(2, 8, "music", 0.6)]
    result = select_fx_words(cands, song)

    assert {t.line for t in result.words} == {0, 1, 3}, "the odd repeat stays silent"
    assert result.stats.pattern_missing == 1
    assert set(result.words).issubset(set(cands)), "never invents a tag"


def test_lines_with_no_sung_token_never_form_a_class():
    # Glyph-only lines normalize to nothing. Grouping them would put unrelated
    # lines in one class and exempt the whole song from thinning.
    song = [
        LineFacts(words=2, start_ms=i * LINE_MS, end_ms=i * LINE_MS + 2000, norm_tokens=("", ""))
        for i in range(6)
    ]
    result = select_fx_words([tag(i, 0, "music", 0.6) for i in range(6)], song)
    assert result.stats.repeat_classes == 0


def test_a_repeated_word_on_one_line_is_ONE_gesture():
    # "music, music, music" is a single insistence, not three effects — but
    # every occurrence still lights up.
    song = [
        LineFacts(
            words=6,
            start_ms=0,
            end_ms=6000,
            word_starts=(0, 400, 800, 1200, 1600, 2000),
            norm_tokens=("music", "music", "music", "and", "more", "here"),
        )
    ]
    cands = [tag(0, w, "music", 0.6) for w in (0, 1, 2)]
    got = select_fx_words(cands, song).words
    assert [t.word for t in got] == [0, 1, 2], "all three light up"


def test_a_glyph_between_repeats_does_not_break_the_gesture():
    # A note symbol is not sung, so it must not split "music ♪ music" into two
    # gestures that the spacing rule then argues about.
    song = [
        LineFacts(
            words=4,
            start_ms=0,
            end_ms=4000,
            word_starts=(0, 400, 800, 1200),
            norm_tokens=("music", "music", "", "music"),
        )
    ]
    cands = [tag(0, w, "music", 0.6) for w in (0, 1, 3)]
    got = select_fx_words(cands, song).words
    assert [t.word for t in got] == [0, 1, 3], "the fourth music belongs to the run"


def test_a_different_word_between_repeats_DOES_break_the_gesture():
    song = [
        LineFacts(
            words=4,
            start_ms=0,
            end_ms=4000,
            word_starts=(0, 400, 800, 1200),
            norm_tokens=("music", "loud", "music", "here"),
        )
    ]
    cands = [tag(0, 0, "music", 0.6), tag(0, 2, "music", 0.6)]
    got = select_fx_words(cands, song).words
    # Two separate gestures on a 4-word line: the quota is 1, so only one wins.
    assert len(got) == 1


def test_the_line_opening_word_loses_a_tie_but_can_still_win_alone():
    # Field note: an effect on the first word reads as jumping the gun.
    # A five-word line carries exactly one effect, so the two candidates
    # genuinely compete.
    tied = [
        LineFacts(
            words=5,
            start_ms=0,
            end_ms=5000,
            word_starts=tuple(w * 400 for w in range(5)),
            norm_tokens=tuple(VERSE[:5]),
        )
    ]
    contested = select_fx_words(
        [tag(0, 0, "music", 0.6), tag(0, 4, "dance", 0.6)], tied
    ).words
    assert [t.word for t in contested] == [4], "the later word wins a tie"

    alone = select_fx_words([tag(0, 0, "music", 0.6)], tied).words
    assert [t.word for t in alone] == [0], "a penalty, not a ban"


def test_a_chorus_heavy_song_still_leaves_the_verses_a_voice():
    """The class must not become the wallpaper the module exists to prevent.

    Numbers chosen so the reserve is the ONLY thing keeping the verses alive:
    40 lines of 4 s is a 160 s song, so the cadence allows 18 effects, and the
    chorus alone repeats 20 times. Without a floor, "spend the cap on the class
    first" leaves exactly zero for everything else — a song whose every effect
    is one repeated word, which is the complaint this module was written for.
    """
    chorus_lines = [
        LineFacts(
            words=len(CHORUS),
            start_ms=i * LINE_MS,
            end_ms=i * LINE_MS + LINE_MS - 200,
            word_starts=tuple(i * LINE_MS + w * WORD_MS for w in range(len(CHORUS))),
            norm_tokens=tuple(CHORUS),
        )
        for i in range(20)
    ]
    verse_lines = [
        LineFacts(
            words=len(VERSE),
            start_ms=(20 + i) * LINE_MS,
            end_ms=(20 + i) * LINE_MS + LINE_MS - 200,
            word_starts=tuple((20 + i) * LINE_MS + w * WORD_MS for w in range(len(VERSE))),
            norm_tokens=tuple(VERSE[:7] + [f"w{i}"]),  # each verse line unique
        )
        for i in range(20)
    ]
    song = chorus_lines + verse_lines
    cands = [tag(i, 4, "music", 0.6) for i in range(20)]
    cands += [tag(20 + i, 3, "love", 0.6) for i in range(20)]

    result = select_fx_words(cands, song)
    verses = [t for t in result.words if t.line >= 20]
    assert verses, "the verses are never silenced entirely"
    assert result.stats.kept_gestures <= result.stats.song_cap


def test_the_pattern_stays_identical_even_when_the_cap_thins_it():
    # Degrading gracefully means the chorus gets quieter, never inconsistent.
    # Spaced wide enough that the cadence can afford the class once thinned:
    # two words across 13 repeats is 26 gestures, one word is 13, and the cap
    # here is 15 — so thinning saves the class instead of the packing killing
    # it, which is the ladder this test is about.
    song = spread(13, CHORUS, every_ms=11_000)
    cands = [tag(i, 4, "music", 0.6) for i in range(13)]
    cands += [tag(i, 8, "music", 0.6) for i in range(13)]
    result = select_fx_words(cands, song)

    by_line: dict[int, set[int]] = {}
    for t in result.words:
        by_line.setdefault(t.line, set()).add(t.word)
    patterns = {frozenset(words) for words in by_line.values()}
    assert len(patterns) == 1, f"every firing repeat wears one pattern, got {patterns}"
    assert len(by_line) == 13, "thinning the pattern must save the class, not silence it"


def test_repeat_classes_are_deterministic_under_input_permutation():
    # `spread`, not `sung`: five repeats packed into 20 s give a cap of 4, the
    # class costs 5, and both sides would come back empty — a comparison of two
    # empty lists proves nothing about ordering.
    song = spread(5, CHORUS)
    cands = [tag(i, 4, "music", 0.6) for i in range(5)]
    got = select_fx_words(cands, song).words
    assert got, "the fixture must actually select something"
    assert got == select_fx_words(list(reversed(cands)), song).words


def test_the_affordable_class_fires_whole_when_both_cannot_fit():
    """Two choruses, room for one — and the one that gets in gets in WHOLE.

    This fixture used to assert proportional sharing: both classes survive,
    trimmed in proportion to their size. The field overruled it. Sharing meant
    "eleven repeats, six of them silent", and the verdict on that was that a
    line firing on one repeat and not the next reads as broken, not as quiet.
    So the budget now buys whole classes: the six-repeat hook fires all six
    times, the eleven-repeat one says nothing at all, and the cadence the dead
    class was holding goes back to the verses.
    """
    big = [
        LineFacts(
            words=len(CHORUS),
            start_ms=i * LINE_MS,
            end_ms=i * LINE_MS + LINE_MS - 200,
            word_starts=tuple(i * LINE_MS + w * WORD_MS for w in range(len(CHORUS))),
            norm_tokens=tuple(CHORUS),
        )
        for i in range(11)
    ]
    small = [
        LineFacts(
            words=len(VERSE),
            start_ms=(11 + i) * LINE_MS,
            end_ms=(11 + i) * LINE_MS + LINE_MS - 200,
            word_starts=tuple((11 + i) * LINE_MS + w * WORD_MS for w in range(len(VERSE))),
            norm_tokens=tuple(VERSE),
        )
        for i in range(6)
    ]
    singles = [
        LineFacts(
            words=6,
            start_ms=(17 + i) * LINE_MS,
            end_ms=(17 + i) * LINE_MS + LINE_MS - 200,
            word_starts=tuple((17 + i) * LINE_MS + w * WORD_MS for w in range(6)),
            norm_tokens=tuple(f"u{i}{w}" for w in range(6)),
        )
        for i in range(10)
    ]
    song = big + small + singles
    cands = [tag(i, 4, "music", 0.6) for i in range(11)]
    cands += [tag(11 + i, 3, "love", 0.6) for i in range(6)]
    cands += [tag(17 + i, 2, "fire", 0.8) for i in range(10)]

    result = select_fx_words(cands, song)
    assert result.stats.kept_gestures <= result.stats.song_cap, "the cap still binds"

    big_alive = len({t.line for t in result.words if t.line < 11})
    small_alive = len({t.line for t in result.words if 11 <= t.line < 17})
    assert (big_alive, small_alive) == (0, 6), (
        f"whole classes only: big={big_alive}/11 small={small_alive}/6"
    )
    assert result.stats.classes_kept == 1 and result.stats.classes_dropped == 1
    # The dead class's budget is not left on the table.
    singles_alive = len({t.line for t in result.words if t.line >= 17})
    assert singles_alive > math.ceil(result.stats.song_cap * NON_CLASS_RESERVE), (
        f"the killed class's cadence should return to the verses, got {singles_alive}"
    )


#: A chorus that insists on one word — the shape of the field case
#: ("Please don't stop the music (music, music, music, music)").
INSIST = ["please", "dont", "stop", "the", "music", "music", "music", "music", "music"]


def test_a_run_survives_the_cap_whole_or_not_at_all():
    """The field failure: not one run survived a capped song.

    Pattern thinning removed one WORD at a time, but the cap counts GESTURES
    and a repeated word is one gesture — so every removal freed exactly zero
    budget while dismantling the insistence the run exists to render. The loop
    ground each run down to a single word and only then gave up. Measured on
    the first refreshed document: `kept == kept_gestures == 22`, which can only
    happen if no run kept a second member.
    """
    chorus = [
        LineFacts(
            words=len(INSIST),
            start_ms=i * LINE_MS,
            end_ms=i * LINE_MS + LINE_MS - 200,
            word_starts=tuple(i * LINE_MS + w * WORD_MS for w in range(len(INSIST))),
            norm_tokens=tuple(INSIST),
        )
        for i in range(5)
    ]
    verses = [
        LineFacts(
            words=6,
            start_ms=(5 + i) * LINE_MS,
            end_ms=(5 + i) * LINE_MS + LINE_MS - 200,
            word_starts=tuple((5 + i) * LINE_MS + w * WORD_MS for w in range(6)),
            norm_tokens=tuple(f"v{i}{w}" for w in range(6)),
        )
        for i in range(17)
    ]
    song = chorus + verses
    # Every "music" of the run is a candidate, exactly as the tagger emits them.
    # Five repeats, so the class is affordable and the run's integrity — not
    # the all-or-nothing packing — is what this test measures.
    cands = [tag(li, w, "music", 0.6) for li in range(5) for w in range(4, 9)]
    cands += [tag(5 + i, 2, "fire", 0.8) for i in range(17)]

    result = select_fx_words(cands, song)
    assert result.stats.kept_gestures <= result.stats.song_cap, "the cap still binds"
    assert result.stats.dropped_cap > 0, "fixture must actually reach the cap"

    fired = {li: sorted(t.word for t in result.words if t.line == li) for li in range(5)}
    alive = {li: words for li, words in fired.items() if words}
    assert alive, "the chorus may not be silenced outright"
    for li, words in alive.items():
        assert words == [4, 5, 6, 7, 8], (
            f"line {li} kept a mutilated run {words}: a run is one gesture and "
            "must be kept whole or dropped whole"
        )


def test_the_chorus_rescue_trades_a_whole_gesture_for_a_whole_gesture():
    """The guarantee was word-accounted while the cap counts gestures.

    Invisible until runs began surviving: it reinserted a run's FIRST member
    only (leaving that repeat firing a different pattern from its siblings) and
    paid with a single word, which frees no budget when that word belongs to a
    run — so the song drifted past its own cadence. Measured on the refreshed
    Rihanna document: 26 gestures against a cap of 24, and one repeat firing
    (4,) beside siblings firing (4,5,6,7,8).

    Built on a UNIQUE line rather than a repeat: class lines are off limits to
    the rescue now (it may neither resurrect nor evict one), so the whole-gesture
    trade has to be proved where the rescue can still reach — a one-off line
    that happens to insist on a word.
    """
    chant = ("hey", "now", "you", "go", "go", "go", "tonight")
    verses = [
        LineFacts(
            words=6,
            start_ms=i * LINE_MS,
            end_ms=i * LINE_MS + LINE_MS - 200,
            word_starts=tuple(i * LINE_MS + w * WORD_MS for w in range(6)),
            norm_tokens=tuple(f"w{i}{w}" for w in range(6)),
        )
        for i in range(20)
    ]
    hook = [
        LineFacts(
            words=len(chant),
            start_ms=20 * LINE_MS,
            end_ms=20 * LINE_MS + LINE_MS - 200,
            word_starts=tuple(20 * LINE_MS + w * WORD_MS for w in range(len(chant))),
            norm_tokens=chant,
        )
    ]
    song = verses + hook
    cands = [tag(i, 2, "fire", 0.8) for i in range(20)]
    # "go, go, go" — one gesture, three words.
    cands += [tag(20, w, "music", 0.4) for w in (3, 4, 5)]
    # The hook is the last line, which the positional stride drops; the section
    # over it is then silent and the rescue must buy it back.
    sections = [Section(type="chorus", start_ms=20 * LINE_MS, end_ms=21 * LINE_MS)]

    result = select_fx_words(cands, song, sections)
    assert result.stats.guard_reinserted > 0, "fixture must exercise the rescue"
    assert result.stats.kept_gestures <= result.stats.song_cap, (
        f"the rescue blew the cadence: {result.stats.kept_gestures} > "
        f"{result.stats.song_cap}"
    )
    assert sorted(t.word for t in result.words if t.line == 20) == [3, 4, 5], (
        "a rescued line must carry the whole run, not its first member"
    )


def test_one_gesture_never_outruns_the_client_belt():
    """`MAX_RUN_WORDS` documents a per-GESTURE limit; enforce it as one.

    The cap was applied to the line's total instead, so a single long run could
    slip past it — nine "yeah"s on a thirteen-word line is one run under a total
    of `MAX_RUN_WORDS * quota`. The overlay's own belt would then trim the tail,
    which means the server would be planning one thing and the screen showing
    another.
    """
    chant = ["yeah"] * 13
    song = [
        LineFacts(
            words=len(chant),
            start_ms=i * LINE_MS,
            end_ms=i * LINE_MS + LINE_MS - 200,
            word_starts=tuple(i * LINE_MS + w * WORD_MS for w in range(len(chant))),
            norm_tokens=tuple(chant),
        )
        for i in range(4)
    ]
    cands = [tag(li, w, "music", 0.6) for li in range(4) for w in range(len(chant))]

    result = select_fx_words(cands, song)
    for li in range(4):
        words = [t.word for t in result.words if t.line == li]
        assert len(words) <= MAX_RUN_WORDS, (
            f"line {li} emitted {len(words)} words for one gesture"
        )


def _fired_per_class(result, groups: dict[str, range]) -> dict[str, int]:
    """How many repeats of each named class actually fire."""
    firing = {t.line for t in result.words}
    return {name: len(set(rows) & firing) for name, rows in groups.items()}


def _class_lines(count: int, tokens: list[str], start_at: int) -> list[LineFacts]:
    """`count` identical lines beginning at `start_at`, one per LINE_MS slot."""
    return [
        LineFacts(
            words=len(tokens),
            start_ms=(start_at + i) * LINE_MS,
            end_ms=(start_at + i) * LINE_MS + LINE_MS - 200,
            word_starts=tuple(
                (start_at + i) * LINE_MS + w * WORD_MS for w in range(len(tokens))
            ),
            norm_tokens=tuple(tokens),
        )
        for i in range(count)
    ]


def _unique_lines(count: int, start_at: int, mark: str = "u") -> list[LineFacts]:
    """`count` lines that share no wording with anything — never a class."""
    return [
        LineFacts(
            words=6,
            start_ms=(start_at + i) * LINE_MS,
            end_ms=(start_at + i) * LINE_MS + LINE_MS - 200,
            word_starts=tuple((start_at + i) * LINE_MS + w * WORD_MS for w in range(6)),
            norm_tokens=tuple(f"{mark}{i}{w}" for w in range(6)),
        )
        for i in range(count)
    ]


def test_a_class_the_budget_cannot_afford_goes_entirely_silent():
    """The decision this rule exists to enforce, in its plainest form.

    Twenty repeats against a budget that cannot seat them: the old policy would
    have seated as many as fitted and left the rest silent, which is what the
    field called broken. Now the hook says nothing at all, and the cadence it
    was holding is spent on the verses instead.
    """
    song = _class_lines(20, CHORUS, 0) + _unique_lines(20, 20)
    cands = [tag(i, 4, "music", 0.6) for i in range(20)]
    cands += [tag(20 + i, 2, "fire", 0.8) for i in range(20)]

    result = select_fx_words(cands, song)
    assert not [t for t in result.words if t.line < 20], "the whole class must fall silent"
    assert result.stats.classes_dropped == 1
    assert result.stats.classes_kept == 0
    assert result.words, "the verses must still speak"


def test_partial_firing_is_impossible_under_any_pressure():
    """The invariant, swept rather than sampled.

    Class sizes and song lengths chosen to walk the cap from its floor to its
    ceiling, so the packing, the pattern thinning and the reserve all take a
    turn at being the binding constraint.
    """
    for class_size in (2, 5, 9, 14):
        for gap_ms in (2500, LINE_MS, 12_000):
            chorus = [
                LineFacts(
                    words=len(CHORUS),
                    start_ms=i * gap_ms,
                    end_ms=i * gap_ms + 2000,
                    word_starts=tuple(i * gap_ms + w * WORD_MS for w in range(len(CHORUS))),
                    norm_tokens=tuple(CHORUS),
                )
                for i in range(class_size)
            ]
            verses = _unique_lines(10, class_size + 2)
            song = chorus + verses
            cands = [tag(i, 4, "music", 0.6) for i in range(class_size)]
            cands += [tag(len(chorus) + i, 2, "fire", 0.8) for i in range(10)]

            result = select_fx_words(cands, song)
            firing = {t.line for t in result.words if t.line < class_size}
            assert len(firing) in (0, class_size), (
                f"class_size={class_size} gap={gap_ms}: {len(firing)} of "
                f"{class_size} repeats fired — a class fires all or none"
            )


def test_first_fit_lets_small_classes_in_when_the_giant_does_not_fit():
    """One oversized chant must not starve the hooks that would have fitted.

    First-fit, not first-fail-stop: the giant is skipped and packing carries on
    down the list.
    """
    song = (
        _class_lines(15, CHORUS, 0)
        + _class_lines(4, VERSE, 15)
        + _class_lines(3, ["kilo", "papa", "romeo", "sierra"], 19)
        + _unique_lines(8, 22)
    )
    cands = [tag(i, 4, "music", 0.6) for i in range(15)]
    cands += [tag(15 + i, 3, "love", 0.6) for i in range(4)]
    cands += [tag(19 + i, 1, "fire", 0.8) for i in range(3)]
    cands += [tag(22 + i, 2, "dance", 0.6) for i in range(8)]

    result = select_fx_words(cands, song)
    fired = _fired_per_class(
        result, {"giant": range(0, 15), "mid": range(15, 19), "small": range(19, 22)}
    )
    assert fired["giant"] == 0, "the giant cannot be afforded"
    assert fired["mid"] == 4 and fired["small"] == 3, (
        f"smaller classes must still get in: {fired}"
    )


def test_the_rescue_never_resurrects_a_killed_class():
    """The rescue may not undo the packing's decision one repeat at a time.

    Its candidate pool is the full tagged list, not the kept one, so without an
    explicit rule it would happily light up a single repeat of a class that was
    silenced precisely so it would not flicker.
    """
    song = _class_lines(16, CHORUS, 0) + _unique_lines(12, 16)
    cands = [tag(i, 4, "music", 0.6) for i in range(16)]
    cands += [tag(16 + i, 2, "fire", 0.8) for i in range(12)]
    # A chorus section over each repeat: every one of them is a silent section
    # the rescue would otherwise want to buy back.
    sections = [
        Section(type="chorus", start_ms=i * LINE_MS, end_ms=(i + 1) * LINE_MS)
        for i in range(16)
    ]

    result = select_fx_words(cands, song, sections)
    assert not [t for t in result.words if t.line < 16], "the killed class stays killed"
    assert result.stats.guard_reinserted == 0


def test_the_rescue_never_evicts_a_repeat_of_a_class_that_fires():
    """The other direction: a class may not PAY for someone else's rescue.

    Evicting one repeat as payment would leave the class firing everywhere but
    there — the same flicker, arrived at from the opposite side.
    """
    song = _class_lines(4, CHORUS, 0) + _unique_lines(24, 4)
    cands = [tag(i, 4, "music", 0.6) for i in range(4)]
    cands += [tag(4 + i, 2, "fire", 0.8) for i in range(24)]
    # The last verse line is its own chorus section and will be strided away,
    # so the rescue has to find payment somewhere.
    sections = [Section(type="chorus", start_ms=27 * LINE_MS, end_ms=28 * LINE_MS)]

    result = select_fx_words(cands, song, sections)
    fired = len({t.line for t in result.words if t.line < 4})
    assert fired in (0, 4), f"the class fired {fired} of 4 repeats"


def test_budget_freed_by_a_killed_class_returns_to_the_verses():
    """A silent chorus must not also cost the verses their cadence.

    The singles are trimmed against the FULL class weight, so leaving them at
    the reserve floor after the class dies would spend the dead hook's budget
    on nothing at all.
    """
    song = _class_lines(18, CHORUS, 0) + _unique_lines(18, 18)
    cands = [tag(i, 4, "music", 0.6) for i in range(18)]
    cands += [tag(18 + i, 2, "fire", 0.8) for i in range(18)]

    result = select_fx_words(cands, song)
    verses = len({t.line for t in result.words if t.line >= 18})
    floor = math.ceil(result.stats.song_cap * NON_CLASS_RESERVE)
    assert verses > floor, (
        f"the verses stayed at the reserve floor ({verses} <= {floor}) while a "
        "killed class's budget went unspent"
    )


def test_the_singles_reserve_survives_the_packing():
    """A class that exactly fits must still leave the verses their reserve."""
    song = _class_lines(9, CHORUS, 0) + _unique_lines(20, 9)
    cands = [tag(i, 4, "music", 0.6) for i in range(9)]
    cands += [tag(9 + i, 2, "fire", 0.8) for i in range(20)]

    result = select_fx_words(cands, song)
    verses = len({t.line for t in result.words if t.line >= 9})
    assert verses >= math.ceil(result.stats.song_cap * NON_CLASS_RESERVE)


def test_a_stronger_class_wins_the_budget_when_only_one_fits():
    """Same size, different standing: the chorus one outranks the other."""
    song = _class_lines(6, CHORUS, 0) + _class_lines(6, VERSE, 6) + _unique_lines(6, 12)
    cands = [tag(i, 4, "music", 0.6) for i in range(6)]
    cands += [tag(6 + i, 3, "music", 0.6) for i in range(6)]
    cands += [tag(12 + i, 2, "fire", 0.8) for i in range(6)]
    sections = [Section(type="chorus", start_ms=6 * LINE_MS, end_ms=12 * LINE_MS)]

    result = select_fx_words(cands, song, sections)
    fired = _fired_per_class(result, {"plain": range(0, 6), "ranked": range(6, 12)})
    assert fired["ranked"] == 6 and fired["plain"] == 0, (
        f"the ranked class should take the budget: {fired}"
    )


def test_packing_is_deterministic_under_input_permutation():
    song = _class_lines(12, CHORUS, 0) + _class_lines(5, VERSE, 12) + _unique_lines(8, 17)
    cands = [tag(i, 4, "music", 0.6) for i in range(12)]
    cands += [tag(12 + i, 3, "love", 0.6) for i in range(5)]
    cands += [tag(17 + i, 2, "fire", 0.8) for i in range(8)]

    forward = select_fx_words(cands, song)
    backward = select_fx_words(list(reversed(cands)), song)
    assert forward.words == backward.words
    assert forward.stats.classes_kept == backward.stats.classes_kept
    assert forward.stats.classes_kept >= 1, "the fixture must seat something"


def test_a_song_that_is_only_an_unaffordable_chorus_goes_silent():
    """The accepted cost, written down so nobody rediscovers it as a bug.

    Cap holds, no partial firing: a short track that is nothing but a chant too
    long to seat therefore carries no effects at all.
    """
    song = _class_lines(6, CHORUS, 0)
    cands = [tag(i, 4, "music", 0.6) for i in range(6)]

    result = select_fx_words(cands, song)
    assert result.words == []
    assert result.stats.classes_dropped == 1
    assert result.plan == SELECT_PLAN


def test_stats_report_which_classes_survived():
    song = _class_lines(16, CHORUS, 0) + _class_lines(3, VERSE, 16) + _unique_lines(8, 19)
    cands = [tag(i, 4, "music", 0.6) for i in range(16)]
    cands += [tag(16 + i, 3, "love", 0.6) for i in range(3)]
    cands += [tag(19 + i, 2, "fire", 0.8) for i in range(8)]

    result = select_fx_words(cands, song)
    assert result.stats.repeat_classes == 2
    assert (result.stats.classes_kept, result.stats.classes_dropped) == (1, 1)
