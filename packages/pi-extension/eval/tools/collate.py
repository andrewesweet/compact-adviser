#!/usr/bin/env python3
"""Retain every pair; accept only phase and safety agreement (including explicit unknown)."""
import argparse
from collections import Counter
import json
from pathlib import Path
from label import PROVIDERS, save, validate_label

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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--round-id", default="initial")
    args = parser.parse_args()
    maps = {}
    for provider in PROVIDERS:
        maps[provider] = {}
        for path in (args.directory / "raw" / provider).glob("*/result.json"):
            row = json.loads(path.read_text())
            if row.get("roundId", "initial") == args.round_id:
                maps[provider][row["id"]] = row["label"]
    complete = sorted(set(maps["fable"]) & set(maps["astra"]))
    result = collate([(maps["fable"][key], maps["astra"][key]) for key in complete])
    result["unpaired"] = sorted(set(maps["fable"]) ^ set(maps["astra"]))
    save(args.output, result)
    print(json.dumps({key: result[key] for key in ("pairs", "agreementCounts")}))
    print(f"Accepted {len(result['accepted'])}; unresolved {len(result['unresolved'])}; unpaired {len(result['unpaired'])}.")


if __name__ == "__main__":
    main()
