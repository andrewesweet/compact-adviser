import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from collate import collate
from label import budget_guard, calibration_guard, parse_response, prompt_for, quota_guard, run_one, validate_label
from reduce import reduce_training
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

    def test_separate_projection_limit_never_raises_spend_ceiling(self):
        results = [{"provider": "fable", "costUsd": 0.38}, {"provider": "astra", "costUsd": 0.06}]
        budget_guard(results, 96.48, 130, 22, 100)
        with self.assertRaises(ValueError):
            budget_guard(results, 96.48, 130, 22)
        with self.assertRaises(ValueError):
            budget_guard([{"provider": "fable", "costUsd": 80}], 96.48, 130, 22, 1000)
        calibration_guard(results, 7, {"fable": 11, "astra": 10})
        with self.assertRaises(ValueError):
            calibration_guard(results, 1, {"fable": 11, "astra": 10})

    def test_adjudication_prompt_and_budget_stop(self):
        labels = [fixture(), fixture(context_need="tail")]
        rendered = prompt_for("fixture1", "FULL STATE", "RUBRIC", labels)
        self.assertTrue(rendered.endswith("FULL STATE"))
        self.assertIn(json.dumps(labels), rendered)
        budget_guard([], 96.48, 180, 22)
        with self.assertRaises(ValueError):
            budget_guard([{"provider": "fable", "costUsd": 1}], 96.48, 180, 22)


if __name__ == "__main__":
    unittest.main()
