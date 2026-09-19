#!/usr/bin/env python3
"""Retain every pair; accept only phase and safety agreement (including explicit unknown)."""
import argparse
from collections import Counter
import json
from pathlib import Path
from label import PROVIDERS, call_directory, save, validate_label

FIELDS = ("phase_gold", "safe_to_compact", "context_need", "continuation_gold", "task_boundary", "pivot")


def collate(pairs):
    accepted, unresolved = [], []
    agreement = Counter()
    for first, second in pairs:
        validate_label(first, first["id"])
        validate_label(second, first["id"])
        flags = {key: first[key] == second[key] for key in FIELDS}
        agreement.update(key for key, value in flags.items() if value)
        row = {"id": first["id"], "labels": [first, second], "agreement": flags}
        if flags["phase_gold"] and flags["safe_to_compact"]:
            # Do not promote one provider's auxiliary values into joint truth.
            row.update({key: first[key] if flags[key] else None for key in FIELDS})
            accepted.append(row)
        else:
            unresolved.append(row)
    return {"accepted": accepted, "unresolved": unresolved, "agreementCounts": dict(agreement),
            "pairs": len(pairs)}


def collect(root, round_id="initial", ids=None):
    # Filter directory names before reading responses, so development collation never
    # loads holdout labels into the optimizer's input process.
    if ids is None:
        ids = {path.parent.name.split("-")[0] for path in root.glob("raw/*/*/result.json")}
    maps = {provider: {} for provider in PROVIDERS}
    failures = []
    for checkpoint_id in sorted(ids):
        for provider in PROVIDERS:
            prompt_hash = None
            for attempt in (round_id, round_id + "retry"):
                path = call_directory(root, provider, checkpoint_id, attempt) / "result.json"
                if not path.exists():
                    break
                row = json.loads(path.read_text())
                if prompt_hash is not None and row["promptHash"] != prompt_hash:
                    raise ValueError("Retry used different prompt bytes")
                prompt_hash = row["promptHash"]
                if row.get("status") == "parse-failed":
                    failures.append({"id": checkpoint_id, "provider": provider, "roundId": attempt})
                    continue
                maps[provider][checkpoint_id] = validate_label(row["label"], checkpoint_id)
                break
    complete = sorted(set(maps["fable"]) & set(maps["astra"]))
    result = collate([(maps["fable"][key], maps["astra"][key]) for key in complete])
    result["unpaired"] = sorted(set(ids) - set(complete))
    result["parseFailures"] = failures
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--round-id", default="initial")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--split", choices=("development", "holdout", "train", "validation"))
    args = parser.parse_args()
    if args.split and not args.manifest:
        raise ValueError("Split filtering requires the frozen manifest")
    ids = None
    if args.manifest:
        rows = json.loads(args.manifest.read_text())["rows"]
        ids = {row["id"] for row in rows if not args.split or
               (row["split"] != "holdout" if args.split == "development" else row["split"] == args.split)}
    result = collect(args.directory, args.round_id, ids)
    save(args.output, result)
    print(json.dumps({key: result[key] for key in ("pairs", "agreementCounts")}))
    print(f"Accepted {len(result['accepted'])}; unresolved {len(result['unresolved'])}; unpaired {len(result['unpaired'])}.")


if __name__ == "__main__":
    main()
