#!/usr/bin/env python3
"""Resume frozen prompt pairs under a separately approved programme budget.

The private plan names manifest, forecast allowance, actual-spend ceiling, call
caps and non-labelling reserve. No prompt is regenerated during execution.
"""
import argparse
import json
from pathlib import Path

from label import PROVIDERS, budget_guard, call_directory, ledger, quota_guard, run_one


def run_cohort(root, plan):
    caps = plan["callsPerProvider"]
    reserve = plan["reserveUsd"]
    reserves = {(p, window): draw for p, window, draw in plan["quotaReserves"]}
    manifest = json.loads((root / plan["manifest"]).read_text())
    ids = [row["id"] for row in manifest["rows"]]
    if len(set(ids)) != len(ids) or any(not i.isalnum() for i in ids):
        raise ValueError("Invalid frozen cohort ids")
    # Finish a persisted malformed answer first, then retain manifest order.
    failed = {r["id"] for r in ledger(root) if r.get("status") == "parse-failed"}
    ids.sort(key=lambda i: i not in failed)
    pending = [i for i in ids if any(
        not (call_directory(root, p, i) / "result.json").exists()
        or (json.loads((call_directory(root, p, i) / "result.json").read_text()).get("status") == "parse-failed"
            and not (call_directory(root, p, i, "initialretry") / "result.json").exists())
        for p in PROVIDERS)]
    for offset in range(0, len(pending), 5):
        quota_guard(root, reserves)
        for checkpoint_id in pending[offset:offset + 5]:
            prompt = json.loads((root / "prompts" / f"{checkpoint_id}.json").read_text())
            for provider in PROVIDERS:
                for round_id in ("initial", "initialretry"):
                    results = ledger(root)
                    budget_guard(results, plan["forecastAllowanceUsd"], caps, reserve)
                    if sum(r["costUsd"] for r in results) + reserve >= plan["spendingCeilingUsd"]:
                        raise ValueError("Programme spending ceiling reached")
                    cached = (call_directory(root, provider, checkpoint_id, round_id) / "result.json").exists()
                    if not cached and sum(r["provider"] == provider for r in results) >= caps[provider]:
                        raise ValueError("Provider call cap reached")
                    result = run_one(root, provider, checkpoint_id, prompt, round_id=round_id)
                    print(f"{provider}: {result['tokens']} tokens ${result['costUsd']:.6f} {result.get('status', 'labelled')}", flush=True)
                    if result.get("status") != "parse-failed":
                        break
        quota_guard(root, reserves)
    print("Frozen cohort complete; malformed retries remain unresolved.", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("plan", type=Path)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not args.execute:
        print("No paid calls. Pass --execute with an approved private plan.")
        return
    run_cohort(args.directory.resolve(), json.loads(args.plan.read_text()))


if __name__ == "__main__":
    main()
