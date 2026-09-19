import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { floorFor, parseJudgment, qualifies, score } from "../src/judge.ts";
import { parseProfile } from "../src/profile.ts";
import { apiResponse } from "./helpers.ts";

test("offline numeric decisions match the production profile gate", () => {
  const vectors = [];
  const expected = [];
  for (const weight of [0, 0.25, 0.5, 0.75, 1]) {
    for (const floors of [
      [[0, 0.6]],
      [
        [0.1, 0.9],
        [0.9, 0.5],
      ],
      [
        [0.1, 0.85],
        [0.9, 0.4],
      ],
    ]) {
      const profile = parseProfile(
        JSON.stringify({ version: 1, coordinationWeight: weight, floors }),
      );
      for (const usage of [Number.NaN, -1, 0, 0.1, 0.12345, 0.3, 0.5, 0.9, 1]) {
        for (const [done, shape] of [
          [1, 0],
          [0.8, 0.4],
          [0.1, 1],
        ]) {
          const judgment = parseJudgment(apiResponse(done, shape));
          vectors.push({
            profile,
            result: {
              ok: true,
              usage,
              doneP: judgment.done.probabilities,
              shapeP: judgment.shape.probabilities,
            },
          });
          expected.push({
            score: score(judgment, profile),
            floor: floorFor(usage, profile),
            hint: qualifies(judgment, usage, profile),
          });
        }
      }
    }
  }
  const tools = fileURLToPath(new URL("../eval/tools/", import.meta.url));
  const actual = JSON.parse(
    execFileSync(
      "python3",
      [
        "-c",
        "import sys,json;sys.path.insert(0,sys.argv[1]);from optimise import decision;print(json.dumps([decision(v['result'],v['profile']) for v in json.load(sys.stdin)]))",
        tools,
      ],
      { input: JSON.stringify(vectors), encoding: "utf8" },
    ),
  );
  assert.deepEqual(actual, expected);
});
