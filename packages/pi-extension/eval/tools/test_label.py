import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from collate import collate
from label import CONTEXT, budget_guard, parse_response, prompt_for, quota_guard, run_one, validate_label
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
