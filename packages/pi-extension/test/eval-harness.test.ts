import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";
import { mainBranch } from "../eval/branch.ts";
import { EXAMPLE_CORPUS_PATH, loadCorpus } from "../eval/corpus.ts";
import { buildSession, claudeFuture, privateOutput, worksheet } from "../eval/dataset.ts";
import { futureWindow, renderEntry } from "../eval/render.ts";
import { loadSession, passesSizeGates, replayAt, settledEntries } from "../eval/replay.ts";
import { spread } from "../eval/sample.ts";
import { harness, temp } from "./helpers.ts";

test("corpus loader accepts the example contract and rejects a missing file", () => {
  const rows = loadCorpus(EXAMPLE_CORPUS_PATH);
  assert.equal(rows.length, 3);
  assert.equal(rows[0].label, "interactive-1");
  assert.equal(rows[1].stratum, "coding");
  assert.match(rows[2].file, /<session>\.jsonl$/);
  assert.throws(() => loadCorpus(join("/tmp", "compact-adviser-missing-corpus.json")));
});

test("spread samples across the arc", () => {
  const items = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9];
  const got = spread(items, 3);
  assert.equal(got.length, 3);
  assert.ok(got[0] < got[1] && got[1] < got[2]);
  assert.deepEqual(spread(["a", "b"], 5), ["a", "b"]);
});

test("replay drives production snapshot from a synthetic Pi session", (t) => {
  const h = harness(t);
  const file = h.sm.getSessionFile();
  assert.ok(file);
  const s = loadSession(file);
  assert.ok(s.cwd);
  const settled = settledEntries(s);
  assert.ok(settled.length >= 2);
  const last = settled.at(-1);
  assert.ok(last);
  const cp = replayAt(s, last, settled.length - 1);
  assert.ok(cp);
  assert.equal(cp.sessionFile, file);
  assert.equal(cp.entryId, last.id);
  assert.ok(cp.contextTokens >= 40000);
  assert.ok(cp.conversationTokens > 20000);
  assert.equal(passesSizeGates(cp), true);
  const state = cp.state as {
    userConstraints: unknown[];
    recent: unknown[];
    coverage: { transcriptRecoverable: boolean };
  };
  assert.ok(Array.isArray(state.userConstraints));
  assert.ok(state.recent.length > 0);
  assert.equal(state.coverage.transcriptRecoverable, true);
  const branch = mainBranch(s);
  assert.ok(branch.some((e) => e.id === last.id));
  const future = futureWindow(branch, settled[0].id, 4);
  assert.ok(future.length > 0);
  assert.match(renderEntry(settled[0] as Record<string, unknown>, 40), /\[assistant/);
});

test("dataset builds full redacted worksheets from a real replay interface", (t) => {
  const h = harness(t);
  const file = h.sm.getSessionFile();
  assert.ok(file);
  const dataset = buildSession({ host: "pi", stratum: "fixture", file });
  assert.ok(dataset.checkpoints.length > 0);
  assert.ok(dataset.eventIds.length > 0);
  const checkpoint = dataset.checkpoints[0];
  assert.equal(
    dataset.checkpoints.length,
    new Set(dataset.checkpoints.map((row) => row.checkpointKey)).size,
  );
  const sheet = worksheet({
    ...checkpoint,
    future: ["TOKEN=abcdefghijklmnop"],
    futureTruncated: true,
  });
  assert.ok(sheet.includes(JSON.stringify(checkpoint.state, null, 2)));
  assert.ok(!sheet.includes("abcdefghijklmnop"));
  assert.match(sheet, /Future evidence truncated: true/);
  assert.throws(() => privateOutput(join(process.cwd(), "test", "not-ignored.txt")));
});

test("Claude hindsight follows descendants, not the later sibling branch", () => {
  const root = { uuid: "root", parentUuid: null, type: "user" };
  const checkpoint = { uuid: "left", parentUuid: "root", type: "assistant" };
  const child = { uuid: "child", parentUuid: "left", type: "user" };
  const sibling = { uuid: "right", parentUuid: "root", type: "assistant" };
  assert.deepEqual(claudeFuture([root, checkpoint, child, sibling], checkpoint), [child]);
});

test("labelling tools validate provider usage and joint truth without network", () => {
  const result = spawnSync("python3", ["eval/tools/test_label.py"], { encoding: "utf8" });
  assert.equal(result.status, 0, result.stderr);
});

test("comparison command re-gates stored answers with explicit threshold evidence", (t) => {
  const directory = temp(t);
  const checkpoints = join(directory, "checkpoints.jsonl");
  const results = join(directory, "results.jsonl");
  const settings = join(directory, "settings.json");
  const output = join(directory, "comparison.jsonl");
  writeFileSync(
    checkpoints,
    `${JSON.stringify({ id: "fixture", session: "s", contextTokens: 50000 })}\n`,
  );
  writeFileSync(
    results,
    `${JSON.stringify({ id: "fixture", ok: true, model: "fixture", inputTokens: 10, outputTokens: 2, done: "finished", doneP: { finished: 0.8, not_finished: 0.2, unclear: 0 }, doneConf: 0.6, shape: "hands_on", shapeP: { hands_on: 1, coordinating: 0, unclear: 0 }, shapeConf: 1 })}\n`,
  );
  writeFileSync(
    settings,
    JSON.stringify({ s: { window: 1000000, autoCompactThreshold: 60000, source: "fixture" } }),
  );
  const result = spawnSync(
    process.execPath,
    ["--import", "tsx", "eval/tools/compare.ts", checkpoints, results, settings, output],
    { encoding: "utf8" },
  );
  assert.equal(result.status, 0, result.stderr);
  const row = JSON.parse(readFileSync(output, "utf8"));
  assert.equal(row.unpatchedHint, false);
  assert.equal(row.patchedHint, true);
  assert.equal(row.source, "fixture");
});

test("metrics.py scores synthetic labels without a live session", (t) => {
  const dir = join(temp(t), "metrics");
  mkdirSync(dir, { recursive: true });
  writeFileSync(
    join(dir, "labels.jsonl"),
    `${JSON.stringify({
      id: "cp001",
      phase_gold: "completed_checkpoint",
      continuation_gold: "recoverable",
      safe_to_compact: true,
      pivot: false,
      task_boundary: true,
    })}\n${JSON.stringify({
      id: "cp002",
      phase_gold: "still_in_progress",
      continuation_gold: "needs_older_details",
      safe_to_compact: false,
      pivot: false,
      task_boundary: false,
    })}\n`,
  );
  writeFileSync(
    join(dir, "checkpoints.jsonl"),
    `${JSON.stringify({ id: "cp001", sampling: "spread", stratum: "coding" })}\n${JSON.stringify({
      id: "cp002",
      sampling: "spread",
      stratum: "coding",
    })}\n`,
  );
  writeFileSync(
    join(dir, "results.jsonl"),
    `${JSON.stringify({
      id: "cp001",
      ok: true,
      model: "jev-test",
      phase: "completed_checkpoint",
      phaseP: { completed_checkpoint: 0.95, still_in_progress: 0.03, unclear: 0.02 },
      hint: true,
      auto: false,
    })}\n${JSON.stringify({
      id: "cp002",
      ok: true,
      model: "jev-test",
      phase: "still_in_progress",
      phaseP: { completed_checkpoint: 0.1, still_in_progress: 0.8, unclear: 0.1 },
      hint: false,
      auto: false,
    })}\n`,
  );
  const result = spawnSync(
    "python3",
    [
      join(process.cwd(), "eval/metrics.py"),
      join(dir, "labels.jsonl"),
      join(dir, "checkpoints.jsonl"),
      join(dir, "results.jsonl"),
    ],
    { encoding: "utf8" },
  );
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /scored 2 checkpoints/);
  assert.match(result.stdout, /overall agreement: 2\/2/);
  assert.match(result.stdout, /TP=1 FP=0 FN=0 TN=1/);
  // Both gold definitions are reported side by side, and task-boundary recall
  // is its own section: a judge can look healthy overall and still miss the
  // moments the product exists to catch.
  assert.match(result.stdout, /=== product truth: gold is safe_to_compact ===/);
  assert.match(result.stdout, /=== contract: should-hint = completed \+ safe/);
  assert.match(result.stdout, /^ALL\s+2\s+1\s+0\s+100%\s+100%\s+0$/m);
  assert.match(result.stdout, /^ALL\s+2\s+1\s+0\s+1\s+100%\s+100%\s+1\s+1$/m);
  assert.match(result.stdout, /=== task-boundary recall/);
  assert.match(result.stdout, /^product truth\s+1\/1 = 100%\s+0\/0 = -$/m);
  assert.match(result.stdout, /^contract\s+1\/1 = 100%\s+0\/0 = -$/m);
});
