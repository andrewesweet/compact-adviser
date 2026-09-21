import json
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from assemble import assemble
from adjudication import choose, merge, prepare
from cohort import run_cohort
from evaluate_profiles import clustered_difference, missing_label_bounds, rank_metrics
from collate import collate, collect
from label import CONTEXT, budget_guard, ledger, parse_response, prompt_for, quota_guard, run_one, validate_label
from optimise import SHIPPED, eligible, family, join_rows, metrics, select
from reduce import reduce_training
import reuse
from split import sample_rows, split_sessions


def fixture(**patch):
    return {"id": "fixture1", "phase_gold": "completed_checkpoint", "context_need": "artifact",
            "continuation_gold": "recoverable", "safe_to_compact": True,
            "pivot": False, "task_boundary": True, "note": "Recovered from an artifact.", **patch}


class LabelTests(unittest.TestCase):
    def test_safety_derivation_and_unknown(self):
        self.assertIsNone(validate_label(fixture(context_need="unknown", safe_to_compact=None), "fixture1")["safe_to_compact"])
        with self.assertRaises(ValueError):
            validate_label(fixture(context_need="older"), "fixture1")
        with self.assertRaises(ValueError):
            validate_label(fixture(safe_to_compact=1), "fixture1")

    def test_joint_truth_retains_disagreement(self):
        result = collate([(fixture(), fixture(context_need="tail")),
                          (fixture(), fixture(phase_gold="still_in_progress"))])
        self.assertEqual(len(result["accepted"]), 1)
        self.assertIsNone(result["accepted"][0]["context_need"])
        self.assertEqual(len(result["unresolved"]), 1)
        self.assertEqual(result["pairs"], 2)

    def test_retry_collation_keeps_failures_and_never_reads_excluded_holdout(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            def result(provider, name, value):
                path = root / "raw" / provider / name / "result.json"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps({"promptHash": "same", **value}))
            result("fable", "fixture1", {"status": "parse-failed", "label": None})
            result("fable", "fixture1-initialretry", {"label": fixture()})
            result("astra", "fixture1", {"label": fixture()})
            for name in ("fixture2", "fixture2-initialretry"):
                result("fable", name, {"status": "parse-failed", "label": None})
            result("astra", "fixture2", {"label": fixture(id="fixture2")})
            result("fable", "holdout3", {"label": "must not be read"})
            output = collect(root, ids={"fixture1", "fixture2"})
            self.assertEqual(output["pairs"], 1)
            self.assertEqual(output["unpaired"], ["fixture2"])
            self.assertEqual(len(output["parseFailures"]), 3)
            self.assertEqual(output["accepted"][0]["id"], "fixture1")

    def test_adjudication_preparation_limits_calls_and_preserves_round_prompts(self):
        Path(".test-tmp").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=".test-tmp") as temporary:
            root = Path(temporary).resolve()
            manifest = {"rows": [{"id": "fixture1", "split": "train"}]}
            (root / "worksheet").mkdir()
            (root / "worksheet/fixture1.md").write_text("Synthetic worksheet")
            for provider, label in (("fable", fixture()), ("astra", fixture(phase_gold="still_in_progress"))):
                directory = root / "raw" / provider / "fixture1"
                directory.mkdir(parents=True)
                (directory / "result.json").write_text(json.dumps({"id": "fixture1", "provider": provider, "promptHash": "same", "costUsd": 0.01, "label": label}))
            plan = {"callsPerProvider": {"fable": 3, "astra": 3}, "reserveUsd": 0,
                    "forecastAllowanceUsd": 10, "spendingCeilingUsd": 9, "quotaReserves": []}
            self.assertEqual(prepare(root, manifest, plan, "fixed", 5, 1), 1)
            actual_plan = json.loads((root / "adjudication/plan.json").read_text())
            frozen_prompt = json.loads((root / "prompts/fixture1-adjudication.json").read_text())
            calls = []
            def fake_call(directory, provider, key, prompt, round_id):
                calls.append((provider, prompt, round_id))
                return {"tokens": 1, "costUsd": 0.01}
            with patch("cohort.quota_guard"), patch("cohort.run_one", side_effect=fake_call):
                run_cohort(root, actual_plan)
            self.assertEqual(calls, [(provider, frozen_prompt, "adjudication") for provider in ("fable", "astra")])
            other = root / "raw/astra/fixture1/result.json"
            record = json.loads(other.read_text()); record["promptHash"] = "different"
            other.write_text(json.dumps(record))
            with self.assertRaisesRegex(ValueError, "different prompt"):
                collect(root, ids={"fixture1"})

    def test_seeded_adjudication_merge_requires_renewed_agreement(self):
        initial = collate([(fixture(), fixture(phase_gold="still_in_progress"))])
        selected = choose(initial["unresolved"], "fixed", 1)
        self.assertEqual(selected, choose(list(reversed(initial["unresolved"])), "fixed", 1))
        self.assertEqual(choose(initial["unresolved"], "fixed", 0), [])
        final = merge(initial, collate([(fixture(), fixture())]))
        self.assertEqual(final["newlyAccepted"], 1)
        self.assertEqual(final["unresolved"], [])
        unresolved = merge(initial, initial)
        self.assertEqual(len(unresolved["unresolved"]), 1)
        self.assertEqual(unresolved["accepted"], [])
        with self.assertRaisesRegex(ValueError, "only reconsider"):
            merge(collate([]), collate([(fixture(), fixture())]))

    def test_missing_label_bounds_do_not_promote_uncertainty_to_truth(self):
        yes = {"ok": True, "usage": 0.5, "doneP": {"finished": 1}, "shapeP": {"hands_on": 1}, "label": fixture()}
        no_hint = {**yes, "doneP": {"finished": 0}}
        unknown = {**yes, "label": fixture(context_need="unknown", safe_to_compact=None)}
        result = missing_label_bounds([yes, no_hint, unknown], [no_hint], SHIPPED)
        self.assertEqual(result["uncertainRows"], 2)
        self.assertEqual(result["uncertainHints"], 1)
        self.assertEqual(result["precisionWorst"], 0.5)
        self.assertEqual(result["precisionBest"], 1)
        self.assertAlmostEqual(result["recallWorst"], 1 / 3)
        self.assertAlmostEqual(result["recallBest"], 2 / 3)

    def test_fable_usage(self):
        row = parse_response("fable", json.dumps({"result": json.dumps(fixture()), "usage": {"input_tokens": 10, "output_tokens": 2}, "total_cost_usd": 0.1}), "fixture1")
        self.assertEqual(row["tokens"], 12)
        self.assertEqual(row["costUsd"], 0.1)

    def test_astra_usage_ignores_streaming_and_duplicate_agent_end(self):
        message = {"role": "assistant", "provider": "openai-codex", "model": "gpt-6-astra", "stopReason": "stop",
                   "usage": {"input": 10, "cacheRead": 4, "cacheWrite": 0, "output": 2},
                   "content": [{"type": "text", "text": json.dumps(fixture())}]}
        events = [{"type": "message_update", "usage": message["usage"]},
                  {"type": "message_end", "message": message}, {"type": "agent_end", "messages": [message]}]
        row = parse_response("astra", "\n".join(map(json.dumps, events)), "fixture1")
        self.assertEqual(row["tokens"], 16)
        self.assertAlmostEqual(row["costUsd"], 0.00012)
        message["usage"] = {}
        with self.assertRaises(ValueError):
            parse_response("astra", json.dumps({"type": "message_end", "message": message}), "fixture1")

    def test_cached_call_uses_exact_prompt_and_never_repeats(self):
        Path(".test-tmp").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=".test-tmp") as temporary:
            root = Path(temporary).resolve()
            answer = json.dumps({"result": json.dumps(fixture()), "usage": {"input_tokens": 3}, "total_cost_usd": 0.01})
            with patch("label.COMMANDS", {"fable": [sys.executable, "-c", f"print({answer!r})"]}):
                first = run_one(root, "fable", "fixture1", "one prompt")
            # No process can run now: reuse must return from durable state.
            with patch("label.COMMANDS", {}):
                self.assertEqual(first, run_one(root, "fable", "fixture1", "one prompt"))
                with self.assertRaises(ValueError):
                    run_one(root, "fable", "fixture1", "changed prompt")

    def test_malformed_label_preserves_usage_without_inventing_truth(self):
        Path(".test-tmp").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=".test-tmp") as temporary:
            root = Path(temporary).resolve()
            answer = json.dumps({"result": '{"id":', "usage": {"input_tokens": 3}, "total_cost_usd": 0.01})
            with patch("label.COMMANDS", {"fable": [sys.executable, "-c", f"print({answer!r})"]}):
                result = run_one(root, "fable", "fixture1", "prompt")
            self.assertEqual(result["status"], "parse-failed")
            self.assertIsNone(result["label"])
            self.assertEqual(ledger(root)[0]["costUsd"], 0.01)
            with patch("label.COMMANDS", {}):
                self.assertEqual(run_one(root, "fable", "fixture1", "prompt"), result)

    def test_incomplete_call_refuses_automatic_retry(self):
        Path(".test-tmp").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=".test-tmp") as temporary:
            root = Path(temporary).resolve()
            with patch("label.COMMANDS", {"fable": [sys.executable, "-c", "print('unparseable')"]}):
                with self.assertRaises(ValueError):
                    run_one(root, "fable", "fixture1", "prompt")
            self.assertTrue((root / "raw/fable/fixture1/stdout.txt").exists())
            with patch("label.COMMANDS", {}):
                with self.assertRaisesRegex(ValueError, "Incomplete paid call"):
                    run_one(root, "fable", "fixture1", "prompt")

    def test_assembly_keeps_usage_inference_explicit_and_unknown_strict(self):
        row = {"id": "fixture1", "group": "group1", "split": "train", "contextTokens": 50000, "harness": "claude"}
        evidence = {"id": "fixture1", "contextTokens": 50000, "patchedUsage": 0.25}
        known = assemble([[row]], [evidence])[0]
        self.assertEqual(known["contextUsage"], 0.25)
        self.assertIn("inference", known["usageSource"])
        self.assertIsNone(assemble([[row]])[0]["contextUsage"])
        pi = {**row, "harness": "pi", "contextUsage": 0.25, "usageSource": "observed model window"}
        self.assertEqual(assemble([[pi]])[0]["contextUsage"], 0.25)
        with self.assertRaisesRegex(ValueError, "only to Claude"):
            assemble([[pi]], [evidence])
        with self.assertRaisesRegex(ValueError, "token count"):
            assemble([[row]], [{**evidence, "contextTokens": 60000}])
        with self.assertRaisesRegex(ValueError, "crosses"):
            assemble([[row, {**row, "id": "fixture2", "split": "holdout"}]])

    def test_linked_sessions_never_cross_splits(self):
        sessions = [{"session": str(i), "source": {"host": "pi", "stratum": "coding"},
                     "eventIds": [str(i)], "stateHashes": [str(i)]} for i in range(8)]
        sessions[1]["eventIds"] = ["0", "1"]
        first = split_sessions(sessions, "seed")
        self.assertEqual(first, split_sessions(list(reversed(sessions)), "seed"))
        self.assertEqual(first["0"], first["1"])
        self.assertEqual({row["split"] for row in first.values()}, {"train", "validation", "holdout"})

    def test_missing_hard_candidates_remain_a_visible_deficit(self):
        rows = [{"id": str(i), "session": str(i), "stratum": "coding", "state": {}, "future": []} for i in range(8)]
        assignments = {str(i): {"group": str(i), "split": "train"} for i in range(8)}
        sampled = sample_rows(rows, assignments, {"coding": {"train": 6}}, "seed")
        self.assertEqual(len(sampled), 3)
        self.assertTrue(all(row["sampling"] == "spread" for row in sampled))

    def test_rank_statistics_handle_ties_and_absent_classes(self):
        def row(positive):
            return {"ok": True, "usage": 0.5, "doneP": {"finished": 0.8}, "shapeP": {"hands_on": 1},
                    "label": fixture(phase_gold="completed_checkpoint" if positive else "still_in_progress")}
        positive, negative = row(True), row(False)
        self.assertEqual(rank_metrics([positive, negative], SHIPPED)["ap"], 0.5)
        self.assertEqual(rank_metrics([negative, positive], SHIPPED)["ap"], 0.5)
        self.assertEqual(rank_metrics([positive, negative], SHIPPED)["auc"], 0.5)
        self.assertIsNone(rank_metrics([positive], SHIPPED)["auc"])
        self.assertIsNone(rank_metrics([negative], SHIPPED)["ap"])

    def test_offline_cli_smoke_freezes_selects_and_reports_without_network(self):
        Path(".test-tmp").mkdir(exist_ok=True)
        tools = Path(__file__).parent.resolve()
        with tempfile.TemporaryDirectory(dir=".test-tmp") as temporary:
            root = Path(temporary).resolve()
            checkpoints, labels, results = [], [], []
            for split in ("train", "validation", "holdout"):
                for positive in (True, False):
                    key = split + ("positive" if positive else "negative")
                    checkpoints.append({"id": key, "group": key, "split": split,
                                        "stratum": "fixture-worker", "contextUsage": 0.5, "state": {}})
                    labels.append(fixture(id=key, phase_gold="completed_checkpoint" if positive else "still_in_progress", task_boundary=positive))
                    results.append({"id": key, "ok": True, "model": "fixture-jev", "usage": 0.5,
                                    "done": "finished" if positive else "not_finished", "shape": "coordinating",
                                    "doneP": {"finished": 0.95 if positive else 0.1}, "shapeP": {"hands_on": 0}})
            for name, values in (("checkpoints", checkpoints), ("labels", labels),
                                 ("development", results[:4]), ("holdout", results[4:])):
                (root / f"{name}.jsonl").write_text("".join(json.dumps(value) + "\n" for value in values))
            def command(script, *args):
                return subprocess.run([sys.executable, str(tools / script), *map(str, args)], check=True, capture_output=True, text=True)
            cp = root / "checkpoints.jsonl"
            def rejected(script, *args):
                completed = subprocess.run([sys.executable, str(tools / script), *map(str, args)], capture_output=True, text=True)
                self.assertNotEqual(completed.returncode, 0)
                return completed.stderr
            self.assertIn("absent", rejected("optimise.py", "freeze", cp, root / "plan.json", "--protect", "missing-stratum"))
            self.assertIn("--protect", rejected("optimise.py", "freeze", cp, root / "plan.json"))
            self.assertFalse((root / "plan.json").exists())
            command("optimise.py", "freeze", cp, root / "plan.json", "--protect", "fixture-worker")
            self.assertEqual(json.loads((root / "plan.json").read_text())["protectedStrata"], ["fixture-worker"])
            for split in ("train", "validation"):
                self.assertIn("invalid choice", rejected("partition_labels.py", cp, root / "rejected.jsonl", split, root / "labels.jsonl"))
                self.assertIn("invalid choice", rejected("collate.py", root, root / "rejected.json", "--manifest", root / "plan.json", "--split", split))
            self.assertFalse((root / "rejected.jsonl").exists())
            command("partition_labels.py", cp, root / "development-labels.jsonl", "development", root / "labels.jsonl")
            plan = json.loads((root / "plan.json").read_text())
            (root / "unprotected-plan.json").write_text(json.dumps({**plan, "protectedStrata": []}))
            self.assertIn("at least one --protect", rejected("optimise.py", "select", cp, root / "development-labels.jsonl", root / "development.jsonl", root / "unprotected-plan.json", root / "selection.json"))
            self.assertFalse((root / "selection.json").exists())
            command("optimise.py", "select", cp, root / "development-labels.jsonl", root / "development.jsonl", root / "plan.json", root / "selection.json")
            command("partition_labels.py", cp, root / "holdout-labels.jsonl", "holdout", root / "labels.jsonl", "--selection", root / "selection.json")
            command("evaluate_profiles.py", cp, root / "holdout-labels.jsonl", root / "holdout.jsonl", root / "selection.json", root / "report.json", "--bootstrap", 20, "--legacy-output", root / "legacy")
            self.assertEqual(json.loads((root / "selection.json").read_text())["protectedStrata"], ["fixture-worker"])
            report = json.loads((root / "report.json").read_text())
            self.assertEqual(report["all"]["selected"]["union"]["recall"], 1)
            self.assertEqual(report["all"]["selected"]["union"]["precision"], 1)
            self.assertEqual(report["all"]["shipped"]["union"]["hints"], 0)
            legacy = root / "legacy"
            command("../metrics.py", legacy / "labels.jsonl", legacy / "checkpoints.jsonl", legacy / "selected.jsonl")
            command("schedule.py", legacy / "labels.jsonl", legacy / "selected.jsonl", "p['decision']['positive']", 0.4, 0.6)
            command("earn.py", legacy / "labels.jsonl", "decision", "p['positive']", f"shipped={legacy / 'shipped.jsonl'}", f"selected={legacy / 'selected.jsonl'}", "--n20")

    def test_session_bootstrap_is_paired_deterministic_and_aggregate_only(self):
        rows = [{"ok": True, "group": group, "stratum": "fixture-worker", "usage": 0.5,
                 "doneP": {"finished": 0.9 if positive else 0.1}, "shapeP": {"hands_on": 1},
                 "label": fixture(phase_gold="completed_checkpoint" if positive else "still_in_progress")}
                for group in ("private-group-one", "private-group-two") for positive in (True, False)]
        measured = clustered_difference(rows, SHIPPED, SHIPPED, repeats=50)
        self.assertEqual(measured, clustered_difference(rows, SHIPPED, SHIPPED, repeats=50))
        self.assertEqual(measured["intervals"]["recall"]["lower95"], 0)
        self.assertEqual(measured["intervals"]["precision"]["upper95"], 0)
        self.assertNotIn("private-group-one", json.dumps(measured))
        self.assertIsNone(clustered_difference(rows[:2], SHIPPED, SHIPPED)["intervals"])

    def test_numeric_selection_uses_only_training_validation_and_keeps_unknown_separate(self):
        def row(positive):
            return {"stratum": "fixture-worker", "ok": True, "usage": 0.5,
                    "doneP": {"finished": 0.6 if positive else 0.1}, "shapeP": {"hands_on": 0},
                    "label": fixture(phase_gold="completed_checkpoint" if positive else "still_in_progress")}
        rows = [row(True), row(True), row(False)]
        chosen = select(rows, rows, family(), ["fixture-worker"])
        selected = json.loads(chosen["profile"])
        self.assertTrue(chosen["selectedMeetsConstraints"])
        self.assertTrue(chosen["flatMeetsConstraints"])
        self.assertEqual(chosen["flatProfile"]["floors"], [[0, 0.3]])
        self.assertEqual(metrics(rows, selected)["precision"], 1)
        self.assertEqual(metrics(rows, selected)["recall"], 1)
        unknown = {**row(True), "label": fixture(context_need="unknown", safe_to_compact=None)}
        self.assertEqual(metrics([unknown], SHIPPED)["unknown"], 1)
        self.assertIsNone(metrics([unknown], SHIPPED)["precision"])
        checkpoints = [{"id": "holdout1", "group": "g", "stratum": "fixture-worker", "split": "holdout"}]
        with self.assertRaisesRegex(ValueError, "outside"):
            join_rows(checkpoints, [fixture(id="holdout1")], [], "development")

    def test_protected_stratum_constraint_uses_exact_names_not_substrings(self):
        def row(stratum, finished, positive):
            return {"stratum": stratum, "ok": True, "usage": 0.5, "doneP": {"finished": finished}, "shapeP": {"hands_on": 1},
                    "label": fixture(phase_gold="completed_checkpoint" if positive else "still_in_progress")}
        rows = [row("coding-agent", 0.95, True), row("coding-agent", 0.5, True), row("coding-agent", 0.55, False)]
        rows += [row("supervisor", 0.95, True) for _ in range(18)]
        flat = {"version": 1, "coordinationWeight": 0.5, "floors": [[0, 0.3]]}
        self.assertEqual(metrics(rows, flat)["precision"], 20 / 21)
        self.assertEqual(metrics(rows[:3], SHIPPED)["precision"], 1)
        self.assertTrue(eligible(rows, flat, [SHIPPED]))
        self.assertTrue(eligible(rows, flat, [SHIPPED], ["worker"]))
        self.assertFalse(eligible(rows, flat, [SHIPPED], ["coding-agent"]))
        chosen = select(rows, rows, family(), ["coding-agent"])
        self.assertEqual(chosen["protectedStrata"], ["coding-agent"])
        self.assertEqual(metrics(rows[:3], json.loads(chosen["profile"]))["fp"], 0)
        unprotected = select(rows, rows, family(), ["worker"])
        self.assertEqual(metrics(rows[:3], json.loads(unprotected["profile"]))["fp"], 1)
        with self.assertRaisesRegex(ValueError, "cannot enforce"):
            select(rows, rows, family(), [])

    def test_reduction_preserves_validation_holdout_and_pilot(self):
        manifest = {"plan": {"seed": "fixed", "targets": {"coding": {"train": 4, "validation": 1, "holdout": 1}}},
                    "rows": [{"id": str(i), "stratum": "coding", "split": "train" if i < 4 else "validation" if i == 4 else "holdout"} for i in range(6)]}
        reduced = reduce_training(manifest, {"coding": 2}, ["0"])
        self.assertIn(manifest["rows"][0], reduced["rows"])
        self.assertEqual([row for row in reduced["rows"] if row["split"] != "train"], manifest["rows"][4:])
        self.assertEqual(len(reduced["rows"]), 4)
        with self.assertRaises(ValueError):
            reduce_training(manifest, {"coding": 2}, ["5"])

    def test_quota_guard_rejects_stale_missing_and_insufficient_headroom(self):
        class Response:
            stdout = ""
        response = Response()
        value = {"providers": [{"provider": "claude", "state": {"status": "fresh", "stale": False},
                               "windows": [{"id": "seven_day", "percentRemaining": 30}]}]}
        reserves = {("claude", "seven_day"): 8}
        with patch("label.subprocess.run", return_value=response), patch("label.save"):
            response.stdout = json.dumps(value)
            self.assertEqual(quota_guard(Path("unused"), reserves), {("claude", "seven_day"): 30})
            value["providers"][0]["windows"][0]["percentRemaining"] = 27
            response.stdout = json.dumps(value)
            with self.assertRaises(ValueError):
                quota_guard(Path("unused"), reserves)
            value["providers"][0]["state"]["stale"] = True
            response.stdout = json.dumps(value)
            with self.assertRaises(ValueError):
                quota_guard(Path("unused"), reserves)
            response.stdout = json.dumps({"providers": []})
            with self.assertRaises(ValueError):
                quota_guard(Path("unused"), reserves)

    def test_single_allowance_bounds_spend_and_projection(self):
        results = [{"provider": "fable", "costUsd": 0.38}, {"provider": "astra", "costUsd": 0.06}]
        caps = {"fable": 130, "astra": 130}
        budget_guard(results, 100, caps, 22)
        with self.assertRaisesRegex(ValueError, "Projected cost"):
            budget_guard(results, 96.48, caps, 22)
        with self.assertRaisesRegex(ValueError, "Recorded spend"):
            budget_guard([{"provider": "fable", "costUsd": 80}], 96.48, caps, 22)
        calibration = {"fable": 11, "astra": 10}
        budget_guard(results, 7, calibration, 0)
        with self.assertRaises(ValueError):
            budget_guard(results, 1, calibration, 0)
        with self.assertRaisesRegex(ValueError, "call cap"):
            budget_guard(results * 12, 100, calibration, 0)

    def test_reuse_keeps_source_stratum_and_sampling(self):
        sessions = [{"session": str(i), "source": {"host": "claude", "stratum": "supervision", "file": f"s{i}.jsonl"},
                     "eventIds": [str(i)], "stateHashes": [str(i)]} for i in range(3)]
        checkpoints = [{"id": f"cp{i}", "sessionFile": f"s{i}.jsonl", "stratum": "interactive",
                        "sampling": "targeted-hard" if i == 1 else "spread"} for i in range(3)]
        checkpoints[2].pop("sampling")
        labels = [fixture(id=f"cp{i}") for i in range(3)]
        Path(".test-tmp").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=".test-tmp") as temporary:
            old = Path(temporary) / "old"
            old.mkdir()
            for name, rows in (("checkpoints", checkpoints), ("labels-fable", labels), ("labels-astra", labels)):
                (old / f"{name}.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
            (Path(temporary) / "sessions.json").write_text(json.dumps(sessions))
            with patch("sys.argv", ["reuse", str(old), str(Path(temporary) / "sessions.json"), str(Path(temporary) / "out"), "--seed", "s"]):
                reuse.main()
            rows = {row["id"]: row for row in reuse.jsonl(Path(temporary) / "out" / "checkpoints.jsonl")}
        self.assertEqual([rows[f"cp{i}"]["sampling"] for i in range(3)], ["spread", "targeted-hard", "spread"])
        self.assertTrue(all(row["stratum"] == "interactive" for row in rows.values()))
        self.assertEqual({row["split"] for row in rows.values()}, {"train", "validation", "holdout"})

    def test_rubric_table_enumerates_prompt_context_values(self):
        readme = (Path(__file__).parents[1] / "README.md").read_text()
        rubric = readme[readme.index("## Label schema"):readme.index("## Gitignore boundary")]
        rows = {line.split("|")[1].strip(" `"): line.split("|")[2] for line in rubric.splitlines() if line.startswith("| `")}
        self.assertEqual({value.strip(" `") for value in rows["context_need"].split("/")}, CONTEXT)
        self.assertIn("null", rows["safe_to_compact"])

    def test_adjudication_prompt_and_budget_stop(self):
        labels = [fixture(), fixture(context_need="tail")]
        rendered = prompt_for("fixture1", "FULL STATE", "RUBRIC", labels)
        self.assertTrue(rendered.endswith("FULL STATE"))
        self.assertIn(json.dumps(labels), rendered)
        caps = {"fable": 180, "astra": 180}
        budget_guard([], 96.48, caps, 22)
        with self.assertRaises(ValueError):
            budget_guard([{"provider": "fable", "costUsd": 1}], 96.48, caps, 22)


if __name__ == "__main__":
    unittest.main()
