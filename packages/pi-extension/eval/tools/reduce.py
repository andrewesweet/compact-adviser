#!/usr/bin/env python3
"""Reduce training spend without changing a frozen validation or holdout population."""
import argparse
from collections import Counter
import copy
import hashlib
import json
from pathlib import Path
from label import save
from split import hash_key


def reduce_training(manifest, targets, pinned_ids):
    result = copy.deepcopy(manifest)
    pinned = set(pinned_ids)
    by_id = {row["id"]: row for row in manifest["rows"]}
    if any(key not in by_id or by_id[key]["split"] != "train" for key in pinned):
        raise ValueError("Pinned rows must be existing training rows")
    keep = set(pinned)
    strata = {row["stratum"] for row in manifest["rows"] if row["split"] == "train"}
    if set(targets) != strata:
        raise ValueError("Supply an explicit training target for every stratum")
    for stratum, target in targets.items():
        candidates = [row for row in manifest["rows"] if row["stratum"] == stratum and row["split"] == "train"]
        pinned_count = sum(row["id"] in pinned for row in candidates)
        if type(target) is not int or target < pinned_count or target > len(candidates):
            raise ValueError("Training target cannot remove pinned rows or add candidates")
        rest = sorted((row for row in candidates if row["id"] not in pinned),
                      key=lambda row: hash_key(manifest["plan"]["seed"], row["id"]))
        keep.update(row["id"] for row in rest[:target - pinned_count])
        result["plan"]["targets"][stratum]["train"] = target
    result["rows"] = [row for row in manifest["rows"] if row["split"] != "train" or row["id"] in keep]
    result["reduction"] = {"rule": "keep pinned training rows, then smallest seeded id hashes within each stratum",
                           "trainingTargets": targets, "pinnedIds": sorted(pinned)}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("targets", type=Path, help="JSON mapping each stratum to its reduced training count")
    parser.add_argument("pinned", type=Path, help="JSON array of pilot ids that must remain")
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    raw = args.manifest.read_bytes()
    result = reduce_training(json.loads(raw), json.loads(args.targets.read_text()), json.loads(args.pinned.read_text()))
    result["parentManifestHash"] = hashlib.sha256(raw).hexdigest()
    save(args.output, result)
    print(json.dumps(dict(Counter(row["split"] for row in result["rows"]))))


if __name__ == "__main__":
    main()
