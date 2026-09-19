#!/usr/bin/env python3
"""Freeze session-grouped samples before labels or fitting. No model calls."""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re
from label import save


def hash_key(seed, value):
    return hashlib.sha256(f"{seed}:{value}".encode()).hexdigest()


def session_groups(sessions):
    parent = {row["session"]: row["session"] for row in sessions}
    def find(key):
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key
    seen = {}
    for row in sessions:
        identities = [f"event:{row['source']['host']}:{value}" for value in row["eventIds"]]
        identities += [f"state:{value}" for value in row["stateHashes"]]
        for identity in identities:
            if identity in seen:
                first, second = find(row["session"]), find(seen[identity])
                parent[max(first, second)] = min(first, second)
            else:
                seen[identity] = row["session"]
    return {key: find(key) for key in parent}


def split_sessions(sessions, seed):
    groups = session_groups(sessions)
    strata = defaultdict(set)
    for row in sessions:
        strata[groups[row["session"]]].add(row["source"]["stratum"])
    by_stratum = defaultdict(list)
    for group, values in strata.items():
        by_stratum[tuple(sorted(values))].append(group)
    assignment = {}
    for values in by_stratum.values():
        ordered = sorted(values, key=lambda value: hash_key(seed, value))
        # Whole groups only; a tiny stratum remains training, not fake holdout evidence.
        validation_count = len(ordered) // 4
        holdout_count = len(ordered) // 4
        for index, group in enumerate(ordered):
            split = "holdout" if index < holdout_count else "validation" if index < holdout_count + validation_count else "train"
            assignment[group] = split
    return {session: {"group": group, "split": assignment[group]} for session, group in groups.items()}


def difficulty(row):
    state = row.get("state", {})
    recent = state.get("recent", []) if isinstance(state, dict) else []
    text = " ".join(str(message.get("text", "")) for message in recent[-3:] if message.get("role") == "assistant")
    return bool(row.get("future")) and bool(re.search(r"\b(still|retry|running|next step|will continue|waiting|failed|in progress)\b", text, re.I))


def sample_rows(rows, assignments, targets, seed):
    selected = []
    for stratum, counts in targets.items():
        for split, count in counts.items():
            candidates = [row for row in rows if row["stratum"] == stratum and assignments[row["session"]]["split"] == split]
            # Round-robin sessions prevents a long transcript dominating the sample.
            by_session = defaultdict(list)
            for row in candidates:
                by_session[row["session"]].append(row)
            spread = []
            ordered_sessions = sorted(by_session, key=lambda value: hash_key(seed, value))
            for session in ordered_sessions:
                by_session[session].sort(key=lambda row: hash_key(seed, row["id"]))
            while any(by_session.values()):
                for session in ordered_sessions:
                    if by_session[session]:
                        spread.append(by_session[session].pop())
            hard_count = count // (3 if split == "holdout" else 2)
            natural = spread[:count - hard_count]
            natural_ids = {row["id"] for row in natural}
            hard = [row for row in spread if row["id"] not in natural_ids and difficulty(row)][:hard_count]
            hard_ids = {row["id"] for row in hard}
            # If mining finds too few candidates, retain the deficit rather than invent negatives.
            for row in natural + hard:
                selected.append({**row, **assignments[row["session"]],
                                 "sampling": "targeted-hard" if row["id"] in hard_ids else "spread"})
    return selected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bank", type=Path)
    parser.add_argument("plan", type=Path, help="JSON: seed and per-stratum train/validation/holdout candidate counts")
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    sessions = json.loads((args.bank / "sessions.json").read_text())
    rows = [json.loads(line) for line in (args.bank / "checkpoints.jsonl").read_text().splitlines() if line]
    plan = json.loads(args.plan.read_text())
    assignments = split_sessions(sessions, plan["seed"])
    selected = sample_rows(rows, assignments, plan["targets"], plan["seed"])
    manifest = {"version": 1, "plan": plan, "sessions": assignments,
                "bankHashes": {name: hashlib.sha256((args.bank / name).read_bytes()).hexdigest() for name in ("sessions.json", "checkpoints.jsonl")},
                "rows": [{key: row[key] for key in ("id", "session", "group", "split", "stratum", "sampling")} for row in selected]}
    save(args.output, manifest)
    print(f"Frozen {len(selected)} rows from {len(assignments)} sessions; inspect aggregate deficits before labelling.")


if __name__ == "__main__":
    main()
