#!/usr/bin/env python3
"""Seeded bounded adjudication preparation, then partition-specific agreement merging."""
import argparse
import hashlib
import json
from pathlib import Path
from collate import collect
from label import PROVIDERS, call_directory, ledger, prompt_for, save


def choose(unresolved, seed, maximum):
    if maximum < 0:
        raise ValueError("Adjudication limit cannot be negative")
    return sorted(unresolved, key=lambda row: hashlib.sha256(f"{seed}:{row['id']}".encode()).hexdigest())[:maximum]


def merge(initial, adjudicated):
    original = {row["id"] for row in initial["unresolved"]}
    reconsidered = {row["id"] for row in adjudicated["accepted"] + adjudicated["unresolved"]} | set(adjudicated.get("unpaired", []))
    if reconsidered - original:
        raise ValueError("Adjudication may only reconsider initial disagreements")
    resolved = {row["id"] for row in adjudicated["accepted"]}
    return {
        "accepted": initial["accepted"] + adjudicated["accepted"],
        "unresolved": [row for row in initial["unresolved"] if row["id"] not in resolved],
        "unpaired": initial.get("unpaired", []),
        "parseFailures": initial.get("parseFailures", []) + adjudicated.get("parseFailures", []),
        "initialPairs": initial["pairs"], "initialAccepted": len(initial["accepted"]),
        "adjudicatedPairs": adjudicated["pairs"], "newlyAccepted": len(resolved),
        "adjudicationEvidence": adjudicated,
    }


def prepare(root, manifest, plan, seed, maximum, retry_reserve):
    ids = {row["id"] for row in manifest["rows"]}
    results = ledger(root)  # Refuse an in-flight controller or unreconciled attempt.
    for key in ids:
        for provider in PROVIDERS:
            first = call_directory(root, provider, key) / "result.json"
            if not first.exists() or (json.loads(first.read_text()).get("status") == "parse-failed"
                                     and not (call_directory(root, provider, key, "initialretry") / "result.json").exists()):
                raise ValueError("Complete initial acquisition and retries before adjudication")
    available = min(plan["callsPerProvider"][p] - sum(row["provider"] == p for row in results) - retry_reserve for p in PROVIDERS)
    if available < 0 or retry_reserve < 0:
        raise ValueError("Call cap cannot cover the retry reserve")
    initial = collect(root, ids=ids)
    selected = choose(initial["unresolved"], seed, min(maximum, available))
    rubric = (Path(__file__).parents[1] / "README.md").read_text()
    rubric = rubric[rubric.index("## Label schema"):rubric.index("## Gitignore boundary")]
    for row in selected:
        key = row["id"]
        prompt = prompt_for(key, (root / "worksheet" / f"{key}.md").read_text(), rubric, row["labels"])
        save(root / "prompts" / f"{key}-adjudication.json", prompt)
    selected_ids = {row["id"] for row in selected}
    save(root / "adjudication" / "manifest.json", {"seed": seed, "selection": "seeded id hash among initial disagreements; no severity or class targeting",
         "rows": [row for row in manifest["rows"] if row["id"] in selected_ids],
         "initialAccepted": len(initial["accepted"]), "initialUnresolved": len(initial["unresolved"]),
         "retryCallReservePerProvider": retry_reserve})
    save(root / "adjudication" / "plan.json", {**plan, "manifest": "adjudication/manifest.json", "roundId": "adjudication"})
    return len(selected)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("directory", type=Path)
    prep.add_argument("manifest", type=Path)
    prep.add_argument("plan", type=Path)
    prep.add_argument("--seed", required=True)
    prep.add_argument("--max-pairs", type=int, required=True)
    prep.add_argument("--retry-reserve", type=int, default=1)
    combine = sub.add_parser("merge")
    combine.add_argument("initial", type=Path)
    combine.add_argument("adjudicated", type=Path)
    combine.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.command == "prepare":
        count = prepare(args.directory, json.loads(args.manifest.read_text()), json.loads(args.plan.read_text()),
                        args.seed, args.max_pairs, args.retry_reserve)
        print(f"Prepared {count} frozen prompt pairs. No paid calls or per-row labels printed.")
    else:
        result = merge(json.loads(args.initial.read_text()), json.loads(args.adjudicated.read_text()))
        save(args.output, result)
        print(f"Accepted {len(result['accepted'])}; unresolved {len(result['unresolved'])}; unpaired {len(result['unpaired'])}.")


if __name__ == "__main__":
    main()
