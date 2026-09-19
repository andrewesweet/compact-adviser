#!/usr/bin/env python3
"""Freeze a qualified session-level holdout from a previously observed labelled corpus."""
import argparse
import json
from pathlib import Path
from collate import collate
from label import private_path, save
from split import hash_key, session_groups


def jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def write_jsonl(path, rows):
    with private_path(path).open("x") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("old", type=Path, help="Directory containing checkpoints.jsonl and labels-{fable,astra}.jsonl")
    parser.add_argument("sessions", type=Path, help="Session metadata from the current offline build")
    parser.add_argument("output", type=Path)
    parser.add_argument("--seed", required=True)
    parser.add_argument("--stratum", default="supervision")
    args = parser.parse_args()
    checkpoints = jsonl(args.old / "checkpoints.jsonl")
    first = {row["id"]: row for row in jsonl(args.old / "labels-fable.jsonl")}
    second = {row["id"]: row for row in jsonl(args.old / "labels-astra.jsonl")}
    paired = collate([(first[key], second[key]) for key in sorted(first.keys() & second.keys())])
    accepted = {row["id"]: row for row in paired["accepted"]}
    sessions = json.loads(args.sessions.read_text())
    by_file = {row["source"]["file"]: row for row in sessions}
    groups = session_groups(sessions)
    active = {groups[by_file[row["sessionFile"]]["session"]] for row in checkpoints if row["id"] in accepted}
    if len(active) < 3:
        raise ValueError("Need at least three independent session groups for train/validation/holdout")
    ordered = sorted(active, key=lambda value: hash_key(args.seed, value))
    rows = []
    for row in checkpoints:
        if row["id"] not in accepted:
            continue
        session = by_file[row["sessionFile"]]["session"]
        group = groups[session]
        split = "holdout" if group == ordered[0] else "validation" if group == ordered[1] else "train"
        rows.append({**row, "session": session, "group": group, "split": split, "stratum": args.stratum,
                     "sampling": "spread", "priorObserved": True})
    manifest = {"seed": args.seed, "selection": "minimum seeded hash of eligible linked-session group; next hash validation; remaining train",
                "caveat": "Prior aggregate outcomes were observed. This is a qualified holdout, not a pristine test corpus.",
                "groups": ordered, "rows": [{key: row[key] for key in ("id", "session", "group", "split")} for row in rows]}
    save(args.output / "manifest.json", manifest)
    write_jsonl(args.output / "checkpoints.jsonl", rows)
    write_jsonl(args.output / "labels.jsonl", [accepted[row["id"]] for row in rows])
    save(args.output / "unresolved.json", paired["unresolved"])
    print(f"Assigned {len(rows)} previously agreed rows across {len(active)} session groups. Prior-observation caveat required.")


if __name__ == "__main__":
    main()
