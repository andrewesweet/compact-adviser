#!/usr/bin/env python3
"""Locked holdout comparison and session-cluster bootstrap; aggregate output only."""
import argparse
from collections import defaultdict
import json
import math
from pathlib import Path
import random
from label import private_path, save
from optimise import SHIPPED, decision, join_rows, jsonl, macro_recall, metrics, sha, strata, truth


def rank_metrics(rows, profile, definition="union"):
    pairs = [(decision(row, profile)["score"], truth(row["label"], definition)) for row in rows if row.get("ok")]
    pairs = [(score, gold) for score, gold in pairs if gold is not None]
    positives = [score for score, gold in pairs if gold]
    negatives = [score for score, gold in pairs if not gold]
    auc = (sum(1 if p > n else 0.5 if p == n else 0 for p in positives for n in negatives) /
           (len(positives) * len(negatives))) if positives and negatives else None
    grouped = defaultdict(lambda: [0, 0])
    for score, gold in pairs:
        grouped[score][0] += bool(gold)
        grouped[score][1] += 1
    hit = seen = 0
    ap = 0 if positives else None
    # Threshold-based AP: ties do not depend on checkpoint-id order.
    for score in sorted(grouped, reverse=True):
        positive, total = grouped[score]
        hit += positive
        seen += total
        if ap is not None:
            ap += positive / len(positives) * hit / seen
    return {"auc": auc, "ap": ap, "successfulKnownRows": len(pairs)}


def composition(rows):
    labels = [row["label"] for row in rows]
    return {
        "rows": len(rows), "sessionGroups": len({row["group"] for row in rows}),
        "safe": sum(label["safe_to_compact"] is True for label in labels),
        "unsafe": sum(label["safe_to_compact"] is False for label in labels),
        "unknownSafety": sum(label["safe_to_compact"] is None for label in labels),
        "completed": sum(label["phase_gold"] == "completed_checkpoint" for label in labels),
        "midTask": sum(label["phase_gold"] == "still_in_progress" for label in labels),
        "midTaskAndUnsafe": sum(label["phase_gold"] == "still_in_progress" and label["safe_to_compact"] is False for label in labels),
        "safeCompletion": sum(truth(label, "union") is True for label in labels),
        "unionNegative": sum(truth(label, "union") is False for label in labels),
        "unionUnknown": sum(truth(label, "union") is None for label in labels),
    }


def table(rows, profiles):
    return {name: {definition: {**metrics(rows, profile, definition),
                              "macroRecall": macro_recall(rows, profile, definition),
                              "rank": rank_metrics(rows, profile, definition)}
                   for definition in ("union", "product", "contract")}
            for name, profile in profiles.items()}


def percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    position = fraction * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def clustered_difference(rows, baseline, candidate, repeats=2000, seed=7):
    groups = defaultdict(lambda: defaultdict(list))
    for row in rows:
        groups[row["stratum"]][row["group"]].append(row)
    counts = {name: len(part) for name, part in groups.items()}
    caveat = "Stratified session-group resampling. Single-group strata are held fixed and have no independent within-stratum uncertainty estimate."
    if sum(counts.values()) < 2:
        return {"groupsByStratum": counts, "intervals": None, "limitation": caveat}
    rng = random.Random(seed)
    differences = {name: [] for name in ("precision", "recall", "macroRecall", "ap", "auc")}
    for _ in range(repeats):
        sample = []
        for part in groups.values():
            keys = sorted(part)
            for _ in keys:
                sample.extend(part[rng.choice(keys)])
        before = {**metrics(sample, baseline), **rank_metrics(sample, baseline), "macroRecall": macro_recall(sample, baseline)}
        after = {**metrics(sample, candidate), **rank_metrics(sample, candidate), "macroRecall": macro_recall(sample, candidate)}
        for name in differences:
            if before[name] is not None and after[name] is not None:
                differences[name].append(after[name] - before[name])
    return {
        "groupsByStratum": counts, "repeats": repeats, "seed": seed, "limitation": caveat,
        "intervals": {name: {"lower95": percentile(values, 0.025), "upper95": percentile(values, 0.975),
                              "probabilityPositive": sum(value > 0 for value in values) / len(values) if values else None,
                              "usableReplicates": len(values)} for name, values in differences.items()},
    }


def missing_label_bounds(rows, unlabelled, profile, definition="union"):
    measured = metrics(rows, profile, definition)
    missing_hints = measured["unknownHints"] + sum(decision(row, profile)["hint"] for row in unlabelled)
    missing = measured["unknown"] + len(unlabelled)
    missed = missing - missing_hints
    tp, fp, fn = (measured[key] for key in ("tp", "fp", "fn"))
    def ratio(numerator, denominator):
        return numerator / denominator if denominator else None
    return {"uncertainRows": missing, "uncertainHints": missing_hints,
            "precisionWorst": ratio(tp, tp + fp + missing_hints),
            "precisionBest": ratio(tp + missing_hints, tp + fp + missing_hints),
            "recallWorst": ratio(tp, tp + fn + missed),
            "recallBest": ratio(tp + missing_hints, tp + fn + missing_hints)}


def report(rows, selection, repeats=2000, unlabelled=()):
    profiles = {"shipped": SHIPPED, "flat": selection["flatProfile"], "selected": json.loads(selection["profile"])}
    grouped = strata(rows)
    missing_groups = strata(unlabelled)
    names = sorted(set(grouped) | set(missing_groups))
    arms = defaultdict(list)
    for row in rows:
        arms[row["sampling"]].append(row)
    missing_usage = [row for row in rows if row.get("usage") is None]
    sensitivity = {}
    for usage, name in ((0.1, "unknownAtStrictUsage"), (0.9, "unknownAtLooseUsage")):
        sensitivity[name] = table([{**row, "usage": usage} if row.get("usage") is None else row for row in rows], profiles)
    return {
        "composition": {name: composition(group) for name, group in grouped.items()},
        "labelCoverage": {name: {"accepted": len(grouped.get(name, [])), "unresolvedOrUnpaired": len(missing_groups.get(name, []))} for name in names},
        "missingLabelBounds": {name: {definition: missing_label_bounds(rows, unlabelled, profile, definition)
                                      for definition in ("union", "product")} for name, profile in profiles.items()},
        "missingLabelBoundsByStratum": {name: {policy: missing_label_bounds(grouped.get(name, []), missing_groups.get(name, []), profile)
                                                for policy, profile in profiles.items()} for name in names},
        "all": table(rows, profiles),
        "successfulJudgmentsOnly": table([row for row in rows if row.get("ok")], profiles),
        "byStratum": {name: table(group, profiles) for name, group in grouped.items()},
        "bySamplingArm": {name: table(group, profiles) for name, group in arms.items()},
        "agreedNonPivotOnly": table([row for row in rows if row["label"].get("pivot") is False], profiles),
        "unknownPivotRows": sum(row["label"].get("pivot") is None for row in rows),
        "unknownUsageRows": len(missing_usage), "usageSensitivity": sensitivity,
        "latencyOverProductionTimeout": sum(row.get("latencyMs", 0) > 2000 for row in rows),
        "models": sorted({row["model"] for row in rows if row.get("ok")}),
        "pairedSessionBootstrap": {name: clustered_difference(rows, profile, profiles["selected"], repeats)
                                   for name, profile in profiles.items() if name != "selected"},
        "profiles": profiles,
        "limitations": [
            "Sparse checkpoints do not reconstruct stateful cooldown, snooze, dedup or compaction history; these are stateless gate results.",
            "Unknown historical usage uses the strictest floor and is also reported under strict/loose sensitivity assumptions.",
            "Enriched sampling precision is not production precision.",
            "Missing-label bounds allow every unresolved or truth-unknown row either binary truth; they are sensitivity extremes, not confidence intervals.",
            "Rank statistics exclude failed judgments; end-to-end precision/recall count them as no hint.",
            "This evaluates hint timing, not actual compaction fidelity or autonomous task success.",
        ],
    }


def export_legacy(directory, rows, profiles):
    # Only product-known labels go to the legacy metrics/schedule/earn diagnostics.
    known = [row for row in rows if row["label"]["safe_to_compact"] is not None]
    def write(name, values):
        with private_path(directory / name).open("x") as stream:
            for value in values:
                stream.write(json.dumps(value) + "\n")
    write("labels.jsonl", [{"id": row["id"], **row["label"], "stratum": row["stratum"]} for row in known])
    write("checkpoints.jsonl", [{"id": row["id"], "stratum": row["stratum"], "sampling": row["sampling"]} for row in known])
    for name, profile in profiles.items():
        results = []
        for row in known:
            scored = decision(row, profile)
            result = {key: value for key, value in row.items() if key != "label"}
            result.update(scored, auto=scored["hint"])
            result["answers"] = {"decision": {"probabilities": {"positive": scored["score"]}}}
            results.append(result)
        write(f"{name}.jsonl", results)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("checkpoints", "labels", "results", "selection", "output"):
        parser.add_argument(name, type=Path)
    parser.add_argument("--bootstrap", type=int, default=2000)
    parser.add_argument("--legacy-output", type=Path)
    args = parser.parse_args()
    if args.bootstrap < 1:
        raise ValueError("Bootstrap count must be positive")
    selection = json.loads(args.selection.read_text())
    if selection["checkpointHash"] != sha(args.checkpoints.read_text()):
        raise ValueError("Holdout selection does not match frozen checkpoint bytes")
    checkpoints, labels, results = jsonl(args.checkpoints), jsonl(args.labels), jsonl(args.results)
    rows = join_rows(checkpoints, labels, results, "holdout")
    accepted_ids = {row["id"] for row in rows}
    results_by_id = {row["id"]: row for row in results}
    unlabelled = [{**results_by_id.get(cp["id"], {"ok": False}), "usage": cp.get("contextUsage"),
                   "stratum": cp["stratum"]} for cp in checkpoints if cp["split"] == "holdout" and cp["id"] not in accepted_ids]
    summary = report(rows, selection, args.bootstrap, unlabelled)
    save(args.output, summary)
    if args.legacy_output:
        export_legacy(args.legacy_output, rows, summary["profiles"])
    print("Holdout aggregate report written. Per-checkpoint diagnostics remain private.")


if __name__ == "__main__":
    main()
