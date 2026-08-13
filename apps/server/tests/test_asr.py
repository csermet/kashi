"""Transcript-based record picking (pipeline/asr.py)."""

from kashi_server.pipeline.asr import (
    DEFAULT_MATCH_THRESHOLD,
    SLICE_SECONDS,
    char_ngrams,
    greedy_ctc_decode,
    normalize_for_match,
    pick_by_transcript,
    similarity,
    slice_window,
    usable_columns,
)

# What a language-model-free CTC decode over a MIX actually looks like — the
# vowels survive, the consonants smear, the word boundaries mostly hold.
HEATHENS_HEARD = "all my frends ar hethens tak it slo weit for them to ask you who you no"
HEATHENS_TRUE = (
    "All my friends are heathens, take it slow\n"
    "Wait for them to ask you who you know\n"
    "Please don't make any sudden moves\n"
    "You don't know the half of the abuse"
)
MAD_LOVE_TRUE = (
    "Girl I got that mad love for you\n"
    "Tell me what you wanna do\n"
    "I could be your one and only"
)


class TestNormalize:
    def test_folds_accents_the_vocabulary_cannot_emit(self):
        # The ASR alphabet is ASCII-ish; the lyric sheet is not. That mismatch
        # says nothing about whether these are the same song.
        assert normalize_for_match("Şarkı Söylüyor") == normalize_for_match("Sarki Soyluyor")

    def test_dotless_i_survives_instead_of_becoming_a_hole(self):
        # NFKD does not decompose "ı" — it is a letter, not an accented base —
        # so without an explicit fold every Turkish "ı" turned into a SPACE and
        # split the word around it.
        assert normalize_for_match("kırık") == "kirik"
        assert normalize_for_match("İstanbul") == "istanbul"

    def test_drops_what_only_the_written_side_has(self):
        assert normalize_for_match("Hello, world! (x2)") == "hello world x"

    def test_collapses_whitespace_so_layout_is_not_evidence(self):
        assert normalize_for_match("one\n\n  two") == "one two"


class TestNgrams:
    def test_keeps_spaces_so_word_boundaries_are_evidence(self):
        # Where the boundary falls is part of what the n-grams see — one of the
        # few things a language-model-free decode gets right even when the
        # letters around it are wrong.
        assert char_ngrams("hold on") != char_ngrams("holdon")
        assert " " in "".join(char_ngrams("hold on"))

    def test_text_shorter_than_the_window_yields_nothing(self):
        assert char_ngrams("ab") == set()


class TestSimilarity:
    def test_recognises_the_song_through_a_bad_decode(self):
        assert similarity(HEATHENS_HEARD, HEATHENS_TRUE) > 0.5

    def test_rejects_a_different_song(self):
        assert similarity(HEATHENS_HEARD, MAD_LOVE_TRUE) < 0.2

    def test_the_right_song_beats_the_wrong_one_by_a_wide_margin(self):
        # The property the rung actually depends on — the absolute values may
        # drift with the model, the ORDER must not.
        assert similarity(HEATHENS_HEARD, HEATHENS_TRUE) > similarity(
            HEATHENS_HEARD, MAD_LOVE_TRUE
        ) + 0.3

    def test_a_complete_sheet_is_not_punished_for_being_complete(self):
        # Containment, not Jaccard: the transcript is one slice, the sheet is
        # the whole song. A symmetric measure would score the correct record
        # DOWN for having verses the slice never reached.
        long_sheet = HEATHENS_TRUE + "\n" + ("filler line that was never sung\n" * 40)
        assert similarity(HEATHENS_HEARD, long_sheet) > 0.5

    def test_empty_input_is_never_a_match(self):
        assert similarity("", HEATHENS_TRUE) == 0.0
        assert similarity(HEATHENS_HEARD, "") == 0.0


class TestGreedyDecode:
    VOCAB = {0: "<pad>", 1: "h", 2: "e", 3: "l", 4: "o", 5: "|"}

    def test_collapses_repeats_then_drops_blanks(self):
        assert greedy_ctc_decode([1, 1, 2, 2, 3, 3, 3, 4], self.VOCAB) == "helo"

    def test_a_blank_between_frames_preserves_a_real_double_letter(self):
        # The reason the order is fixed: "hello" only survives because a blank
        # separates the two l's. Dropping blanks first would eat one of them.
        assert greedy_ctc_decode([1, 2, 3, 0, 3, 4], self.VOCAB) == "hello"

    def test_word_separator_becomes_a_space(self):
        assert greedy_ctc_decode([1, 2, 5, 4], self.VOCAB) == "he o"

    def test_unknown_ids_are_dropped_rather_than_guessed(self):
        assert greedy_ctc_decode([1, 99, 2], self.VOCAB) == "he"

    def test_silence_decodes_to_nothing(self):
        assert greedy_ctc_decode([0, 0, 0], self.VOCAB) == ""


class TestSliceWindow:
    def test_starts_a_quarter_in_past_the_intro(self):
        assert slice_window(200.0, want_s=45.0) == (50.0, 45.0)

    def test_pulls_back_rather_than_running_off_the_end(self):
        start, length = slice_window(50.0, want_s=45.0)
        assert start + length <= 50.0

    def test_a_short_track_gives_what_it_has(self):
        assert slice_window(30.0, want_s=45.0) == (0.0, 30.0)

    def test_a_degenerate_duration_never_produces_a_negative_window(self):
        start, length = slice_window(0.0)
        assert start == 0.0 and length == 0.0


class TestPick:
    CANDIDATES = [("right", HEATHENS_TRUE), ("wrong", MAD_LOVE_TRUE)]

    def test_picks_the_record_the_audio_agrees_with(self):
        assert pick_by_transcript(HEATHENS_HEARD, self.CANDIDATES, threshold=0.4) == "right"

    def test_refuses_when_none_of_them_is_the_song(self):
        # The common case: a title-only search returns twenty records for a
        # title that merely resembles this one.
        picked = pick_by_transcript(
            "completely unrelated speech", self.CANDIDATES, threshold=0.4
        )
        assert picked is None

    def test_refuses_a_near_tie_rather_than_guessing(self):
        # Two records scoring alike is either a harmless duplicate or the right
        # song next to a wrong one sharing a chorus — and the scores cannot tell
        # those apart. A missing sheet beats a wrong one on screen.
        twins = [("a", HEATHENS_TRUE), ("b", HEATHENS_TRUE)]
        assert pick_by_transcript(HEATHENS_HEARD, twins, threshold=0.4, margin=0.05) is None

    def test_no_candidates_is_not_an_error(self):
        assert pick_by_transcript(HEATHENS_HEARD, [], threshold=0.4) is None

    def test_an_empty_transcript_can_never_pick(self):
        # A failed decode must not become a confident answer.
        assert pick_by_transcript("", self.CANDIDATES, threshold=0.4) is None


class TestMeasuredThreshold:
    """The default sits in a gap that was measured, not chosen."""

    def test_accepts_the_weakest_true_positive_seen(self):
        # Heathens, cover -> original, scored 0.296 on the worker. A cover is
        # what the rung EXISTS for, so the default must not reject it.
        assert DEFAULT_MATCH_THRESHOLD < 0.296

    def test_rejects_the_strongest_wrong_sheet_seen(self):
        # 36 decoy records topped out at 0.238.
        assert DEFAULT_MATCH_THRESHOLD > 0.238

    def test_the_slice_stays_one_window(self):
        # Three 30 s slices measured WORSE than one 45 s slice (+0.088 vs
        # +0.134 narrowest margin): spreading reaches instrumental stretches
        # and decodes them into noise.
        assert SLICE_SECONDS == 45.0


class TestUsableColumns:
    def test_drops_the_aligner_star_column(self):
        # 33-token vocabulary, 34 emission columns — the extra one is <star>
        # and it wins every frame. The first probe run decoded to an empty
        # string for exactly this reason.
        assert usable_columns(34, 33) == 33

    def test_never_reads_past_the_end(self):
        assert usable_columns(20, 33) == 20

    def test_leaves_an_exact_width_alone(self):
        assert usable_columns(33, 33) == 33
