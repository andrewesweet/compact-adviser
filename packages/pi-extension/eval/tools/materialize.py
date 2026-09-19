#!/usr/bin/env python3
"""Join a frozen manifest to a checkpoint bank without changing the selected population."""
import argparse
import json
from pathlib import Path
from reuse import jsonl, write_jsonl


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkpoints", type=Path)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    bank = {row["id"]: row for row in jsonl(args.checkpoints)}
    selected = json.loads(args.manifest.read_text())["rows"]
    if len({row["id"] for row in selected}) != len(selected):
        raise ValueError("Duplicate selected id")
    rows = []
    for selection in selected:
        original = bank[selection["id"]]
        if original["session"] != selection["session"] or original["stratum"] != selection["stratum"]:
            raise ValueError("Manifest disagrees with checkpoint provenance")
        rows.append({**original, **selection})
    write_jsonl(args.output, rows)
    print(f"Materialized {len(rows)} rows.")


if __name__ == "__main__":
    main()
