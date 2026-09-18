// The product, driven through the real CLI the hooks and the status line run.
//
// Every case here starts from a Grok-shaped session directory and a loopback TypeSafe fixture
// and asserts what the person would see: a hint on the status row, or nothing at all.

import assert from "node:assert/strict";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";
import { HINT } from "../lib/statusline.ts";
import {
  lab,
  runCli,
  statusPayload,
  stopPayload,
  typesafeFixture,
  workedHistory,
  writeHistory,
  writeSignals,
} from "./support.ts";

function keyed(fixture: { url: string }) {
  return { COMPACT_ADVISER_TEST_ENDPOINT: fixture.url, TYPESAFE_API_KEY: "tsk-test-key" };
}

async function judgeTurn(
  t: Parameters<typeof lab>[0],
  options: { tokens?: number; marker?: string } = {},
) {
  const l = lab(t);
  const fixture = await typesafeFixture(t);
  writeHistory(l, workedHistory(options.marker ?? "one"));
  writeSignals(l, options.tokens ?? 150000);
  return { l, fixture };
}

test("a qualifying checkpoint puts the hint on the status row, and the Stop gate stays silent", async (t) => {
  const { l, fixture } = await judgeTurn(t);
  const stop = await runCli(["hook", "stop"], {
    lab: l,
    stdin: stopPayload(l),
    env: keyed(fixture),
  });
  assert.equal(stop.code, 0);
  // The Stop hook's stdout is the turn's control channel; anything on it could keep the agent
  // working, which would put the advice in the model's context instead of the person's.
  assert.equal(stop.stdout, "");
  assert.equal(fixture.bodies.length, 1);

  const row = await runCli(["status-line"], { lab: l, stdin: statusPayload(l) });
  assert.match(row.stdout, /project │ Grok 4\.6 │ 24% ctx/);
  assert.ok(row.stdout.includes(HINT));
});

test("the judge sees the person's own words, not Grok's prompt envelopes", async (t) => {
  const { l, fixture } = await judgeTurn(t);
  await runCli(["hook", "stop"], { lab: l, stdin: stopPayload(l), env: keyed(fixture) });
  const body = JSON.parse(fixture.bodies[0] ?? "{}") as {
    state: { userConstraints: { text: string }[]; savedArtifacts: string[] };
  };
  assert.deepEqual(
    body.state.userConstraints.map((entry) => entry.text),
    ["Fix the parser bug one, run the tests, and commit."],
  );
  assert.deepEqual(body.state.savedArtifacts, ["/repo/src/parser-one.ts"]);
});

test("a judgment below the floor leaves the row without a hint", async (t) => {
  const { l, fixture } = await judgeTurn(t);
  fixture.verdict = { finished: 0.2, handsOn: 0.2 };
  await runCli(["hook", "stop"], { lab: l, stdin: stopPayload(l), env: keyed(fixture) });
  const row = await runCli(["status-line"], { lab: l, stdin: statusPayload(l) });
  assert.ok(!row.stdout.includes(HINT));
  assert.match(row.stdout, /project │ Grok 4\.6/);
});

test("no TypeSafe key means no request at all", async (t) => {
  const { l, fixture } = await judgeTurn(t);
  const stop = await runCli(["hook", "stop"], {
    lab: l,
    stdin: stopPayload(l),
    env: { COMPACT_ADVISER_TEST_ENDPOINT: fixture.url },
  });
  assert.equal(stop.code, 0);
  assert.equal(fixture.bodies.length, 0);
});

test("a cwd .env supplies the key when the environment does not", async (t) => {
  const { l, fixture } = await judgeTurn(t);
  writeFileSync(join(l.cwd, ".env"), "TYPESAFE_API_KEY=tsk-from-dotenv\n");
  await runCli(["hook", "stop"], {
    lab: l,
    stdin: stopPayload(l),
    env: { COMPACT_ADVISER_TEST_ENDPOINT: fixture.url },
  });
  assert.equal(fixture.bodies.length, 1);
});

test("mode off asks nothing and clears a hint already earned", async (t) => {
  const { l, fixture } = await judgeTurn(t);
  await runCli(["hook", "stop"], { lab: l, stdin: stopPayload(l), env: keyed(fixture) });
  assert.ok(
    (await runCli(["status-line"], { lab: l, stdin: statusPayload(l) })).stdout.includes(HINT),
  );

  await runCli(["mode", "off"], { lab: l });
  await runCli(["hook", "stop"], {
    lab: l,
    stdin: stopPayload(l, { promptId: "prompt-2" }),
    env: keyed(fixture),
  });
  assert.equal(fixture.bodies.length, 1);
  const row = await runCli(["status-line"], { lab: l, stdin: statusPayload(l) });
  assert.ok(!row.stdout.includes(HINT));
});

test("a subagent's stop and the session-end fire are not checkpoints", async (t) => {
  const { l, fixture } = await judgeTurn(t);
  for (const patch of [
    { subagentType: "explore" },
    { reason: "channel_closed" },
    { stopHookActive: true },
  ]) {
    const stop = await runCli(["hook", "stop"], {
      lab: l,
      stdin: stopPayload(l, patch),
      env: keyed(fixture),
    });
    assert.equal(stop.code, 0);
  }
  assert.equal(fixture.bodies.length, 0);
});

test("a context below the minimum is never judged", async (t) => {
  const { l, fixture } = await judgeTurn(t, { tokens: 30000 });
  await runCli(["hook", "stop"], { lab: l, stdin: stopPayload(l), env: keyed(fixture) });
  assert.equal(fixture.bodies.length, 0);

  await runCli(["threshold", "25000"], { lab: l });
  await runCli(["hook", "stop"], {
    lab: l,
    stdin: stopPayload(l, { promptId: "prompt-2" }),
    env: keyed(fixture),
  });
  assert.equal(fixture.bodies.length, 1);
});

test("an unchanged checkpoint is judged once; new work is judged again", async (t) => {
  const { l, fixture } = await judgeTurn(t);
  await runCli(["hook", "stop"], { lab: l, stdin: stopPayload(l), env: keyed(fixture) });
  // A later turn that added nothing the judge can see: same answer, same last ask.
  await runCli(["hook", "stop"], {
    lab: l,
    stdin: stopPayload(l, { promptId: "prompt-2" }),
    env: keyed(fixture),
  });
  assert.equal(fixture.bodies.length, 1);

  writeHistory(l, [...workedHistory("one"), ...workedHistory("two").slice(1)]);
  await runCli(["hook", "stop"], {
    lab: l,
    stdin: stopPayload(l, { promptId: "prompt-3" }),
    env: keyed(fixture),
  });
  assert.equal(fixture.bodies.length, 2);
});

test("a compaction retires the hint and holds the next judgments", async (t) => {
  const { l, fixture } = await judgeTurn(t);
  await runCli(["hook", "stop"], { lab: l, stdin: stopPayload(l), env: keyed(fixture) });
  assert.ok(
    (await runCli(["status-line"], { lab: l, stdin: statusPayload(l) })).stdout.includes(HINT),
  );

  await runCli(["hook", "compact"], {
    lab: l,
    stdin: JSON.stringify({
      hook_event_name: "PostCompact",
      sessionId: l.sessionId,
      trigger: "auto",
    }),
  });
  const row = await runCli(["status-line"], { lab: l, stdin: statusPayload(l) });
  assert.ok(!row.stdout.includes(HINT));

  writeHistory(l, [...workedHistory("one"), ...workedHistory("two").slice(1)]);
  await runCli(["hook", "stop"], {
    lab: l,
    stdin: stopPayload(l, { promptId: "prompt-2" }),
    env: keyed(fixture),
  });
  assert.equal(fixture.bodies.length, 1, "post-compaction cooldown holds the next checkpoint");
  assert.match((await runCli(["status"], { lab: l })).stdout, /Waiting for 20k new tokens/);
});

test("the next prompt retires the hint", async (t) => {
  const { l, fixture } = await judgeTurn(t);
  await runCli(["hook", "stop"], { lab: l, stdin: stopPayload(l), env: keyed(fixture) });
  await runCli(["hook", "prompt"], {
    lab: l,
    stdin: JSON.stringify({ hook_event_name: "UserPromptSubmit", sessionId: l.sessionId }),
  });
  const row = await runCli(["status-line"], { lab: l, stdin: statusPayload(l) });
  assert.ok(!row.stdout.includes(HINT));
});

test("a turn that started after the hint hides it even before the prompt hook lands", async (t) => {
  const { l, fixture } = await judgeTurn(t);
  await runCli(["hook", "stop"], { lab: l, stdin: stopPayload(l), env: keyed(fixture) });
  const row = await runCli(["status-line"], {
    lab: l,
    stdin: statusPayload(l, { prompt_id: "prompt-2" }),
  });
  assert.ok(!row.stdout.includes(HINT));
});

test("dismiss and snooze take the hint away and hold the next checkpoints", async (t) => {
  const { l, fixture } = await judgeTurn(t);
  await runCli(["hook", "stop"], { lab: l, stdin: stopPayload(l), env: keyed(fixture) });
  assert.match((await runCli(["dismiss"], { lab: l })).stdout, /dismissed/i);
  assert.ok(
    !(await runCli(["status-line"], { lab: l, stdin: statusPayload(l) })).stdout.includes(HINT),
  );

  assert.match((await runCli(["snooze"], { lab: l })).stdout, /snoozed/i);
  writeHistory(l, [...workedHistory("one"), ...workedHistory("two").slice(1)]);
  await runCli(["hook", "stop"], {
    lab: l,
    stdin: stopPayload(l, { promptId: "prompt-2" }),
    env: keyed(fixture),
  });
  assert.equal(fixture.bodies.length, 1);
  assert.match((await runCli(["status"], { lab: l })).stdout, /Snoozed/);
});

test("a refused judgment backs off, reports its kind, and never hints", async (t) => {
  const { l, fixture } = await judgeTurn(t);
  fixture.status = 401;
  await runCli(["hook", "stop"], { lab: l, stdin: stopPayload(l), env: keyed(fixture) });
  const row = await runCli(["status-line"], { lab: l, stdin: statusPayload(l) });
  assert.ok(!row.stdout.includes(HINT));
  const status = await runCli(["status"], { lab: l });
  assert.match(status.stdout, /Last TypeSafe outcome: authentication/);
  assert.match(status.stdout, /TypeSafe backoff/);
});

test("request logging writes the request and the outcome, and never the key", async (t) => {
  const { l, fixture } = await judgeTurn(t);
  await runCli(["log", "on"], { lab: l });
  await runCli(["hook", "stop"], { lab: l, stdin: stopPayload(l), env: keyed(fixture) });
  const path = join(l.dataDir, `compact-adviser-requests-${l.sessionId}.jsonl`);
  const lines = readFileSync(path, "utf8")
    .trim()
    .split("\n")
    .map((line) => JSON.parse(line));
  assert.deepEqual(
    lines.map((line: { kind: string }) => line.kind),
    ["request", "response"],
  );
  assert.equal(lines[0].id, lines[1].id);
  assert.equal(lines[1].qualifies, true);
  assert.ok(!readFileSync(path, "utf8").includes("tsk-test-key"));
});

test("session start writes the launchers the status line and the slash command point at", async (t) => {
  const l = lab(t);
  const entry = join(import.meta.dirname, "..", "bin", "adviser.ts");
  await runCli(["hook", "session-start"], {
    lab: l,
    stdin: JSON.stringify({ hook_event_name: "SessionStart", sessionId: l.sessionId }),
  });
  for (const name of ["adviser.sh", "status-line.sh"]) {
    const path = join(l.dataDir, name);
    assert.ok(existsSync(path), `${name} written`);
    const body = readFileSync(path, "utf8");
    assert.match(body, /^#!\/bin\/sh/);
    assert.ok(body.includes(entry), `${name} points at this package`);
  }
  assert.match((await runCli(["doctor"], { lab: l })).stdout, /ok {3}status-line launcher/);
});

test("install registers the same handlers the plugin ships, with a path Grok can run", async (t) => {
  const l = lab(t);
  const entry = join(import.meta.dirname, "..", "bin", "adviser.ts");
  const before = await runCli(["doctor"], { lab: l });
  assert.match(before.stdout, /FAIL hooks registered/);

  const installed = await runCli(["install"], { lab: l });
  assert.match(installed.stdout, /Registered the compact-adviser hooks/);
  // The person still has to opt the status row in themselves; say so at install time.
  assert.match(installed.stdout, /\[ui\.status_line\]/);

  const written = JSON.parse(readFileSync(join(l.home, "hooks", "compact-adviser.json"), "utf8"));
  const shipped = JSON.parse(
    readFileSync(join(import.meta.dirname, "..", "hooks", "hooks.json"), "utf8"),
  );
  assert.deepEqual(Object.keys(written.hooks), Object.keys(shipped.hooks));
  for (const [event, groups] of Object.entries(written.hooks) as [string, any][]) {
    const command = groups[0].hooks[0].command;
    assert.ok(command.includes(entry), `${event} runs this package`);
    assert.ok(!command.includes("GROK_PLUGIN_ROOT"), `${event} needs no plugin environment`);
  }
  assert.match((await runCli(["doctor"], { lab: l })).stdout, /ok {3}hooks registered/);
});

test("one turn is judged once even when the Stop gate is registered twice", async (t) => {
  const { l, fixture } = await judgeTurn(t);
  // Two identical runs of the same turn: what a plugin copy and an installed copy would do.
  const [first, second] = await Promise.all([
    runCli(["hook", "stop"], { lab: l, stdin: stopPayload(l), env: keyed(fixture) }),
    runCli(["hook", "stop"], { lab: l, stdin: stopPayload(l), env: keyed(fixture) }),
  ]);
  assert.equal(first.code, 0);
  assert.equal(second.code, 0);
  // Back-to-back, the second run sees the turn already handled.
  await runCli(["hook", "stop"], { lab: l, stdin: stopPayload(l), env: keyed(fixture) });
  assert.ok(fixture.bodies.length <= 2, `asked ${fixture.bodies.length} times`);
  assert.match((await runCli(["status"], { lab: l })).stdout, /1 completed exchange/);
});

test("an unreadable settings file stops the product instead of guessing", async (t) => {
  const { l, fixture } = await judgeTurn(t);
  await runCli(["mode", "hint"], { lab: l });
  writeFileSync(join(l.dataDir, "settings.json"), '{"version":1,"mode":"sometimes"}');
  const stop = await runCli(["hook", "stop"], {
    lab: l,
    stdin: stopPayload(l),
    env: keyed(fixture),
  });
  assert.equal(stop.code, 0);
  assert.equal(fixture.bodies.length, 0);
  const status = await runCli(["status"], { lab: l });
  assert.equal(status.code, 1);
  assert.match(status.stderr, /Cannot read the compact-adviser mode setting/);
});
