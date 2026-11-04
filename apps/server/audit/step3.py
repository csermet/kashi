"""Faz 3.5 Step 3 metrics: a summarizer's verdicts against the labelled moments.

Labels are the Faz 3 verdicts under ``audit.compare``'s owner rules (a
disputed verdict joins no set) with ``unsure`` left out: agreement with them is
REPRODUCIBILITY of the agent's reading, not accuracy. Accuracy is against the
owner's listening set (Step 0), reported here, gated by nobody yet.

Operational readings of the note (pre-registered with ``audit.gates``):
  - a real error is a label in BAD (pile, late, early, missing, wrong_text);
    the model flags one with any BAD verdict; a missing, refused or invalid
    output flags nothing;
  - label severity: ok 0, pile 1, every visible class 2 (LABEL_SEVERITY);
  - every gate must hold in EVERY shuffled run (the worst run decides);
  - precision@3 is pooled over the songs with >= 3 real errors (ties in the
    model's severity broken by time);
  - M3 counts a visible-class moment as recalled when the model gives any
    visible-class verdict;
  - M4 is a share: correct probes over probed songs >= 19 / 20;
  - M5 agreement: the share of labelled moments whose verdict is the same in
    every run.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from collections.abc import Sequence

from audit import gates
from audit.compare import BAD, owner_state

LABEL_SEVERITY = {"ok": 0, "pile": 1, "late": 2, "early": 2, "missing": 2, "wrong_text": 2}
VISIBLE = frozenset(gates.VISIBLE_CLASSES)

Key = tuple[str, int]   # (video id, moment t_ms)


def labels_from(records: Sequence[dict]) -> dict[Key, str]:
    """(id, t_ms) -> label: disputed and unsure left out."""
    out = {}
    for rec in records:
        if owner_state(rec) == "owner-disputed" or rec["verdict"] not in LABEL_SEVERITY:
            continue
        out[(str(rec["id"]), int(rec["t_ms"]))] = str(rec["verdict"])
    return out


def validate(answer: dict | None, moment_ids: dict[str, int], line_ids: dict[str, int]
             ) -> str | None:
    """Why an answer is unusable (None when it is valid): the schema, then
    every moment exactly once and a probe line that exists."""
    import jsonschema

    from audit.pack import SCHEMA

    if answer is None:
        return "no answer"
    try:
        jsonschema.validate(answer, SCHEMA)
    except jsonschema.ValidationError as exc:
        return f"schema: {exc.message}"
    seen = [m["moment"] for m in answer["moments"]]
    if sorted(seen) != sorted(moment_ids):
        return f"moments {sorted(seen)} != {sorted(moment_ids)}"
    if answer["probe"]["line"] not in line_ids:
        return f"probe line {answer['probe']['line']} not in the table"
    return None


def predictions(output: dict) -> dict[Key, tuple[str, int]]:
    """(id, t_ms) -> (verdict, severity) from one valid output; {} otherwise."""
    if not output.get("valid"):
        return {}
    ids = output["moment_ids"]
    return {(output["id"], int(ids[m["moment"]])): (m["verdict"], int(m["severity"]))
            for m in output["answer"]["moments"]}


def probe_correct(output: dict) -> bool | None:
    expected = output.get("probe")
    if expected is None:
        return None
    if not output.get("valid"):
        return False
    got = output["answer"]["probe"]
    return got["line"] in expected["lines"] and abs(int(got["abs_ms"])) == expected["abs_ms"]


def _ranks(values: Sequence[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def spearman(a: Sequence[float], b: Sequence[float]) -> float | None:
    """Rank correlation with average ranks for ties; None when either side is flat."""
    if len(a) != len(b) or len(a) < 2:
        return None
    ra, rb = _ranks(a), _ranks(b)
    ma, mb = statistics.mean(ra), statistics.mean(rb)
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb, strict=True))
    den = (sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb)) ** 0.5
    return round(num / den, 4) if den else None


def run_metrics(labels: dict[Key, str], outputs: Sequence[dict]) -> dict:
    """M1-M4 for one run's outputs (one per song)."""
    pred: dict[Key, tuple[str, int]] = {}
    for out in outputs:
        pred |= predictions(out)
    keys = sorted(labels)
    real = [k for k in keys if labels[k] in BAD]
    flagged = [k for k in keys if pred.get(k, ("ok", 0))[0] in BAD]
    tp = len(set(real) & set(flagged))
    visible = [k for k in keys if labels[k] in VISIBLE]
    by_song: dict[str, list[Key]] = defaultdict(list)
    for k in keys:
        by_song[k[0]].append(k)
    top_hits = top_n = 0
    for song_keys in by_song.values():
        if sum(1 for k in song_keys if labels[k] in BAD) < gates.M2_SONG_MIN_ERRORS:
            continue
        top = sorted(song_keys, key=lambda k: (-pred.get(k, ("ok", 0))[1], k[1]))[:3]
        top_hits += sum(1 for k in top if labels[k] in BAD)
        top_n += len(top)
    probes = [c for c in (probe_correct(o) for o in outputs) if c is not None]
    return {
        "labelled": len(keys), "real_errors": len(real), "flagged": len(flagged), "hits": tp,
        "m1_recall": round(tp / len(real), 4) if real else None,
        "m1_precision": round(tp / len(flagged), 4) if flagged else None,
        "m2_spearman": spearman([LABEL_SEVERITY[labels[k]] for k in keys],
                                [pred.get(k, ("ok", 0))[1] for k in keys]),
        "m2_precision_at_3": round(top_hits / top_n, 4) if top_n else None,
        "m3_visible_recall": round(sum(1 for k in visible if pred.get(k, ("ok", 0))[0]
                                       in VISIBLE) / len(visible), 4) if visible else None,
        "m4_probe_correct": sum(probes), "m4_probed": len(probes),
        "valid": sum(1 for o in outputs if o.get("valid")), "outputs": len(outputs),
    }


def run_gates(m: dict) -> dict[str, bool]:
    def at_least(value, bar):
        return value is not None and value >= bar
    return {
        "m1": at_least(m["m1_recall"], gates.M1_RECALL_MIN)
        and at_least(m["m1_precision"], gates.M1_PRECISION_MIN),
        "m2": at_least(m["m2_spearman"], gates.M2_SPEARMAN_MIN)
        and at_least(m["m2_precision_at_3"], gates.M2_PRECISION_AT_3_MIN),
        "m3": at_least(m["m3_visible_recall"], gates.M3_VISIBLE_RECALL_MIN),
        "m4": m["m4_probed"] > 0 and m["m4_probe_correct"] * gates.M4_PROBE_SONGS
        >= gates.M4_PROBE_MIN_CORRECT * m["m4_probed"],
    }


def agreement(labels: dict[Key, str], runs: Sequence[Sequence[dict]]) -> float | None:
    """Share of labelled moments with one verdict in every run (a moment a run
    could not answer disagrees)."""
    per_run = []
    for outputs in runs:
        pred: dict[Key, tuple[str, int]] = {}
        for out in outputs:
            pred |= predictions(out)
        per_run.append(pred)
    if not labels or not per_run:
        return None
    same = sum(1 for k in labels
               if all(k in p for p in per_run) and len({p[k][0] for p in per_run}) == 1)
    return round(same / len(labels), 4)


def listening_accuracy(listening: dict[Key, str], outputs: Sequence[dict]) -> dict:
    """Against the owner's listening set: error or not, and the exact class."""
    pred: dict[Key, tuple[str, int]] = {}
    for out in outputs:
        pred |= predictions(out)
    keys = sorted(listening)
    if not keys:
        return {"moments": 0}
    error_agree = sum(1 for k in keys if (pred.get(k, ("ok", 0))[0] in BAD)
                      == (listening[k] in BAD))
    exact = sum(1 for k in keys if pred.get(k, ("ok", 0))[0] == listening[k])
    return {"moments": len(keys), "error_or_not": round(error_agree / len(keys), 4),
            "exact_class": round(exact / len(keys), 4)}


def evaluate(labels: dict[Key, str], runs: Sequence[Sequence[dict]],
             listening: dict[Key, str] | None = None) -> dict:
    per_run = [run_metrics(labels, outputs) for outputs in runs]
    per_gates = [run_gates(m) for m in per_run]
    total = sum(m["outputs"] for m in per_run)
    first_try = sum(1 for outputs in runs for o in outputs if o.get("valid_first_try"))
    m5_schema = round(first_try / total, 4) if total else None
    m5_agree = agreement(labels, runs)
    out = {
        "runs": per_run,
        "run_gates": per_gates,
        "m5_schema_valid": m5_schema,
        "m5_agreement": m5_agree,
        "gates": {
            **{g: all(pg[g] for pg in per_gates) for g in ("m1", "m2", "m3", "m4")},
            "m5": len(runs) >= gates.SHUFFLED_RUNS
            and m5_schema is not None and m5_schema >= gates.M5_SCHEMA_VALID_MIN
            and m5_agree is not None and m5_agree >= gates.M5_AGREEMENT_MIN,
        },
    }
    out["passed"] = bool(per_run) and all(out["gates"].values())
    if listening:
        out["listening"] = [listening_accuracy(listening, outputs) for outputs in runs]
    return out
