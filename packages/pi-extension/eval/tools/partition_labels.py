#!/usr/bin/env python3
"""Prepare private label inputs separately from fitting; holdout export requires a frozen selection."""
import argparse
import json
from pathlib import Path
from label import private_path
from optimise import jsonl, sha


def partition_labels(checkpoints, bundles, split):
    assignments = {row["id"]: row["split"] for row in checkpoints}
    output = {}
    for bundle in bundles:
        for row in bundle:
            key = row["id"]
            if key not in assignments:
                raise ValueError("Label is absent from the frozen checkpoint set")
            selected = assignments[key] != "holdout" if split == "development" else assignments[key] == split
            if not selected:
                continue
            if key in output:
                raise ValueError("Duplicate label identity")
            output[key] = row
    return [output[key] for key in sorted(output)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkpoints", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("split", choices=("train", "validation", "development", "holdout"))
    parser.add_argument("labels", nargs="+", type=Path)
    parser.add_argument("--selection", type=Path)
    args = parser.parse_args()
    if args.split == "holdout":
        if not args.selection or json.loads(args.selection.read_text())["checkpointHash"] != sha(args.checkpoints.read_text()):
            raise ValueError("Holdout labels remain locked until a matching selection is frozen")
    bundles = []
    for path in args.labels:
        if path.suffix == ".json":
            bundles.append(json.loads(path.read_text())["accepted"])
        else:
            bundles.append(jsonl(path))
    rows = partition_labels(jsonl(args.checkpoints), bundles, args.split)
    with private_path(args.output).open("x") as stream:
        for row in rows:
            stream.write(json.dumps(row) + "\n")
    print(f"Prepared {len(rows)} labels for {args.split}; source disagreements remain in their collation files.")


if __name__ == "__main__":
    main()
