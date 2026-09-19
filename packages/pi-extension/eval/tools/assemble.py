#!/usr/bin/env python3
"""Combine frozen checkpoint partitions, with explicit historical usage provenance."""
import argparse
import json
import math
from pathlib import Path
from label import private_path


def jsonl(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def assemble(bundles, comparison=()):
    usage = {row["id"]: row for row in comparison}
    if len(usage) != len(comparison):
        raise ValueError("Duplicate usage evidence")
    rows = [dict(row) for bundle in bundles for row in bundle]
    ids = set()
    groups = {}
    for row in rows:
        if row["id"] in ids:
            raise ValueError("Duplicate checkpoint identity")
        ids.add(row["id"])
        if row.get("split") not in ("train", "validation", "holdout") or not row.get("group"):
            raise ValueError("Every checkpoint needs its frozen split and linked-session group")
        if row["group"] in groups and groups[row["group"]] != row["split"]:
            raise ValueError("A session group crosses splits")
        groups[row["group"]] = row["split"]
        old = usage.get(row["id"])
        fraction = row.get("contextUsage")
        if fraction is not None and (type(fraction) not in (float, int) or not math.isfinite(fraction) or fraction < 0 or not row.get("usageSource")):
            raise ValueError("Explicit usage needs a valid fraction and provenance")
        row["contextUsage"] = fraction
        row["usageSource"] = row.get("usageSource", "unavailable; strictest floor; sensitivity bounds required")
        if old is not None:
            if row.get("harness") not in ("claude", "claude-code"):
                raise ValueError("Historical threshold comparison applies only to Claude; Pi uses its model window")
            if old.get("contextTokens") != row.get("contextTokens"):
                raise ValueError("Usage evidence does not match checkpoint token count")
            fraction = old.get("patchedUsage")
            if type(fraction) not in (float, int) or not math.isfinite(fraction) or fraction < 0:
                raise ValueError("Invalid historical usage fraction")
            row["contextUsage"] = fraction
            row["usageSource"] = "historical auto-compact threshold inference, not observed configuration"
    return sorted(rows, key=lambda row: row["id"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("checkpoints", nargs="+", type=Path)
    parser.add_argument("--comparison", type=Path)
    args = parser.parse_args()
    rows = assemble([jsonl(path) for path in args.checkpoints], jsonl(args.comparison) if args.comparison else [])
    with private_path(args.output).open("x") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    print(f"Frozen {len(rows)} checkpoints; {sum(row['contextUsage'] is None for row in rows)} usage fractions unavailable.")


if __name__ == "__main__":
    main()
