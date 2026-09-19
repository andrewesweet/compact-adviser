import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { readConfig } from "../../claude-mod/lib/config.ts";
import * as claudeJudge from "../../claude-mod/lib/judge.ts";
import * as claude from "../../claude-mod/lib/profile.ts";
import { ConfigStore as CodexStore } from "../../codex-plugin/src/config.ts";
import * as codexJudge from "../../codex-plugin/src/judge.ts";
import * as codex from "../../codex-plugin/src/profile.ts";
import { parseSettings } from "../../grok-plugin/lib/config.ts";
import * as grokJudge from "../../grok-plugin/lib/judge.ts";
import * as grok from "../../grok-plugin/lib/profile.ts";
import { ConfigStore } from "../src/config.ts";
import * as judge from "../src/judge.ts";
import * as pi from "../src/profile.ts";
import { apiResponse } from "./helpers.ts";

const defaults = {
  version: 1,
  coordinationWeight: 0.5,
  floors: [
    [0.1, 0.9],
    [0.9, 0.5],
  ],
};
const tailored = { version: 1, coordinationWeight: 0, floors: [[0, 0.6]] };
const parsers = [pi, claude, codex, grok];
const judges = [judge, claudeJudge, codexJudge, grokJudge];

test("profiles preserve default request bytes and decisions across every host", () => {
  const profile = pi.parseProfile(JSON.stringify(defaults));
  for (const module of judges) {
    assert.equal(
      module.requestBody({}),
      JSON.stringify({ model: "jev-latest", state: {}, questions: judge.QUESTIONS }),
    );
    assert.equal(module.requestBody({}, profile), module.requestBody({}));
    for (const usage of [Number.NaN, Number.POSITIVE_INFINITY, -1, 0, 0.1, 0.1001, 0.3, 0.9, 1]) {
      assert.equal(module.floorFor(usage, profile), judge.floorFor(usage));
      for (const finished of [0, 0.3, 0.7, 1]) {
        for (const handsOn of [0, 0.5, 1]) {
          const result = judge.parseJudgment(apiResponse(finished, handsOn));
          const expected = finished * (0.5 + 0.5 * handsOn);
          assert.equal(module.score(result, profile), expected);
          assert.equal(module.qualifies(result, usage, profile), expected >= judge.floorFor(usage));
        }
      }
    }
  }
});

test("all hosts accept the same bounded profiles and reject malformed overrides", () => {
  const invalid = [
    null,
    false,
    {},
    " ",
    "null",
    "{}",
    JSON.stringify({ ...defaults, extra: true }),
    JSON.stringify({ ...defaults, version: 2 }),
    JSON.stringify({ ...defaults, coordinationWeight: -1 }),
    JSON.stringify({ ...defaults, floors: [] }),
    JSON.stringify({
      ...defaults,
      floors: [
        [0.5, 0.5],
        [0.4, 0.4],
      ],
    }),
    JSON.stringify({
      ...defaults,
      floors: [
        [0, 0.4],
        [1, 0.5],
      ],
    }),
    JSON.stringify({ ...defaults, floors: [[0, 0.5, 1]] }),
    JSON.stringify({ ...defaults, questions: {} }),
    "x".repeat(4097),
  ];
  for (const module of parsers) {
    assert.equal(module.parseProfile(undefined), undefined);
    assert.equal(module.parseProfile(""), undefined);
    assert.deepEqual(module.parseProfile(JSON.stringify(defaults)), defaults);
    for (const setting of invalid)
      assert.throws(() => module.parseProfile(setting), /Invalid compact-adviser profile/);
  }
  const questions = structuredClone(judge.QUESTIONS) as pi.ProfileQuestions;
  questions.done.instructions = "Determine whether the latest work is complete.";
  const profile = pi.parseProfile(JSON.stringify({ ...tailored, questions }));
  for (const module of judges) {
    assert.deepEqual(JSON.parse(module.requestBody({}, profile)).questions, questions);
    assert.equal(module.floorFor(Number.NaN, profile), 0.6);
    assert.equal(module.score(judge.parseJudgment(apiResponse(1, 0)), profile), 1);
  }
});

test("host settings retain a profile and fail closed on invalid configuration", (t) => {
  const root = mkdtempSync(join(tmpdir(), "compact-profile-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const profile = JSON.stringify(tailored);
  for (const store of [new ConfigStore(join(root, "pi")), new CodexStore(join(root, "codex"))]) {
    store.update({ profile });
    store.update({ logRequests: true });
    assert.equal(store.read().profile, profile);
    assert.throws(() => store.update({ profile: "invalid" }));
    assert.equal(store.read().profile, profile);
    store.update({ profile: "" });
    assert.equal(pi.parseProfile(store.read().profile), undefined);
  }
  assert.equal(
    readConfig([], undefined, { mode: "hint", minContextTokens: 40000, profile }).profile,
    profile,
  );
  assert.throws(() =>
    readConfig([], undefined, { mode: "hint", minContextTokens: 40000, profile: "invalid" }),
  );
  assert.equal(parseSettings({ version: 1, profile }).profile, profile);
  assert.throws(() => parseSettings({ version: 1, profile: "invalid" }));
});

test("all real judge transports send selected questions", async () => {
  const questions = structuredClone(judge.QUESTIONS) as pi.ProfileQuestions;
  questions.shape.instructions = "Determine whether the work was hands-on.";
  const profile = pi.parseProfile(JSON.stringify({ ...tailored, questions }));
  const response = apiResponse(1, 0);
  const bodies: string[] = [];
  await judge.judge(
    {},
    "test-key",
    new AbortController().signal,
    async (_url, init) => {
      bodies.push(String(init?.body));
      return new Response(JSON.stringify(response));
    },
    100,
    profile,
  );
  for (const module of [claudeJudge, codexJudge, grokJudge]) {
    await module.judge(
      {},
      "test-key",
      {
        fetch: async (_url, init) => {
          bodies.push(init.body);
          return { status: 200, ok: true, text: JSON.stringify(response) };
        },
        sleep: () => new Promise(() => {}),
      },
      profile,
    );
  }
  assert.equal(bodies.length, 4);
  for (const body of bodies) assert.deepEqual(JSON.parse(body).questions, questions);
});
