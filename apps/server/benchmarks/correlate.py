"""Which candidate signal actually knows how accurate a document is?

Reads a `--signals` sweep and ranks every candidate by how well it predicts
the ground-truth error. The bar to beat is the shipped `quality_score`, which
measured **Pearson +0.36 / Spearman +0.29** against PCO@0.3 on this same set —
the number that started this hunt.

    python -m benchmarks.correlate benchmarks/results/<file>.json

Spearman is the one to read. A confidence signal is used by RANKING documents
(gate the worst, trust the best), so monotonicity is what matters; a signal
could be perfectly informative and badly scaled, and Pearson would punish it
for the scaling alone.

Correlations are reported against PCO@0.3 (higher is better) and word MAE
(lower is better), with the sign normalised so **positive always means the
signal is right**. Anything that cannot clear the incumbent on a set this
small has not earned production.
"""

import argparse
import json
import sys
from pathlib import Path

# The incumbent, measured 2026-08-06 on all 79 JamendoLyrics songs.
INCUMBENT_SPEARMAN = 0.285
INCUMBENT_LABEL = "quality_score (shipped)"

# Signals where a LOW value means a GOOD document, so the correlation's sign
# has to be flipped before it can be compared with the rest.
LOWER_IS_BETTER = {
    "prob_frac_below_01",
    "prob_frac_below_05",
    "onset_median_ms",
    "duration_outlier_frac",
    "overlap_frac",
    "silence_frac",
}


def _pearson(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    if n < 3:
        return 0.0
    mx, my = sum(xs) / n, sum(ys) / n
    num = sum((a - mx) * (b - my) for a, b in zip(xs, ys, strict=True))
    den = (
        sum((a - mx) ** 2 for a in xs) * sum((b - my) ** 2 for b in ys)
    ) ** 0.5
    return num / den if den else 0.0


def _ranks(values: list[float]) -> list[float]:
    """Average ranks, so ties do not invent an ordering that is not there."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        shared = (i + j) / 2
        for k in range(i, j + 1):
            ranks[order[k]] = shared
        i = j + 1
    return ranks


def _spearman(xs: list[float], ys: list[float]) -> float:
    return _pearson(_ranks(xs), _ranks(ys))


def _flatten(signals: dict) -> dict[str, float]:
    """`onset_within` is nested; everything else is already flat."""
    out: dict[str, float] = {}
    for key, value in signals.items():
        if isinstance(value, dict):
            for sub, inner in value.items():
                if isinstance(inner, int | float):
                    out[f"{key}_{sub}"] = float(inner)
        elif isinstance(value, int | float):
            out[key] = float(value)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path)
    args = parser.parse_args()

    report = json.loads(args.results.read_text(encoding="utf-8"))
    songs = [
        s
        for s in report.get("jamendo", {}).get("songs", [])
        if s.get("words") and s.get("signals")
    ]
    if not songs:
        print(
            "no scored songs carry signals — was the sweep run with --signals?",
            file=sys.stderr,
        )
        return 1

    truth_pco = [s["words"]["pcs"]["0.3"] for s in songs]
    truth_mae = [s["words"]["mae_ms"] for s in songs]

    candidates: dict[str, list[float]] = {"quality_score": [s["quality_score"] for s in songs]}
    for name in _flatten(songs[0]["signals"]):
        column = [_flatten(s["signals"]).get(name) for s in songs]
        if all(v is not None for v in column):
            candidates[name] = [float(v) for v in column]  # pyright: ignore[reportArgumentType]

    rows = []
    for name, values in candidates.items():
        if len(set(values)) < 3:  # constant signals cannot rank anything
            continue
        flip = -1.0 if name in LOWER_IS_BETTER else 1.0
        rows.append(
            (
                name,
                flip * _spearman(values, truth_pco),
                flip * _pearson(values, truth_pco),
                -flip * _spearman(values, truth_mae),
            )
        )
    rows.sort(key=lambda r: -abs(r[1]))

    print(f"{len(songs)} songs · {args.results.name}")
    print(f"bar to beat: {INCUMBENT_LABEL} Spearman {INCUMBENT_SPEARMAN:+.3f}\n")
    print(f"{'signal':<28}{'Spearman':>10}{'Pearson':>10}{'vs MAE':>10}   verdict")
    print("-" * 74)
    for name, sp, pe, mae_sp in rows:
        if abs(sp) < 0.1:
            verdict = "knows nothing"
        elif abs(sp) <= INCUMBENT_SPEARMAN:
            verdict = "no better than today"
        elif abs(sp) < 0.5:
            verdict = "BEATS the incumbent"
        else:
            verdict = "STRONG"
        mark = "*" if name == "quality_score" else " "
        print(f"{mark}{name:<27}{sp:>+10.3f}{pe:>+10.3f}{mae_sp:>+10.3f}   {verdict}")
    print("\n* = the incumbent. Sign is normalised: positive always means the")
    print("  signal is right. Read Spearman — a gate ranks, it does not scale.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
