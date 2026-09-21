#!/usr/bin/env python3
"""Frozen numeric search over cached judge answers; no model calls or expression eval."""
import argparse
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
from label import save

SHIPPED = {"version": 1, "coordinationWeight": 0.5, "floors": [[0.1, 0.9], [0.9, 0.5]]}
WEIGHTS = (0, 0.25, 0.5, 0.75, 1)
FLOORS = tuple(i / 20 for i in range(8, 20))
FLAT_BASELINE = "all distinct successful validation scores plus zero and one, using the shipped coordination weight"


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def jsonl(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def family():
    return [{"version": 1, "coordinationWeight": weight,
             "floors": [[0, strict]] if strict == loose else [[0.1, strict], [0.9, loose]]}
            for weight in WEIGHTS for strict in FLOORS for loose in FLOORS if strict >= loose]


def floor_for(usage, profile):
    points = profile["floors"]
    if type(usage) not in (int, float) or not math.isfinite(usage) or usage <= points[0][0]:
        return points[0][1]
    for left, right in zip(points, points[1:]):
        if usage <= right[0]:
            raw = left[1] - (left[1] - right[1]) * ((usage - left[0]) / (right[0] - left[0]))
            # Match JavaScript Math.round rather than Python's ties-to-even round.
            return math.floor(raw * 1000 + 0.5) / 1000
    return points[-1][1]


def decision(result, profile):
    if not result.get("ok"):
        return {"score": None, "floor": floor_for(result.get("usage"), profile), "hint": False}
    weight = profile["coordinationWeight"]
    score = result["doneP"]["finished"] * (1 - weight + weight * result["shapeP"]["hands_on"])
    floor = floor_for(result.get("usage"), profile)
    return {"score": score, "floor": floor, "hint": score >= floor}


def truth(label, definition):
    phase, safe = label["phase_gold"], label["safe_to_compact"]
    if definition == "product":
        return safe
    if phase == "still_in_progress":
        return False
    if definition == "union" and safe is False:
        return False
    if phase == "completed_checkpoint" and safe is True:
        return True
    return None


def metrics(rows, profile, definition="union"):
    counts = dict(tp=0, fp=0, tn=0, fn=0, unknown=0, unknownHints=0, hints=0,
                  unsafeFp=0, midTaskFp=0, unsafeMidTaskFp=0, errors=0, boundaryPositive=0, boundaryTp=0)
    for row in rows:
        hint = decision(row, profile)["hint"]
        gold = truth(row["label"], definition)
        counts["hints"] += hint
        counts["errors"] += not row.get("ok")
        counts["unsafeFp"] += hint and row["label"]["safe_to_compact"] is False
        counts["midTaskFp"] += hint and row["label"]["phase_gold"] == "still_in_progress"
        counts["unsafeMidTaskFp"] += hint and row["label"]["phase_gold"] == "still_in_progress" and row["label"]["safe_to_compact"] is False
        if gold is None:
            counts["unknown"] += 1
            counts["unknownHints"] += hint
        else:
            counts["tp" if gold and hint else "fn" if gold else "fp" if hint else "tn"] += 1
            if gold and row["label"].get("task_boundary") is True:
                counts["boundaryPositive"] += 1
                counts["boundaryTp"] += hint
    for key, numerator, denominator in (
        ("precision", counts["tp"], counts["tp"] + counts["fp"]),
        ("recall", counts["tp"], counts["tp"] + counts["fn"]),
        ("boundaryRecall", counts["boundaryTp"], counts["boundaryPositive"]),
    ):
        counts[key] = numerator / denominator if denominator else None
    return counts


def strata(rows):
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["stratum"]].append(row)
    return dict(grouped)


def macro_recall(rows, profile, definition="union"):
    recalls = [metrics(group, profile, definition)["recall"] for group in strata(rows).values()]
    available = [value for value in recalls if value is not None]
    return sum(available) / len(available) if available else None


def eligible(rows, profile, baselines=(), protected=()):
    measured = metrics(rows, profile)
    if measured["precision"] is None or measured["precision"] < 0.95:
        return False
    if any(measured["unsafeFp"] > metrics(rows, base)["unsafeFp"] for base in baselines):
        return False
    for name, group in strata(rows).items():
        if name not in protected:
            continue
        current = metrics(group, profile)["precision"]
        if current is None:  # No protected hint cannot introduce a false positive.
            continue
        prior = [metrics(group, base)["precision"] for base in baselines]
        required = max([0.95] + [value for value in prior if value is not None])
        if current < required:
            return False
    return True


def rank(rows, profile, baselines=(), protected=()):
    measured = metrics(rows, profile)
    valid = eligible(rows, profile, baselines, protected)
    precision = measured["precision"] if measured["precision"] is not None else -1
    recall = macro_recall(rows, profile) or 0
    return (valid, recall if valid else precision, precision if valid else recall,
            -measured["unsafeFp"], profile == SHIPPED, -len(profile["floors"]),
            -abs(profile["coordinationWeight"] - 0.5), profile["floors"][0][1])


def select(train, validation, candidates, protected=()):
    if not train or not validation:
        raise ValueError("Training and validation rows are required")
    thresholds = {0, 1} | {decision(row, SHIPPED)["score"] for row in validation if row.get("ok")}
    flat = [{"version": 1, "coordinationWeight": 0.5, "floors": [[0, floor]]} for floor in sorted(thresholds)]
    best_flat = max(flat, key=lambda profile: rank(validation, profile, [SHIPPED], protected))
    # Fix the shortlist on training only. Each weight retains four candidates.
    shortlist = [SHIPPED, best_flat]
    for weight in WEIGHTS:
        matching = [profile for profile in candidates if profile["coordinationWeight"] == weight]
        shortlist.extend(sorted(matching, key=lambda profile: rank(train, profile, [SHIPPED], protected), reverse=True)[:4])
    shortlist = list({canonical(profile): profile for profile in shortlist}.values())
    selected = max(shortlist, key=lambda profile: rank(validation, profile, [SHIPPED, best_flat], protected))
    if not eligible(validation, selected, [SHIPPED, best_flat], protected):
        selected = SHIPPED
    return {"profile": canonical(selected), "flatProfile": best_flat, "shortlist": shortlist,
            "protectedStrata": sorted(protected),
            "flatMeetsConstraints": eligible(validation, best_flat, [SHIPPED], protected),
            "selectedMeetsConstraints": eligible(validation, selected, [SHIPPED, best_flat], protected),
            "trainingRows": len(train), "validationRows": len(validation),
            "validation": {name: metrics(validation, profile) for name, profile in
                           (("shipped", SHIPPED), ("flat", best_flat), ("selected", selected))}}


def join_rows(checkpoints, labels, results, split):
    by_id = {row["id"]: row for row in checkpoints}
    if len(by_id) != len(checkpoints):
        raise ValueError("Duplicate checkpoint identity")
    groups = {}
    for row in checkpoints:
        if row["group"] in groups and groups[row["group"]] != row["split"]:
            raise ValueError("A session group crosses splits")
        groups[row["group"]] = row["split"]
    label_map = {row["id"]: row for row in labels}
    result_map = {row["id"]: row for row in results}
    if len(label_map) != len(labels) or len(result_map) != len(results):
        raise ValueError("Duplicate labels or results")
    expected = {key for key, row in by_id.items() if (row["split"] == "holdout") == (split == "holdout")}
    if (set(label_map) | set(result_map)) - expected:
        raise ValueError("Input contains labels/results from outside the selected partition")
    output = []
    for key in sorted(set(label_map) & expected):
        cp = by_id[key]
        result = result_map.get(key, {"ok": False, "error": "missing judgment"})
        output.append({**result, "id": key, "group": cp["group"], "split": cp["split"],
                       "stratum": cp["stratum"], "sampling": cp.get("sampling", "spread"),
                       "usage": cp.get("contextUsage"), "usageSource": cp.get("usageSource", "unavailable"),
                       "label": label_map[key]})
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    freeze = sub.add_parser("freeze")
    freeze.add_argument("checkpoints", type=Path)
    freeze.add_argument("output", type=Path)
    freeze.add_argument("--protect", action="append", default=[], metavar="STRATUM",
                        help="stratum whose precision may not regress against either baseline; repeatable")
    fit = sub.add_parser("select")
    for name in ("checkpoints", "labels", "results", "plan", "output"):
        fit.add_argument(name, type=Path)
    args = parser.parse_args()
    checkpoint_hash = sha(args.checkpoints.read_text())
    if args.command == "freeze":
        known = {row["stratum"] for row in jsonl(args.checkpoints)}
        unknown = sorted(set(args.protect) - known)
        if unknown:
            raise ValueError(f"Protected strata absent from the checkpoint set: {unknown}")
        save(args.output, {"checkpointHash": checkpoint_hash, "candidates": family(), "flatBaseline": FLAT_BASELINE,
                           "protectedStrata": sorted(set(args.protect)),
                           "objective": "macro union recall at >=95% validation precision; no protected-stratum precision or unsafe-FP regression",
                           "shortlist": "four candidates per weight ranked on training, plus shipped and the validation-selected full flat baseline",
                           "flatFallback": "if no flat satisfies constraints, highest precision with nonempty hints, then macro recall; report failure to meet constraints"})
        print(f"Froze {len(family())} numeric candidates. No model calls.")
        return
    plan = json.loads(args.plan.read_text())
    if plan["checkpointHash"] != checkpoint_hash or plan["candidates"] != family() or plan.get("flatBaseline") != FLAT_BASELINE:
        raise ValueError("Checkpoint bytes or search family changed after the plan was frozen")
    if "protectedStrata" not in plan:
        raise ValueError("Plan names no protected strata; refreeze with --protect")
    rows = join_rows(jsonl(args.checkpoints), jsonl(args.labels), jsonl(args.results), "development")
    selected = select([row for row in rows if row["split"] == "train"],
                      [row for row in rows if row["split"] == "validation"], plan["candidates"], plan["protectedStrata"])
    selected.update(checkpointHash=checkpoint_hash, planHash=sha(args.plan.read_text()),
                    developmentLabelsHash=sha(args.labels.read_text()), developmentResultsHash=sha(args.results.read_text()))
    save(args.output, selected)
    print("Candidate and flat baseline frozen. Holdout was not loaded.")


if __name__ == "__main__":
    main()
