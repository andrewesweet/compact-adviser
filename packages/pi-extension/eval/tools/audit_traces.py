#!/usr/bin/env python3
"""Read a local MLflow trace inventory without fetching transcript-bearing span artifacts."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import urllib.parse
import urllib.request
from label import save


def audit(base_url, experiment, since):
    base = urllib.parse.urlparse(base_url)
    if base.scheme != "http" or base.hostname not in ("localhost", "127.0.0.1") or base.username or base.password:
        raise ValueError("Only a local, unauthenticated MLflow server is supported")
    cutoff = int(datetime.fromisoformat(since.replace("Z", "+00:00")).timestamp() * 1000)
    query = urllib.parse.urlencode({"experiment_ids": [experiment], "max_results": 100,
                                  "filter": f"timestamp_ms > {cutoff}"}, doseq=True)
    with urllib.request.urlopen(f"{base_url.rstrip('/')}/api/2.0/mlflow/traces?{query}", timeout=20) as response:
        value = json.load(response)
    rows = []
    for trace in value.get("traces", []):
        metadata = {row["key"]: row["value"] for row in trace.get("request_metadata", [])}
        rows.append({"session": metadata.get("mlflow.trace.session"),
                     "cwd": metadata.get("mlflow.trace.working_directory"),
                     "timestampMs": trace.get("timestamp_ms")})
    return {"cutoffMs": cutoff, "rows": rows, "complete": not bool(value.get("next_page_token"))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--since", required=True, help="UTC ISO timestamp")
    parser.add_argument("--experiment", default="1")
    parser.add_argument("--url", default="http://127.0.0.1:5000")
    args = parser.parse_args()
    result = audit(args.url, args.experiment, args.since)
    save(args.output, result)
    print(f"Inventoried {len(result['rows'])} traces. Complete page: {result['complete']}.")


if __name__ == "__main__":
    main()
