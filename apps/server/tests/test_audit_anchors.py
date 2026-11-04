"""audit.anchors: the lyrics sheet against the whole-song ear (A1-A3).

Synthetic songs only (pseudo-words, no real ids or lyrics). Every pre-registered
threshold is pinned as a number and every rule is tested on both sides of its
boundary."""

import json
import random

import pytest
from audit_synth import VID, document, ear, letter_tokens, lines_of, record, vocabulary

from audit import anchors
from audit.anchors import (
    Placement,
    block_verdict,
    check,
    coverage_rule,
    find_anchors,
    heard_words,
    parse_lrc,
    pool_sections,
    same_word,
    semiglobal_cer,
    sheet_from_document,
    sheet_from_record,
    sheet_words,
)

VOCAB = vocabulary(160)


def test_thresholds_are_the_pre_registered_numbers():
    assert (anchors.SHIFT_MS, anchors.LONG_MOVE_MS, anchors.LONG_MOVE_MIN_LINES) == (1000, 5000, 5)
    assert anchors.COVERAGE_SHARE == 0.25
    assert (anchors.MIN_ANCHOR_WORDS, anchors.NOISY_MIN_ANCHOR_WORDS) == (2, 5)
    assert (anchors.MIN_ANCHOR_LETTERS, anchors.WORD_MATCH_RATIO, anchors.FUZZY_MIN_LETTERS) \
        == (8, 0.75, 4)
    assert (anchors.SECTION_GAP_MS, anchors.AGREE_MS) == (3000, 1000)
    assert (anchors.MIN_SECTION_LINES, anchors.MIN_CONFIRMED_SHARE) == (2, 0.6)
    assert (anchors.A2_RES_MS, anchors.A2_MAX_OFFSET_MS, anchors.A2_SMOOTH_MS) == (100, 30_000, 500)
    assert anchors.A2_STRETCHES == tuple(round(0.95 + 0.01 * i, 2) for i in range(11))
    assert (anchors.A2_MIN_NCC, anchors.A2_PERIOD_MIN_MS, anchors.A2_AMBIGUITY_NCC) \
        == (0.8, 4000, 0.05)
    assert (anchors.A3_MIN_LETTERS, anchors.A3_PAD_MS, anchors.LINE_MATCH_MAX_CER) == (8, 2000, 0.5)
    assert (anchors.SUNG_DILATE_MS, anchors.COVER_PAD_MS) == (200, 500)
    assert (anchors.BLOCK_JOIN_MS, anchors.BLOCK_MIN_MS) == (1000, 3000)
    assert (anchors.READABLE_LETTERS_PER_S, anchors.READABLE_WORD_SHARE,
            anchors.READABLE_BLOCK_SHARE) == (4.0, 0.6, 0.5)


# --- helpers ---------------------------------------------------------------------

def _song(n_sections=6, per_section=4, seed=3, words=5):
    lines = lines_of(VOCAB, n_sections * per_section, words, seed=seed)
    paras, t = [], 10_000
    for s in range(n_sections):
        para = []
        for k in range(per_section):
            para.append((lines[s * per_section + k], t))
            t += 4000
        paras.append(para)
        t += 3000
    return paras


def _run(paras, delta, **kw):
    """Sheet = the record; the singing sits at sheet + delta(section, line)."""
    sheet = sheet_from_record(record(paras))
    sung = [(ln.text, ln.start_ms + delta(ln.section, ln.index)) for ln in sheet]
    return sheet, check(sheet, ear(sung), **kw)


# --- the sheet ---------------------------------------------------------------------

def test_parse_lrc_is_the_production_parse_plus_paragraphs():
    from kashi_server.pipeline.lrclib import _parse_synced

    lrc = ("[00:01.00] alpha beta\n[00:02.50][00:40.00] gamma delta\n\n"
           "[00:05.00]\n[00:06.123] epsilon\nunstamped zeta\n[00:09.00] ")
    ours = parse_lrc(lrc)
    assert [(s, t) for s, t, _ in ours] == _parse_synced(lrc)
    assert [p for _, _, p in ours] == [0, 0, 1, 1]   # the blank line starts a paragraph
    assert ours[2][0] == 6123


def test_record_paragraphs_are_sections_else_gaps_of_3000_ms():
    paras = [[("abab cdcd", 1000), ("efef ghgh", 3000)], [("ijij klkl", 5000)]]
    sheet = sheet_from_record(record(paras))
    assert [ln.section for ln in sheet] == [0, 0, 1]
    # no paragraph marks: a line ends at start + letters x 90 ms (8 letters: 720 ms)
    flat = sheet_from_record(record([[("abab cdcd", 1000), ("efef ghgh", 1720 + 2990),
                                      ("ijij klkl", 4710 + 720 + 3000)]]))
    assert [ln.section for ln in flat] == [0, 0, 1]   # 2990 ms: same section; 3000: new


def test_document_sheet_uses_first_word_start_and_its_own_rate():
    doc = document([("abab cdcd efef", 2000), ("ghgh ijij klkl", 9000)], ms_per_letter=60)
    doc["lines"][0]["start_ms"] = 1500        # line start before its first word
    sheet = sheet_from_document(doc)
    assert [ln.start_ms for ln in sheet] == [2000, 9000]
    assert sheet[0].ms_per_letter == 50          # sung span 480 / 12 letters = 40, clamped
    assert [ln.section for ln in sheet] == [0, 1]  # 9000 - 2720 >= 3000
    slow = sheet_from_document(document([("abab cdcd efef", 2000)], ms_per_letter=120))
    assert slow[0].ms_per_letter == pytest.approx(8 * 120 / 12)


# --- A1 ---------------------------------------------------------------------------

def test_same_word_is_exact_or_fuzzy_from_four_letters_at_ratio_075():
    assert same_word("abcd", "abce")          # 2*3/8 = 0.75
    assert not same_word("abcdefg", "abcdexy")  # 2*5/14 = 0.714
    assert same_word("abcd", "abcde")         # 0.889
    assert not same_word("abc", "abcd")       # 0.857 but a 3-letter word is spelled exactly
    assert same_word("ab", "ab")


def test_heard_words_split_on_the_delimiter():
    toks = letter_tokens("kala mito", 0, 90)
    assert [(w.text, w.start_ms) for w in heard_words(toks)] == [("kala", 0), ("mito", 360)]


def _anchor_texts(sheet_lines, heard_text, **kw):
    sheet = sheet_from_record(record([[(t, 1000 + 5000 * i) for i, t in enumerate(sheet_lines)]]))
    words = sheet_words(sheet)
    heard = heard_words(letter_tokens(heard_text, 0, 90))
    return [" ".join(w.text for w in words[a.sheet_start:a.sheet_start + a.words])
            for a in find_anchors(heard, words, **kw)]


def test_an_anchor_needs_two_words_and_eight_letters():
    one = _anchor_texts(["bada kemo lupi"], "zzzz bada qqqq kemo xxxx")
    assert one == []                                       # never two in a row
    assert _anchor_texts(["badakemo lupi"], "zzzz badakemo xxxx") == []      # one word
    assert _anchor_texts(["bada kemo lupi"], "zzzz bada kemo xxxx") == ["bada kemo"]
    assert _anchor_texts(["bad kemo lupi"], "zzzz bad kemo xxxx") == []      # 7 letters
    assert _anchor_texts(["badi kemo lupi"], "zzzz badi kemo xxxx") == ["badi kemo"]


def test_a_noisy_stem_needs_five_words():
    line = "bada kemo lupi rasu tevi nolo"
    assert _anchor_texts([line], "bada kemo lupi rasu zzzz", min_words=5) == []
    assert _anchor_texts([line], "bada kemo lupi rasu tevi zzzz", min_words=5) \
        == ["bada kemo lupi rasu tevi"]


def test_an_anchor_is_unique_in_the_sheet_and_in_the_ear():
    # the phrase twice in the sheet: not an anchor
    assert _anchor_texts(["bada kemo lupi", "bada kemo rasu"], "zzzz bada kemo xxxx") == []
    # nor when the second one is a near-spelling the heard run fits as well
    assert _anchor_texts(["bada kemo lupi", "bada kema rasu"], "zzzz bada kemo xxxx") == []
    assert _anchor_texts(["bada kemo lupi", "bada tevi rasu"], "zzzz bada kemo xxxx") \
        == ["bada kemo"]
    # once in the sheet but heard twice: not an anchor either
    assert _anchor_texts(["bada kemo lupi"], "bada kemo xxxx bada kemo") == []
    # the longest unique run wins, even where a shorter one comes first
    got = _anchor_texts(["bada kemo", "lupi rasu tevi"], "bada kemo yyyy lupi rasu tevi")
    assert got == ["bada kemo", "lupi rasu tevi"]
    # anchors keep their order: the longer one wins and the other crosses it
    got = _anchor_texts(["lupi rasu tevi bada kemo"], "bada kemo yyyy lupi rasu tevi")
    assert got == ["lupi rasu tevi"]


def test_a_mid_line_anchor_steps_back_at_the_singers_rate():
    sheet = sheet_from_record(record([[("zzzz yyyy bada kemo lupi", 20_000)]]))
    words = sheet_words(sheet)
    toks = letter_tokens("qqqq wwww bada kemo lupi", 7000, 70)    # sung at 70 ms per letter
    heard = heard_words(toks)
    placed = anchors.anchor_placements(sheet, heard, words, find_anchors(heard, words))
    # bada starts at 7000 + 8 * 70; stepped back by 8 letters at the measured 70 ms
    assert placed[0].heard_ms == 7000
    assert placed[0].delta_ms == 7000 - 20_000


def test_healthy_song_reads_ok_at_the_ears_lag():
    paras = _song()
    _, r = _run(paras, lambda s, i: 50)
    assert r["flag"] == "ok" and r["flags"] == []
    assert r["a1"]["song"]["median_ms"] == 50
    assert r["a1"]["song"]["placed"] == 24
    assert all(b["verdict"] == "ok" for b in r["a1"]["blocks"])


@pytest.mark.parametrize(("delta", "flag"), [(-1000, "ok"), (-1001, "shifted"),
                                              (1000, "ok"), (1001, "shifted")])
def test_shifted_is_a_block_median_beyond_one_second(delta, flag):
    _, r = _run(_song(), lambda s, i: delta)
    assert r["flag"] == flag
    assert r["a1"]["song"]["median_ms"] == delta


def test_gossip_like_sheet_reads_per_section():
    shift = {0: -18_900, 1: -18_900, 2: -18_900, 3: -4000}
    _, r = _run(_song(), lambda s, i: shift.get(s, 50))
    assert r["flag"] == "shifted"
    blocks = [(b["sections"], b["median_ms"], b["verdict"]) for b in r["a1"]["blocks"]]
    assert blocks == [([0, 1, 2], -18_900, "shifted"), ([3], -4000, "shifted"),
                      ([4, 5], 50, "ok")]
    # every line is heard in its corrected window
    assert r["a3"]["matched_lines"] == r["a3"]["counted_lines"] == 24
    # the activity correlation cannot fit one offset to a piecewise shift
    assert r["a2"]["accepted"] is False


def test_block_verdict_boundaries():
    assert block_verdict([]) == "undecidable"
    assert block_verdict([-3000]) == "undecidable"                 # one line
    assert block_verdict([-3000, -3000]) == "shifted"              # two confirm
    # dispersion: 3 of 5 within 1 s of the median is 0.6 -> decided; 2 of 4 -> 0.5 is not
    assert block_verdict([0, 0, 0, 3000, -3000]) == "ok"
    assert block_verdict([0, 0, 2500, -2500, 4000]) == "undecidable"
    # a line exactly 1 s from the median still confirms it (median -1000: not > 1000)
    assert block_verdict([-2000, 0, -1000]) == "ok"
    # beyond 5 s needs five confirmed lines; exactly 5 s needs two
    assert block_verdict([-6000] * 4) == "undecidable"
    assert block_verdict([-6000] * 5) == "shifted"
    assert block_verdict([-5000] * 2) == "shifted"


def test_a_long_move_needs_five_confirmed_lines_end_to_end():
    paras = _song(n_sections=3, per_section=4)
    _, r = _run(paras, lambda s, i: -6000 if s == 1 else 50)
    assert [b["verdict"] for b in r["a1"]["blocks"]] == ["ok", "undecidable", "ok"]
    assert r["flag"] == "ok"
    paras = _song(n_sections=3, per_section=5)
    _, r = _run(paras, lambda s, i: -6000 if s == 1 else 50)
    assert [b["verdict"] for b in r["a1"]["blocks"]] == ["ok", "shifted", "ok"]


def test_sections_pool_within_one_second_of_the_block_median():
    sheet = sheet_from_record(record(_song(n_sections=3, per_section=2)))
    placed = {ln.index: Placement(ln.index, 0, d, "anchor")
              for ln, d in zip(sheet, [-5000, -5000, -6000, -6000, -7001, -7001], strict=True)}
    blocks = pool_sections(sheet, placed)
    # -6000 is 1000 from -5000 (pooled); -7001 is 1500.5 from the pooled median -5500
    assert [b.sections for b in blocks] == [[0, 1], [2]]


def test_repeated_refrain_is_placed_between_its_unique_neighbours():
    """The trap: a refrain the sheet holds three times and the singer sings four
    (an intro chorus the sheet leaves out). Whole-song search would take the
    intro for the first refrain, or find all four equally good and place none."""
    verses = lines_of(VOCAB, 6, 5, seed=11)
    chorus = lines_of(VOCAB, 1, 6, seed=12)[0]
    order = [verses[0], verses[1], chorus, verses[2], verses[3], chorus,
             verses[4], verses[5], chorus]
    sheet_t = [20_000 + 4500 * i for i in range(len(order))]
    paras = [[(t, s)] for t, s in zip(order, sheet_t, strict=True)]
    sheet = sheet_from_record(record(paras))
    shift = -7000
    # an ad-lib the sheet does not hold between lines: no anchor runs across a seam
    adlib = [("hyhyhy", s + shift - 900) for s in sheet_t]   # letters no pseudo-word uses
    sung = [(chorus, 2000)] + [(t, s + shift) for t, s in zip(order, sheet_t, strict=True)]
    r = check(sheet, ear(sung + adlib))
    placed = {ln["line"]: ln for ln in r["a1"]["lines"]}
    for i in (2, 5, 8):
        assert placed[i]["method"] == "between"
        assert placed[i]["delta_ms"] == shift
    assert all(ln["delta_ms"] == shift for ln in r["a1"]["lines"])


def test_a_between_line_stays_inside_its_neighbours():
    verses = lines_of(VOCAB, 3, 5, seed=21)
    loner = lines_of(VOCAB, 1, 5, seed=22)[0]
    sheet = sheet_from_record(record([[(verses[0], 10_000), (loner, 14_000),
                                       (verses[1], 18_000), (verses[2], 22_000)]]))
    # the loner is heard only after the next anchor: it must not be placed there
    sung = [(verses[0], 10_000), (verses[1], 18_000), (verses[2], 22_000), (loner, 30_000)]
    r = check(sheet, ear(sung))
    assert 1 not in {ln["line"] for ln in r["a1"]["lines"]}


# --- A2 ---------------------------------------------------------------------------

def _irregular(n=24, seed=5):
    rng = random.Random(seed)
    texts = lines_of(VOCAB, n, rng.randint(3, 6), seed=seed)
    t, out = 8000, []
    for text in texts:
        out.append((text, t))
        t += rng.randint(2500, 7000)
    return out


def test_activity_correlation_recovers_a_global_offset():
    lines = _irregular()
    sheet = sheet_from_record(record([lines]))
    sung = [(t, s - 12_000) for t, s in lines]
    a2 = anchors.activity_correlation(sheet, ear(sung, total_ms=lines[-1][1] + 10_000))
    assert a2["accepted"] and a2["ncc"] >= 0.8
    assert abs(a2["mid_offset_ms"] - (-12_000)) <= 200
    assert a2["ambiguous"] is False


def test_activity_correlation_reads_a_stretch():
    lines = _irregular()
    sheet = sheet_from_record(record([lines]))
    sung = [(t, round(s * 1.03)) for t, s in lines]
    a2 = anchors.activity_correlation(sheet, ear(sung, total_ms=round(lines[-1][1] * 1.03) + 8000,
                                                 ms_per_letter=93))
    assert a2["stretch"] == 1.03


def test_activity_correlation_rejects_a_sheet_from_another_song():
    sheet = sheet_from_record(record([_irregular(seed=5)]))
    other = _irregular(seed=99)
    a2 = anchors.activity_correlation(sheet, ear(other, total_ms=other[-1][1] + 10_000))
    assert a2["accepted"] is False


def test_activity_correlation_reports_the_refrain_period():
    text = "bada kemo lupi rasu"
    lines = [(text, 5000 + 6000 * i) for i in range(20)]
    sheet = sheet_from_record(record([lines]))
    a2 = anchors.activity_correlation(sheet, ear([(t, s) for t, s in lines]))
    assert a2["ambiguous"] is True
    assert abs(abs(a2["rival_offset_ms"] - a2["offset_ms"]) - 6000) <= 200


def test_a2_accepts_from_ncc_08():
    assert anchors.a2_accepts(0.8) and not anchors.a2_accepts(0.7999)


# --- A3 ---------------------------------------------------------------------------

def test_semiglobal_cer():
    assert semiglobal_cer("kemo", "zzkemozz") == (0.0, 2, 5)
    assert semiglobal_cer("kemo", "zzkexozz")[0] == 0.25
    assert semiglobal_cer("kemo", "") == (1.0, -1, -1)
    assert semiglobal_cer("kemolupi", "zz")[0] == 1.0          # capped


def test_coverage_rule_boundaries():
    assert coverage_rule(250, 1000, 25, 100)
    assert not coverage_rule(249, 1000, 25, 100)
    assert not coverage_rule(250, 1000, 24, 100)
    assert not coverage_rule(0, 0, 10, 10) and not coverage_rule(10, 10, 0, 0)


def _wrong_mix(extra_text, extra_rate):
    """Sheet of 20 lines; the audio sings the first 12, then a 30 s block of
    something else (spelled at extra_rate ms per letter)."""
    lines = lines_of(VOCAB, 20, 5, seed=31)
    sheet_t = [10_000 + 4000 * i for i in range(20)]
    sheet = sheet_from_record(record([list(zip(lines, sheet_t, strict=True))]))
    sung = [(t, s + 40) for t, s in zip(lines[:12], sheet_t[:12], strict=True)]
    block_start = sheet_t[12] + 40
    extra = letter_tokens(extra_text, block_start, extra_rate)
    return sheet, ear(sung, total_ms=sheet_t[-1] + 8000, extra_tokens=extra)


def test_an_unmatched_block_that_spells_other_words_is_a_wrong_version():
    other = " ".join(vocabulary(60, seed=77)[:45])   # ~270 letters at 110 ms: ~30 s
    sheet, e = _wrong_mix(other, 110)
    r = check(sheet, e)
    a3 = r["a3"]
    assert a3["coverage_rule"] and a3["wrong_version"]
    assert a3["matched_lines"] == 12 and a3["counted_lines"] == 20
    assert r["flag"] == "wrong-version candidate"


@pytest.mark.parametrize(("mumble", "rate"), [
    (" ".join(["ao", "oe", "ea"] * 13), 380),   # vowel pairs, but 2.6 letters per second
    (" ".join(list("kstrnmpl") * 25), 120),      # 8 letters per second, none a word
])
def test_processed_vocals_with_no_readable_letters_are_not_a_wrong_version(mumble, rate):
    """The hardstyle negative: the lines are unmatched too, but the ear spells
    nothing - too few letters, or letters that form no word."""
    sheet, e = _wrong_mix(mumble, rate)
    r = check(sheet, e)
    assert r["a3"]["coverage_rule"] and not r["a3"]["wrong_version"]
    assert all(not b["readable"] for b in r["a3"]["blocks"]) and r["a3"]["blocks"]
    assert r["flag"] == "undecidable" and r["flags"] == []


@pytest.mark.parametrize(("heard", "cer", "matched"), [
    ("abcdwxyz", 0.5, True), ("abcwxyzq", 0.625, False)])
def test_a_line_is_heard_up_to_cer_05(heard, cer, matched):
    sheet = sheet_from_record(record([[("abcdefgh", 10_000)]]))
    e = ear([(heard, 10_000)])
    row = anchors.coverage(sheet, e, lambda line: 0)["lines"][0]
    assert (row["cer"], row["matched"]) == (cer, matched)


def test_lines_under_eight_letters_are_not_counted():
    sheet = sheet_from_record(record([[("abcdefgh", 10_000), ("abcdefg", 14_000)]]))
    e = ear([("abcdefgh", 10_000), ("abcdefg", 14_000)])
    assert anchors.coverage(sheet, e, lambda line: 0)["counted_lines"] == 1


def test_tfidf_cosine_is_high_for_the_same_text():
    lines = _irregular()
    sheet = sheet_from_record(record([lines]))
    same = anchors.tfidf_cosine(sheet, " ".join(t for t, _ in lines))
    other = anchors.tfidf_cosine(sheet, " ".join(t for t, _ in _irregular(seed=99)))
    assert same > 0.95 and other < same


# --- CLI ---------------------------------------------------------------------------

def test_cli_writes_the_result_and_warns_on_a_foreign_record(tmp_path, capsys):
    paras = _song(n_sections=3)
    rec = record(paras, record_id=999)
    sheet = sheet_from_record(rec)
    sung = [(ln.text, ln.start_ms - 3000) for ln in sheet]
    doc = document([(ln.text, ln.start_ms) for ln in sheet], source_id=4242)
    (tmp_path / "doc.json").write_text(json.dumps(doc))
    (tmp_path / "rec.json").write_text(json.dumps(rec))
    (tmp_path / "whole.json").write_text(json.dumps(ear(sung)))
    out = tmp_path / "out.json"
    assert anchors.main([VID, "--doc", str(tmp_path / "doc.json"), "--lyrics",
                         str(tmp_path / "rec.json"), "--ear", str(tmp_path / "whole.json"),
                         "--json", str(out)]) == 0
    printed = capsys.readouterr()
    assert "not the document's lyrics source" in printed.err
    assert printed.out.startswith(f"{VID} shifted: song -3.0 s")
    result = json.loads(out.read_text())
    assert result["inputs"]["record_matches_doc"] is False and result["flag"] == "shifted"
    # without --lyrics the document is the sheet (here it equals the record)
    assert anchors.main([VID, "--doc", str(tmp_path / "doc.json"), "--ear",
                         str(tmp_path / "whole.json")]) == 0
    assert capsys.readouterr().out.startswith(f"{VID} shifted: song -3.0 s")
