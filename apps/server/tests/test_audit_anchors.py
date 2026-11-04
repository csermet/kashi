"""audit.anchors (Faz 3.5 A1-A3) on synthetic songs: invented words, a perfect
greedy ear (tests/audit_synth.py). Parameters pinned as numbers; every rule on
both sides of its boundary."""

import audit_synth as syn
import pytest

from audit import anchors, gates, inject
from audit.anchors import Anchor, SheetLine, check, sections, unique_lines


@pytest.fixture(scope="module")
def healthy():
    texts, true, ear, total = syn.song(1, 16)
    return texts, true, ear, total


def test_method_parameters_are_pinned():
    assert (anchors.UNIQUE_MAX_SIMILARITY, anchors.SECTION_TOL_MS, anchors.SECTION_RECENT,
            anchors.MIN_SECTION_ANCHORS) == (0.6, 1000, 3, 2)
    assert anchors.SHIFT_FLAG_MS == gates.PILOT_SHIFT_MS == 1000
    assert (anchors.ACTIVITY_BIN_MS, anchors.ACTIVITY_MAX_OFFSET_MS, anchors.ACTIVITY_MIN_NCC) == \
        (100, 30_000, 0.8)
    assert anchors.ACTIVITY_STRETCHES == (0.95, 0.96, 0.97, 0.98, 0.99, 1.0, 1.01, 1.02, 1.03,
                                          1.04, 1.05)
    assert (anchors.SUNG_PROB, anchors.SPAN_SLACK_MS, anchors.BLOCK_GAP_MS,
            anchors.BLOCK_MIN_MS, anchors.READABLE_LETTERS_PER_S, anchors.UNSUNG_RUN) == \
        (0.5, 500, 1000, 3000, 4.0, 3)
    assert anchors.ALARMS == {"shift", "wrong_sheet"}


# --- A1 ---------------------------------------------------------------------------

def test_a_unique_line_shares_less_than_sixty_percent_with_every_other():
    a = "abcdefghijklmno"
    at = "abcdefghi" + "pqrstu"       # 9 of 15 letters: ratio 0.6 exactly -> not unique
    below = "abcdefgh" + "pqrstuv"    # 8 of 15: 0.533 -> unique
    assert unique_lines([SheetLine(a, 0), SheetLine(at, 1)]) == []
    assert unique_lines([SheetLine(a, 0), SheetLine(below, 1)]) == [0, 1]


def test_refrains_and_short_lines_are_never_anchors():
    long_ = "zero one two three four"
    sheet = [SheetLine(long_, 0), SheetLine("oh yeah", 1), SheetLine(long_, 2),
             SheetLine("five six seven eight", 3)]
    assert unique_lines(sheet) == [3]


def test_a_healthy_sheet_reads_zero_and_raises_nothing(healthy):
    texts, true, ear, _ = healthy
    r = check(syn.sheet(texts, true), ear)
    assert r["flags"] == [] and r["shift_kind"] is None
    assert len(r["anchors"]) == r["anchor_candidates"] == 16
    assert [(s["first_line"], s["last_line"], s["median_ms"]) for s in r["sections"]] == \
        [(0, 15, 0)]
    assert r["activity"]["offset_ms"] == 0 and r["activity"]["confident"]
    assert r["coverage"]["unmatched_sung_share"] == 0.0


def test_a_global_shift_is_flagged_with_its_size(healthy):
    texts, true, ear, _ = healthy
    r = check(inject.shift_all(syn.sheet(texts, true), 10_000), ear)
    assert r["flags"] == ["shift"] and r["shift_kind"] == "global"
    assert r["sections"][0]["median_ms"] == -10_000      # the sheet is 10 s late
    assert r["activity"]["offset_ms"] == -10_000


def test_the_shift_flag_is_strictly_beyond_one_second(healthy):
    texts, true, ear, _ = healthy
    base = syn.sheet(texts, true)
    assert check(inject.shift_all(base, 1000), ear)["flags"] == []
    assert check(inject.shift_all(base, 1001), ear)["flags"] == ["shift"]
    assert check(inject.shift_all(base, -1001), ear)["flags"] == ["shift"]


def test_a_shifted_verse_is_its_own_section(healthy):
    texts, true, ear, _ = healthy
    r = check(inject.shift_block(syn.sheet(texts, true), (5, 8), 6000), ear)
    assert r["shift_kind"] == "section"
    assert [(s["first_line"], s["last_line"], s["median_ms"], s["shifted"])
            for s in r["sections"]] == [(0, 4, 0, False), (5, 8, -6000, True),
                                        (9, 15, 0, False)]


def test_drift_is_flagged_and_measured(healthy):
    texts, true, ear, _ = healthy
    r = check(inject.drift(syn.sheet(texts, true), 10_000), ear)
    assert "shift" in r["flags"]
    assert r["drift_ms"] is not None and -10_500 < r["drift_ms"] < -9000


def _a(line, delta):
    return Anchor(line, line * 1000, line * 1000 + delta, delta, 0.9, 0.3)


def test_sections_need_two_agreeing_anchors_and_skip_a_lone_outlier():
    secs, out = sections([_a(0, 0), _a(1, 40), _a(2, 9000), _a(3, 20), _a(4, -30)])
    assert [(s.first_line, s.last_line, s.anchors) for s in secs] == [(0, 4, 4)]
    assert [a.line for a in out] == [2]
    # two outliers in a row that agree open the next section
    secs, _ = sections([_a(0, 0), _a(1, 0), _a(2, 9000), _a(3, 9100), _a(4, 9050)])
    assert [(s.first_line, s.last_line, s.median_ms) for s in secs] == [(0, 1, 0), (2, 4, 9050)]
    # a lone anchor is never a section
    assert sections([_a(0, 5000)]) == ([], [_a(0, 5000)])


def test_section_tolerance_is_inclusive():
    assert len(sections([_a(0, 0), _a(1, 1000)])[0]) == 1
    assert sections([_a(0, 0), _a(1, 1001)])[0] == []
    # against the median of the last three, not the first anchor: an anchor sits
    # two steps past that median, so a drift of 500 ms per line stays one section
    secs, _ = sections([_a(k, 500 * k) for k in range(8)])
    assert len(secs) == 1 and secs[0].anchors == 8
    assert len(sections([_a(k, 501 * k) for k in range(8)])[0]) > 1


# --- A2 ---------------------------------------------------------------------------

def test_with_no_unique_line_the_activity_fit_stands_in(healthy):
    texts, true, _, total = healthy
    doubled = [t for t in texts[:8] for _ in (0, 1)]
    ear = syn.ear(list(zip(doubled, true, strict=True)), total)
    sheet = syn.sheet(doubled, true)
    assert check(sheet, ear)["flags"] == []
    r = check(inject.shift_all(sheet, 5000), ear)
    assert r["flags"] == ["shift"] and r["shift_kind"] == "activity"
    assert r["activity"]["offset_ms"] == -5000 and r["activity"]["ncc"] >= 0.8


def test_an_unconfident_activity_fit_flags_nothing(healthy, monkeypatch):
    texts, true, _, total = healthy
    doubled = [t for t in texts[:8] for _ in (0, 1)]
    ear = syn.ear(list(zip(doubled, true, strict=True)), total)
    shifted = inject.shift_all(syn.sheet(doubled, true), 5000)
    ncc = check(shifted, ear)["activity"]["ncc"]
    monkeypatch.setattr(anchors, "ACTIVITY_MIN_NCC", ncc)
    assert check(shifted, ear)["flags"] == ["shift"]          # the bar itself is accepted
    monkeypatch.setattr(anchors, "ACTIVITY_MIN_NCC", ncc + 0.001)
    assert check(shifted, ear)["flags"] == []


def test_a1_sections_overrule_the_activity_fit(healthy, monkeypatch):
    texts, true, ear, _ = healthy
    monkeypatch.setattr(anchors, "activity_fit", lambda sheet, ear: {
        "offset_ms": -5000, "stretch": 1.0, "ncc": 0.95, "ncc_at_zero": 0.1, "confident": True})
    assert check(syn.sheet(texts, true), ear)["flags"] == []


def test_expected_spans_stop_at_the_next_stamp():
    spans = anchors.expected_spans([SheetLine("a" * 40, 0), SheetLine("b" * 12, 1000)])
    assert spans == [(0, 1000), (1000, 1000 + 12 * 90)]
    assert anchors.expected_spans([SheetLine("a" * 10, 1000)], 1.05) == [(1050, 1050 + 900)]


# --- A3 ---------------------------------------------------------------------------

def test_the_wrong_sheet_signature_needs_readable_unmatched_singing(healthy):
    texts, true, _, total = healthy
    other = syn.lines(7, 6)
    sung = list(zip(texts[:10], true[:10], strict=True)) + \
        list(zip(other, true[10:16], strict=True))
    r = check(syn.sheet(texts, true), syn.ear(sung, total))
    assert "wrong_sheet" in r["flags"]
    cov = r["coverage"]
    assert cov["unmatched_lines_share"] >= 0.25 and cov["unmatched_sung_share"] >= 0.25
    # the same singing as a processed vocal the model cannot spell: no wrong sheet
    ear = syn.ear(list(zip(texts[:10], true[:10], strict=True)), total)
    mute = syn.ear(list(zip(other, true[10:16], strict=True)), total, readable=False)
    ear["letter_prob"] = [max(a, b) for a, b in zip(ear["letter_prob"], mute["letter_prob"],
                                                     strict=True)]
    r = check(syn.sheet(texts, true), ear)
    assert "wrong_sheet" not in r["flags"] and r["coverage"]["readable_blocks"] == 0


def test_wrong_sheet_shares_are_inclusive(healthy, monkeypatch):
    texts, true, _, total = healthy
    other = syn.lines(7, 6)
    sung = list(zip(texts[:10], true[:10], strict=True)) + \
        list(zip(other, true[10:16], strict=True))
    ear = syn.ear(sung, total)
    cov = check(syn.sheet(texts, true), ear)["coverage"]
    monkeypatch.setattr(anchors, "WRONG_SHEET_UNMATCHED_LINES", cov["unmatched_lines_share"])
    monkeypatch.setattr(anchors, "WRONG_SHEET_UNMATCHED_SUNG", cov["unmatched_sung_share"])
    assert "wrong_sheet" in check(syn.sheet(texts, true), ear)["flags"]
    monkeypatch.setattr(anchors, "WRONG_SHEET_UNMATCHED_LINES",
                        cov["unmatched_lines_share"] + 0.001)
    assert "wrong_sheet" not in check(syn.sheet(texts, true), ear)["flags"]


def test_a_deleted_verse_is_unsheeted_singing_an_extra_one_unsung_lines(healthy):
    texts, true, ear, _ = healthy
    base = syn.sheet(texts, true)
    deleted = check(inject.delete_block(base, (5, 8)), ear)
    assert deleted["flags"] == ["unsheeted_singing"]
    block = deleted["coverage"]["blocks"][0]
    assert block["readable"] and block["start_ms"] < true[5] and block["end_ms"] > true[8]
    extra = check(inject.append_lines(base, syn.lines(9, 4)), ear)
    assert extra["flags"] == ["unsung_lines"]
    assert extra["coverage"]["longest_unmatched_run"] == 4


def test_unsung_lines_need_three_in_a_row(healthy):
    texts, true, ear, _ = healthy
    base = syn.sheet(texts, true)
    assert "unsung_lines" not in check(inject.append_lines(base, syn.lines(9, 2)), ear)["flags"]
    assert "unsung_lines" in check(inject.append_lines(base, syn.lines(9, 3)), ear)["flags"]
    # three unsung lines that are not neighbours are no run
    strangers = syn.lines(9, 3)
    spread = list(base)
    for k, line in zip((2, 6, 10), strangers, strict=True):
        spread[k] = SheetLine(line, base[k].start_ms)
    cov = check(spread, ear)["coverage"]
    assert sorted(cov["unmatched_lines"]) == [2, 6, 10] and cov["longest_unmatched_run"] == 1


def test_a_line_heard_at_exactly_the_matchers_ratio_is_heard():
    target = "abcdefghijklmnopqrst"        # 20 letters; a window sharing k of them: 2k/40
    def heard(k):
        tokens = [{"text": ch, "start_ms": 10_000 + 90 * n + anchors.EAR_LAG_MS}
                  for n, ch in enumerate(target[:k] + "z" * (60 - k))]
        ear = {"tokens": tokens, "letter_prob": [], "stride_ms": 20.0, "start_ms": 0}
        return anchors.coverage([SheetLine(target, 10_000)], ear, [10_000])["lines_matched"]
    assert heard(11) == 1 and heard(10) == 0             # 0.55 = textmatch.MIN_RATIO


def test_a_refrain_sung_twice_close_together_is_heard_where_it_is(healthy):
    """Coverage asks 'is it heard near here', not 'is it unambiguous': a line
    sung twice 2.5 s apart is heard in both places."""
    texts, true, _, total = healthy
    lines_ = list(texts)
    lines_[6] = lines_[5]
    ear = syn.ear(list(zip(lines_, true, strict=True)), total)
    cov = check(syn.sheet(lines_, true), ear)["coverage"]
    assert cov["unmatched_lines"] == [] and cov["unmatched_sung_share"] == 0.0


def test_blocks_merge_gaps_up_to_a_second_and_need_three():
    times = list(range(0, 1500, 20)) + list(range(2500, 3000, 20))   # gap of 1000+20
    ear = {"tokens": [], "letter_prob": [], "stride_ms": 20.0}
    assert anchors.unmatched_blocks(times, 20.0, ear) == []
    times = list(range(0, 1500, 20)) + list(range(2480, 3000, 20))   # gap 1000: merges
    (block,) = anchors.unmatched_blocks(times, 20.0, ear)
    assert (block["start_ms"], block["end_ms"]) == (0, 3000)


def test_sheets_from_a_document_and_a_record():
    doc = {"lines": [{"text": "a b", "start_ms": 900, "end_ms": 2000,
                      "words": [{"text": "a", "start_ms": 1000, "end_ms": 1100}]},
                     {"text": "plain", "start_ms": 3000, "end_ms": 4000}]}
    assert anchors.sheet_from_document(doc) == [SheetLine("a b", 1000), SheetLine("plain", 3000)]
    record = {"id": 1, "duration": 30.0, "syncedLyrics": "[00:01.50] first line\n[00:04.00] second"}
    assert anchors.sheet_from_record(record) == [SheetLine("first line", 1500),
                                                 SheetLine("second", 4000)]


def test_trigram_cosine():
    assert anchors.trigram_cosine("abcabc", "abcabc") == 1.0
    assert anchors.trigram_cosine("abcdef", "uvwxyz") == 0.0
    assert anchors.trigram_cosine("ab", "abc") == 0.0


def test_an_anchor_needs_twelve_letters():
    assert unique_lines([SheetLine("abcdefghijkl", 0), SheetLine("mnopqrstuvw", 1)]) == [0]


def test_a_frame_is_sung_from_half_a_probability():
    sheet = [SheetLine("abcdefghijklmn", 100_000)]       # heard nowhere in this ear
    def sung(p):
        ear = {"tokens": [], "letter_prob": [p] * 100, "stride_ms": 20.0, "start_ms": 0}
        return anchors.coverage(sheet, ear, [100_000])["sung_ms"]
    assert sung(0.5) == 2000 and sung(0.499) == 0


def test_a_block_is_readable_from_four_letters_a_second():
    times = list(range(0, 4000, 20))
    def readable(n_letters):
        tokens = [{"text": "a", "start_ms": 100 + 10 * k + anchors.EAR_LAG_MS}
                  for k in range(n_letters)]
        ear = {"tokens": tokens, "letter_prob": [], "stride_ms": 20.0}
        (block,) = anchors.unmatched_blocks(times, 20.0, ear)
        return block["letters_per_s"], block["readable"]
    assert readable(16) == (4.0, True)                    # 16 letters over 4.0 s
    assert readable(15) == (3.75, False)
