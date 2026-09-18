// `grok plugin validate` on this package, plus the checks that command does not make.
//
// Grok's validator reads the manifest and the component directories; it does not read
// hooks.json, so the events this plugin registers, the entry point they run, and the rule
// that no hook writes to the model's context are asserted here instead.
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const PACKAGE = join(import.meta.dirname, "..");
const GROK = process.env.COMPACT_TEST_GROK_BIN ?? "grok";

function grok(args) {
  try {
    return { status: 0, output: execFileSync(GROK, args, { encoding: "utf8" }) };
  } catch (error) {
    return {
      status: error.status ?? 1,
      output: `${error.stdout ?? ""}${error.stderr ?? ""}${error.message ?? ""}`,
    };
  }
}

const problems = [];

const version = grok(["--version"]);
if (version.status !== 0) problems.push(`cannot run ${GROK}: ${version.output.trim()}`);

const validated = grok(["plugin", "validate", PACKAGE]);
if (validated.status !== 0 || !validated.output.includes("Plugin manifest is valid.")) {
  problems.push(`grok plugin validate failed:\n${validated.output.trim()}`);
}
if (!validated.output.includes("hooks")) problems.push("grok did not see this plugin's hooks");
if (!validated.output.includes("1 command dir(s)")) {
  problems.push("grok did not see the /compact-adviser slash command");
}

const hooks = JSON.parse(readFileSync(join(PACKAGE, "hooks", "hooks.json"), "utf8")).hooks;
const expected = {
  Stop: "hook stop",
  UserPromptSubmit: "hook prompt",
  PreCompact: "hook compact",
  PostCompact: "hook compact",
  SessionStart: "hook session-start",
  SessionEnd: "hook session-end",
};
for (const [event, tail] of Object.entries(expected)) {
  const handlers = (hooks[event] ?? []).flatMap((group) => group.hooks ?? []);
  if (handlers.length !== 1) {
    problems.push(`${event} should register exactly one handler, found ${handlers.length}`);
    continue;
  }
  const [handler] = handlers;
  if (handler.type !== "command")
    problems.push(`${event} must be a command hook, not ${handler.type}`);
  if (!handler.command?.endsWith(tail)) problems.push(`${event} should run \`${tail}\``);
  // biome-ignore lint/suspicious/noTemplateCurlyInString: this is Grok's own expansion syntax
  if (!handler.command?.includes('"${GROK_PLUGIN_ROOT}/bin/adviser.ts"')) {
    problems.push(`${event} must run this plugin's own entry point through GROK_PLUGIN_ROOT`);
  }
  if (typeof handler.timeout !== "number") problems.push(`${event} must set an explicit timeout`);
}
// A Stop gate is on the turn's critical path; Grok defaults it to 600 seconds, which is far
// longer than a 2-second judgment should ever hold a turn.
const stopTimeout = hooks.Stop?.[0]?.hooks?.[0]?.timeout;
if (!(stopTimeout > 0 && stopTimeout <= 60)) {
  problems.push(
    `the Stop gate's timeout should be a small number of seconds, found ${stopTimeout}`,
  );
}
for (const event of Object.keys(hooks)) {
  if (!(event in expected)) problems.push(`unexpected hook event registered: ${event}`);
}

// That the Stop gate never speaks to the model is a behaviour, not a spelling: the suite in
// test/adviser.test.ts runs the real hook and asserts its stdout is empty.

if (problems.length > 0) {
  console.error(`Grok plugin validation failed:\n  ${problems.join("\n  ")}`);
  process.exit(1);
}
console.log(`${version.output.trim()}: plugin manifest, hooks, and command validated.`);
