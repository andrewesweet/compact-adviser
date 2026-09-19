#!/usr/bin/env python3
"""Collect transcript usage and auto-compaction evidence without inferring a threshold."""
import argparse
from collections import Counter
import json
from pathlib import Path
from label import save


def collect(checkpoints):
    files = {}
    for row in checkpoints:
        files.setdefault(row["sessionFile"], row["session"])
    events, checkpoint_evidence = [], []
    models = Counter()
    for file, session in files.items():
        records = []
        for line in Path(file).read_text().splitlines():
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        by_id = {row.get("uuid", row.get("id")): row for row in records}
        for checkpoint in (row for row in checkpoints if row["sessionFile"] == file):
            record = by_id.get(checkpoint["entryId"], {})
            model = record.get("message", {}).get("model", record.get("model", "unknown"))
            models[model] += 1
            checkpoint_evidence.append({"id": checkpoint["id"], "session": session, "model": model,
                                        "contextTokens": checkpoint["contextTokens"]})
        for record in records:
            metadata = record.get("compactMetadata")
            if isinstance(metadata, dict):
                events.append({"session": session, "timestamp": record.get("timestamp"),
                               "trigger": metadata.get("trigger"), "preTokens": metadata.get("preTokens")})
            elif record.get("type") == "compaction":
                events.append({"session": session, "timestamp": record.get("timestamp"),
                               "tokensBefore": record.get("tokensBefore")})
    return {"sessions": len(files), "modelCounts": dict(models), "checkpoints": checkpoint_evidence,
            "compactionEvents": events,
            "limitation": "Observed pre-compaction usage is evidence, not the exact configured threshold."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkpoints", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    rows = [json.loads(line) for line in args.checkpoints.read_text().splitlines() if line]
    result = collect(rows)
    save(args.output, result)
    print(json.dumps({key: result[key] for key in ("sessions", "modelCounts")}))


if __name__ == "__main__":
    main()
