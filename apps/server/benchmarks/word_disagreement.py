"""Do MMS and Qwen fail on the SAME words, or on different ones?

The debt this pays (Faz 8.1). The arbiter's third signal was going to be
cross-model disagreement, on the strength of one number: MMS and Qwen3-FA
correlate **+0.483 per SONG**, against +0.92..+0.945 for same-family models.
That is evidence of architectural diversity — but a song-level correlation
cannot tell the two cases apart:

  - the models are wrong in different PLACES -> "these two disagree here" is
    real evidence of doubt, and the arbiter gains a third witness;
  - the models are wrong in the same places, and merely by different amounts
    -> the signal goes quiet exactly when both are wrong, which is exactly
    when the arbiter needed it. Useless, and worse than useless if trusted.

Nothing about the first case follows from +0.483. So it gets measured.

    python -m benchmarks.word_disagreement --mms <sweep>.json --qwen <probe>.json

Both files must carry `word_detail` (run.py / qwen_probe.py `--dump-words`)
and must have been produced with the SAME `--anchor-jitter-ms`: a shared
window plan is the only way a difference between them means the models
disagreed rather than one of them having had better anchors. The tool refuses
otherwise rather than printing a number that looks fine.

THE THRESHOLDS BELOW WERE COMMITTED BEFORE THE FIRST RUN (approved 2026-08-09).
They are the whole point of the exercise: a disagreement signal can always be
made to look good by choosing its operating point afterwards. Do not tune them
to a result. If none of them is met, the answer is that the adapter does not
get written.
"""

import argparse
import json
import sys
from pathlib import Path
from statistics import median

from benchmarks.correlate import _pearson, _spearman

# --- pre-registered decision rule (see module docstring) ---------------------

# P1 — independence. Per-song Spearman between the two models' ABSOLUTE errors,
# taken as the median across songs. Per song, not pooled: pooling would let the
# already-known song-level covariance (+0.483, i.e. "hard songs are hard for
# both") reappear as if it were a word-level finding.
P1_MAX_MEDIAN_RHO = 0.5
# P2 — diagnostic power. A word is BAD when MMS misses it by more than the
# PCO@0.3 convention; the signal FLAGS a word when the two models' times differ
# by more than 500 ms (~6 of Qwen's 80 ms quantisation boxes, so box noise
# cannot manufacture a flag). The bar is set against the arbiter's existing
# onset signal, which caught 35% of broken lines at 7x the base rate.
P2_BAD_MS = 300
P2_FLAG_MS = 500
P2_MIN_LIFT = 3.0
P2_MIN_RECALL = 0.20
# P3 — false-alarm ceiling. Among words MMS clearly got RIGHT, the signal must
# stay quiet. This is the failure mode the other two cannot see: a flag that
# mostly means "Qwen broke here" would flood the arbiter with noise while
# still scoring well on lift.
P3_SURE_MS = 100
P3_MAX_FALSE_ALARM = 0.05

# The ONE escape hatch the pre-registration allows, and only when P1 has
# already passed: if the errors are independent but 500 ms is the wrong place
# to stand, these three operating points may be examined — once. They are a
# constant rather than a CLI value on purpose. A free `--flag-ms` knob would
# let the operating point be chosen after seeing the answer, which is exactly
# the failure the pre-registration exists to prevent.
SWEEP_FLAG_MS = (300, 500, 800)


def _load(path: Path, kind: str) -> dict:
    report = json.loads(path.read_text(encoding="utf-8"))
    songs = report.get("jamendo", {}).get("songs", [])
    detailed = [s for s in songs if s.get("word_detail")]
    if not detailed:
        raise SystemExit(
            f"{path.name}: no word_detail — re-run the {kind} side with --dump-words"
        )
    return report


def _jitter_of(report: dict) -> int:
    """The anchor jitter a report was produced with.

    Absent means the run predates the flag, which for both producers meant
    ground-truth anchors — 0. Stated explicitly because silently defaulting is
    how two incomparable files get compared.
    """
    meta = report.get("meta", {})
    if "anchor_jitter_ms" in meta:
        return meta["anchor_jitter_ms"] or 0
    config = report.get("config", {})
    return config.get("anchor_jitter_ms", 0) or 0


def _rows(report: dict) -> dict[str, dict[int, dict]]:
    """stem -> annotation index -> row."""
    return {
        song["stem"]: {row["i"]: row for row in song["word_detail"]}
        for song in report.get("jamendo", {}).get("songs", [])
        if song.get("word_detail")
    }


def _edge_words(qwen_rows: dict[int, dict]) -> set[int]:
    """Annotation indices sitting at a window boundary.

    A window edge is an error source the two models SHARE — both get the same
    padded slice — so a correlation computed over edge words partly measures
    the window plan rather than the models. P1 is reported with and without
    them; if the two disagree, believe the one that excludes them.
    """
    first: dict[int, int] = {}
    last: dict[int, int] = {}
    for index, row in qwen_rows.items():
        window = row.get("window", -1)
        if window < 0:
            continue
        if window not in first or index < first[window]:
            first[window] = index
        if window not in last or index > last[window]:
            last[window] = index
    return set(first.values()) | set(last.values())


def _operating_point(words: list[dict], flag_ms: int) -> dict:
    """Everything P2 and P3 need at ONE flag threshold.

    P2 and P3 read the same flag from opposite sides — does it find the bad
    words, and does it stay quiet on the good ones — so they are computed
    together. Loosening the threshold trades one against the other, which is
    what makes an after-the-fact choice of threshold so easy to abuse and why
    the sweep is restricted to SWEEP_FLAG_MS.
    """
    bad = [w for w in words if abs(w["e_m"]) > P2_BAD_MS]
    flagged = [w for w in words if abs(w["d"]) > flag_ms]
    hits = [w for w in flagged if abs(w["e_m"]) > P2_BAD_MS]
    sure = [w for w in words if abs(w["e_m"]) <= P3_SURE_MS]
    base_rate = len(bad) / len(words) if words else 0.0
    precision = len(hits) / len(flagged) if flagged else 0.0
    recall = len(hits) / len(bad) if bad else 0.0
    lift = precision / base_rate if base_rate else 0.0
    false_alarm = (
        sum(abs(w["d"]) > flag_ms for w in sure) / len(sure) if sure else 0.0
    )
    return {
        "flag_ms": flag_ms,
        "n_bad": len(bad),
        "n_flagged": len(flagged),
        "n_sure": len(sure),
        "base_rate": base_rate,
        "precision": precision,
        "recall": recall,
        "lift": lift,
        "false_alarm": false_alarm,
        "p2": lift >= P2_MIN_LIFT and recall >= P2_MIN_RECALL,
        "p3": false_alarm <= P3_MAX_FALSE_ALARM,
    }


def _fmt_pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mms", type=Path, required=True, help="run.py sweep with --dump-words")
    parser.add_argument("--qwen", type=Path, required=True, help="qwen_probe.py with --dump-words")
    parser.add_argument(
        "--sweep",
        action="store_true",
        help=(
            "the ONE threshold sweep the pre-registration allows, and only "
            f"after P1 passes: P2/P3 at |Δ| in {SWEEP_FLAG_MS} ms. There is "
            "deliberately no free threshold argument — the values are fixed in "
            "the module so the operating point cannot be chosen after seeing "
            "the answer."
        ),
    )
    parser.add_argument(
        "--allow-anchor-mismatch",
        action="store_true",
        help="compare files built on different anchors anyway (diagnostics only — the "
        "result does not answer the question this tool exists for)",
    )
    args = parser.parse_args()

    mms_report = _load(args.mms, "MMS")
    qwen_report = _load(args.qwen, "Qwen")
    mms_jitter, qwen_jitter = _jitter_of(mms_report), _jitter_of(qwen_report)
    if mms_jitter != qwen_jitter and not args.allow_anchor_mismatch:
        print(
            f"anchor mismatch: MMS ran at {mms_jitter} ms jitter, Qwen at "
            f"{qwen_jitter} ms. The two would not share a window plan, so any "
            "disagreement measured between them is partly an artefact of one "
            "model getting better anchors. Re-run one side to match, or pass "
            "--allow-anchor-mismatch if you know why you want this.",
            file=sys.stderr,
        )
        return 1

    mms_rows, qwen_rows = _rows(mms_report), _rows(qwen_report)
    shared_stems = sorted(set(mms_rows) & set(qwen_rows))
    if not shared_stems:
        print("no song appears in both files", file=sys.stderr)
        return 1

    # --- join ---------------------------------------------------------------
    # Per song, so P1 can be computed within-song; also flattened for P2/P3.
    per_song: list[tuple[str, list[dict]]] = []
    dropped: list[str] = []
    for stem in shared_stems:
        m, q = mms_rows[stem], qwen_rows[stem]
        edges = _edge_words(q)  # hoisted: it is a property of the song, not the word
        joined = []
        for index, q_row in q.items():
            m_row = m.get(index)
            if m_row is None:
                continue
            if m_row["truth_ms"] != q_row["truth_ms"]:  # same index, same word — or a bug
                continue
            joined.append(
                {
                    "stem": stem,
                    "i": index,
                    "e_m": m_row["hyp_ms"] - m_row["truth_ms"],
                    "e_q": q_row["hyp_ms"] - q_row["truth_ms"],
                    "d": m_row["hyp_ms"] - q_row["hyp_ms"],
                    "line": m_row.get("line", -1),
                    "edge": index in edges,
                }
            )
        if len(joined) < 3:
            dropped.append(stem)
            continue
        per_song.append((stem, joined))

    words = [row for _, rows in per_song for row in rows]
    if not words:
        print("nothing joined", file=sys.stderr)
        return 1

    print(f"{len(per_song)} song(s) · {len(words)} words · anchor jitter {mms_jitter} ms")
    print(f"  MMS  {args.mms.name}")
    print(f"  Qwen {args.qwen.name}")
    if dropped:
        print(f"  dropped (too few joined words): {', '.join(dropped)}")
    print()

    # --- P1: are the errors independent? ------------------------------------
    def _rho(rows: list[dict]) -> float | None:
        if len(rows) < 3:
            return None
        return _spearman([abs(r["e_m"]) for r in rows], [abs(r["e_q"]) for r in rows])

    rhos = [(stem, _rho(rows)) for stem, rows in per_song]
    scored = [(stem, r) for stem, r in rhos if r is not None]
    median_rho = median([r for _, r in scored])
    inner = [(stem, _rho([w for w in rows if not w["edge"]])) for stem, rows in per_song]
    inner_scored = [r for _, r in inner if r is not None]
    median_rho_inner = median(inner_scored) if inner_scored else float("nan")
    p1_pass = median_rho <= P1_MAX_MEDIAN_RHO

    print("P1 — independence of the errors (per-song Spearman of |error|)")
    print(f"  median rho            {median_rho:+.3f}   (threshold <= {P1_MAX_MEDIAN_RHO:+.2f})")
    print(f"  median rho, no edges  {median_rho_inner:+.3f}   (window-edge words removed)")
    worst = max(scored, key=lambda r: r[1])
    best = min(scored, key=lambda r: r[1])
    print(f"  range {best[1]:+.3f} ({best[0]}) .. {worst[1]:+.3f} ({worst[0]})")
    print(f"  -> {'PASS' if p1_pass else 'FAIL'}")
    print()

    # --- P2 / P3 at the committed operating point ---------------------------
    point = _operating_point(words, P2_FLAG_MS)
    bad = [w for w in words if abs(w["e_m"]) > P2_BAD_MS]  # kept for the context block
    base_rate = point["base_rate"]
    precision, recall, lift = point["precision"], point["recall"], point["lift"]
    p2_pass = point["p2"]

    print(
        f"P2 — diagnostic power (bad = |MMS error| > {P2_BAD_MS} ms, "
        f"flag = |Δ| > {P2_FLAG_MS} ms)"
    )
    print(f"  base rate of bad words  {_fmt_pct(base_rate)} ({len(bad)}/{len(words)})")
    print(
        f"  flagged                 {_fmt_pct(point['n_flagged'] / len(words))} "
        f"({point['n_flagged']})"
    )
    print(f"  precision               {_fmt_pct(precision)}")
    print(f"  lift                    {lift:.2f}x   (threshold >= {P2_MIN_LIFT:.1f}x)")
    print(
        f"  recall                  {_fmt_pct(recall)}   "
        f"(threshold >= {_fmt_pct(P2_MIN_RECALL)})"
    )
    print(f"  -> {'PASS' if p2_pass else 'FAIL'}")
    print()

    # --- P3: is it quiet where MMS is right? --------------------------------
    sure = [w for w in words if abs(w["e_m"]) <= P3_SURE_MS]
    false_alarm_rate = point["false_alarm"]
    p3_pass = point["p3"]

    print(f"P3 — false alarms where MMS is clearly right (|MMS error| <= {P3_SURE_MS} ms)")
    print(f"  such words              {len(sure)}")
    print(
        f"  of those, flagged       {_fmt_pct(false_alarm_rate)} "
        f"(threshold <= {_fmt_pct(P3_MAX_FALSE_ALARM)})"
    )
    print(f"  -> {'PASS' if p3_pass else 'FAIL'}")
    print()

    # --- context, NOT part of the decision ----------------------------------
    rescuable = [w for w in bad if abs(w["e_q"]) <= P2_BAD_MS]
    print("context (reported, not a criterion)")
    print(
        f"  P(Qwen right | MMS wrong)   {_fmt_pct(len(rescuable) / len(bad)) if bad else 'n/a'}"
        "   — how often a second opinion could have corrected, not just doubted"
    )
    pooled = _pearson([abs(w["e_m"]) for w in words], [abs(w["e_q"]) for w in words])
    print(
        f"  pooled Pearson |e_m| vs |e_q|  {pooled:+.3f}   "
        "(inflated by song-level covariance — do NOT read this as P1)"
    )
    offsets = [
        (stem, median([w["e_m"] for w in rows]), median([w["e_q"] for w in rows]))
        for stem, rows in per_song
    ]
    drifting = [o for o in offsets if abs(o[1] - o[2]) > 200]
    print(f"  songs whose SIGNED medians differ by >200 ms: {len(drifting)}/{len(per_song)}")

    # Line scope: the arbiter decides per LINE, so the word verdict is not the
    # last word on whether the signal is usable where it would actually be used.
    by_line: dict[tuple[str, int], list[dict]] = {}
    for w in words:
        if w["line"] >= 0:
            by_line.setdefault((w["stem"], w["line"]), []).append(w)
    if by_line:
        line_rows = [
            (median([abs(w["d"]) for w in ws]), median([abs(w["e_m"]) for w in ws]))
            for ws in by_line.values()
        ]
        print(
            f"  line scope ({len(line_rows)} lines): Spearman(median|Δ|, median|MMS error|) "
            f"{_spearman([r[0] for r in line_rows], [r[1] for r in line_rows]):+.3f}"
        )
    print()

    # --- the one allowed sweep ----------------------------------------------
    # Gated on P1 having passed: if the two models fail on the SAME words, no
    # operating point can rescue the signal and a sweep would only be shopping
    # for a number.
    if args.sweep and p1_pass:
        allowed = ", ".join(str(v) for v in SWEEP_FLAG_MS)
        print(f"threshold sweep (pre-declared: |Δ| at {allowed} ms)")
        print(
            f"  {'|Δ|':>7}{'flagged':>10}{'precision':>11}{'lift':>8}"
            f"{'recall':>9}{'false alarm':>13}   P2/P3"
        )
        print("  " + "-" * 64)
        for flag_ms in SWEEP_FLAG_MS:
            row = _operating_point(words, flag_ms)
            marks = f"{'ok' if row['p2'] else 'no'}/{'ok' if row['p3'] else 'no'}"
            print(
                f"  {flag_ms:>5} ms{_fmt_pct(row['n_flagged'] / len(words)):>10}"
                f"{_fmt_pct(row['precision']):>11}{row['lift']:>7.2f}x"
                f"{_fmt_pct(row['recall']):>9}{_fmt_pct(row['false_alarm']):>13}   {marks}"
            )
        survivors = [
            v
            for v in SWEEP_FLAG_MS
            if all(_operating_point(words, v)[key] for key in ("p2", "p3"))
        ]
        print()
        if survivors:
            print(f"  operating point(s) satisfying BOTH: {survivors} ms")
        else:
            print("  NO operating point satisfies P2 and P3 together.")
            print("  Per the pre-registration, that ends it: the adapter is not written")
            print("  on this evidence, and no further threshold may be invented.")
        print()
    elif args.sweep:
        print("sweep skipped: P1 failed, so no operating point can rescue the signal.\n")

    verdict = p1_pass and p2_pass and p3_pass
    print("=" * 66)
    if verdict:
        print("ALL THREE PASS -> write the Qwen adapter (English only; never Turkish).")
    elif not p1_pass:
        print(
            "P1 FAILED -> the two models are wrong in the same places. The\n"
            "disagreement signal would go quiet exactly when it was needed.\n"
            "Do not write the adapter; the Qwen file closes here."
        )
    else:
        print(
            "P1 passed but the operating point did not -> ONE pre-declared\n"
            "threshold sweep is allowed (|Δ| at 300/500/800 ms). If no point\n"
            "satisfies P2 and P3 together, the adapter does not get written.\n"
            "No threshold may be invented after seeing this table."
        )
    print("=" * 66)
    return 0 if verdict else 2


if __name__ == "__main__":
    raise SystemExit(main())
