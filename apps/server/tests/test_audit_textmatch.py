"""audit.textmatch: placing a line by its letters in the ear's letter stream."""

from audit import finder, textmatch
from audit.finder import text_events
from audit.textmatch import ear_stream, letters, locate, partial_similarity, sung_span


def _stream(text, t0, step=50):
    """Ear tokens: one letter per token, `step` ms apart, words split by '|'."""
    toks, t = [], t0
    for ch in text:
        toks.append({"text": "|" if ch == " " else ch, "start_ms": t, "end_ms": t + 20})
        t += step
    return toks


def test_thresholds_are_the_calibrated_numbers():
    assert (textmatch.MIN_LETTERS, textmatch.MIN_RATIO, textmatch.MIN_MARGIN) == (12, 0.55, 0.10)
    assert (textmatch.SEARCH_MS, textmatch.AMBIGUITY_MS, textmatch.ANCHOR_BLOCK) == (8000, 1500, 3)
    assert textmatch.DEFAULT_MS_PER_LETTER == 90
    assert (textmatch.MIN_MS_PER_LETTER, textmatch.MAX_MS_PER_LETTER) == (50, 150)


def test_letters_keeps_lowercase_letters_only():
    assert letters("I'll do (anything), ya!") == "illdoanythingya"


def test_locate_finds_the_line_where_its_letters_are_heard():
    noise = "xq zv kw pj " * 8
    toks = _stream(noise + "you dont even know me" + " zq" * 20, 10_000)
    stream, times = ear_stream(toks)
    start_of_line = 10_000 + 50 * len(noise)
    m = locate("You don't even know me", 14_000, stream, times)
    assert m is not None and m.heard_ms == start_of_line


def test_the_heard_time_is_the_first_matching_run_not_the_window_start():
    toks = _stream("zz he doesnt love me so i tell myself", 20_000)
    stream, times = ear_stream(toks)
    m = locate("he doesnt love me so i tell myself", 20_000, stream, times)
    assert m is not None and m.heard_ms == 20_000 + 50 * 3   # the 'h', not a 'z'


def test_short_lines_and_repeats_are_not_placed():
    stream, times = ear_stream(_stream("zq but my love qz", 0))
    assert locate("But my love", 0, stream, times) is None          # 9 letters < 12
    stream, times = ear_stream(_stream("zq but my love you qz", 0))
    assert locate("But my love you", 0, stream, times) is not None  # 12 letters
    twice = _stream("got me looking so crazy" + " qq" * 20 + " got me looking so crazy", 0)
    stream, times = ear_stream(twice)
    assert locate("Got me looking so crazy", 0, stream, times) is None  # two equal homes


def test_text_events_report_lines_heard_more_than_a_second_away():
    toks = _stream("qz " * 40 + "mi alone a give you the remedy", 0)
    heard = 50 * 120
    ear = {"tokens": toks}
    late = {"lines": [{"text": "Mi alone a give you the remedy", "start_ms": heard + 1001,
                        "end_ms": heard + 3000}]}
    assert [e.tag for e in text_events(late, "v", ear)] == ["line_offset"]
    near = {"lines": [{"text": "Mi alone a give you the remedy", "start_ms": heard + 1000,
                        "end_ms": heard + 3000}]}
    assert text_events(near, "v", ear) == []


def test_new_rules_l27_is_placed_at_its_first_matching_letters():
    # The whole-song ear around New Rules 1:31-1:40 (2026-10-31), one token per
    # heard word. An early window ("amoma e doesnt...") scores best; the line is
    # heard from "e doesnt" at 95.8 s; one letter ('h') back at the default
    # rate is 95.71 - exactly where the document has it.
    words = [("never", 90700), ("amoma", 91300), ("e", 95800), ("doesnt", 96000),
             ("love", 96300), ("me", 96600), ("so", 96900), ("ats", 97200),
             ("myself", 97500), ("ase", 99200), ("myself", 99500)]
    toks = [{"text": w, "start_ms": t, "end_ms": t + 100} for w, t in words]
    stream, times = ear_stream(toks)
    m = locate("He doesn't love me, so I tell myself, I tell myself", 95710, stream, times)
    assert m is not None and m.heard_ms == 95800 - textmatch.DEFAULT_MS_PER_LETTER


def test_a_misheard_line_start_does_not_read_late():
    # New Rules L1 (2026-10-31 ear): "Talkin' in my sleep at night, makin' myself
    # crazy" is heard as "saken you must sleep..." from 8.94 s; the first run of
    # 3+ matching letters is in "sleep" (9.96). Stepping back by its place in
    # the line at the line's own letter rate lands on the start, not +1 s.
    words = [("saken", 8940), ("you", 9480), ("must", 9720), ("sleep", 9960),
             ("amakemyself", 10340), ("crazy", 12020)]
    toks = [{"text": w, "start_ms": t, "end_ms": t + 100} for w, t in words]
    stream, times = ear_stream(toks)
    text = "Talkin' in my sleep at night, makin' myself crazy"
    m = locate(text, 8910, stream, times, line_ms=12600 - 8910)
    assert m is not None and abs(m.heard_ms - 8910) < 300
    rate = (12600 - 8910) / len(letters(text))
    assert m.heard_ms == 9960 - round(letters(text).index("sle") * rate)


def test_the_letter_rate_is_clamped_and_taken_from_the_words():
    held = {"start_ms": 0, "end_ms": 4600, "words": [
        {"text": "a", "start_ms": 0, "end_ms": 100},
        {"text": "b", "start_ms": 1410, "end_ms": 4600},
    ]}
    assert sung_span(held) == 1410
    assert sung_span({"start_ms": 0, "end_ms": 900, "text": "x"}) == 900
    toks = [{"text": "zzzz", "start_ms": 0, "end_ms": 10},
            {"text": "llotheremynewrules", "start_ms": 5000, "end_ms": 5010}]
    stream, times = ear_stream(toks)
    # first 3+ run "llo" is 2 letters into "hellotheremynewrules": step back 2 x rate
    slow = locate("Hello there my new rules", 5000, stream, times, line_ms=100_000)
    fast = locate("Hello there my new rules", 5000, stream, times, line_ms=1)
    assert slow is not None and slow.heard_ms == 5000 - 2 * 150
    assert fast is not None and fast.heard_ms == 5000 - 2 * 50


def test_a_placement_on_an_alike_neighbour_heard_in_place_is_that_neighbour():
    assert (finder.NEIGHBOUR_SIMILARITY, finder.NEIGHBOUR_SLACK_MS) == (0.4, 1000)
    lines = [
        {"text": "Most incredibly, it's ya girl, B", "start_ms": 6080, "end_ms": 8600},
        {"text": "It's ya boy, Young", "start_ms": 10420, "end_ms": 12000},
        {"text": "You ready?", "start_ms": 12760, "end_ms": 13620},
    ]
    owned = finder.heard_on_owned_neighbour
    # Crazy In Love L3, heard at 8.68 (2026-10-31); L2 is heard in its own place
    assert owned(lines, {0: 20, 1: -1740}, 1, 8680)
    assert owned(lines, {0: 1000, 1: -1740}, 1, 9600)     # 1000 counts as in place; slack inclusive
    assert not owned(lines, {0: 20, 1: -1740}, 1, 9601)
    assert not owned(lines, {0: 1001, 1: -1740}, 1, 8680)  # L2 itself shifted: a real shift
    assert not owned(lines, {1: -1740}, 1, 8680)           # L2 not placed: no owner
    assert not owned(lines, {2: 0, 1: 2000}, 1, 13000)     # "You ready?" is unlike it


def test_partial_similarity_scores_the_overlap():
    assert partial_similarity("abc", "xxabcxx") == 1.0
    assert partial_similarity("xxabcxx", "abc") == 1.0
    assert partial_similarity("", "abc") == 0.0
    assert round(partial_similarity("itsyaboyyoung", "mostincrediblyitsyagirlb"), 3) >= 0.4
    assert partial_similarity("itsyaboyyoung", "youready") < 0.4
