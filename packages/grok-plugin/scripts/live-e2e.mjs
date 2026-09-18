// Live regression against the real Grok agent.
//
// Grok runs with an isolated GROK_HOME, this package installed the way a person installs it
// (`grok plugin install --trust`) and its hooks registered by `compact-adviser install`, a
// deterministic local stand-in for the xAI Responses API (`--xai-api-base-url`), and a local
// TypeSafe fixture reached through the plugin's loopback-only COMPACT_ADVISER_TEST_ENDPOINT.
// No account credential, model quota, or real TypeSafe request is used, and no user
// configuration is read or written.
//
// Grok 1.0.34 lists a plugin's own hooks.json but never loads it into a session, which is why
// `install` exists and why this script watches the loaded-hook count: a future Grok that does
// load plugin hooks is caught here rather than in someone's session.
//
// It proves, against the host rather than a description of it:
//   1. `install` registers hooks Grok actually runs, and session start refreshes the two
//      launchers the status line and the slash command point at.
//   2. A real completed turn fires the Stop gate, which judges the real transcript Grok wrote
//      and records a verdict - while allowing the stop, with nothing on stdout, so the hint
//      cannot reach the model's context.
//   3. The status-line launcher, fed a real status-line payload, paints the built-in segments
//      and the hint.
//   4. The plugin itself is installed, enabled, and its /compact-adviser command discoverable.
//   5. With mode off the same turn asks TypeSafe nothing.
//   6. With the session kill switch set, a real turn asks TypeSafe nothing and paints no hint.
//
// COMPACT_TEST_KEEP_LAB=1 keeps the lab directory for inspection.

import { execFileSync, spawn } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { join } from "node:path";

const PACKAGE = join(import.meta.dirname, "..");
const GROK = process.env.COMPACT_TEST_GROK_BIN ?? "grok";
const HINT = "Compact adviser: work appears completed or recorded. Run /compact to save tokens.";
const TYPESAFE_KEY = "tsk-live-fixture-key";
const version = execFileSync(GROK, ["--version"], { encoding: "utf8" }).trim();

const lab = mkdtempSync(join(tmpdir(), "compact-adviser-grok-e2e-"));
const home = join(lab, "grok-home");
const project = join(lab, "project");
mkdirSync(home, { recursive: true });
mkdirSync(project, { recursive: true });
// Only this plugin's hooks should run: the compat scanners would otherwise pull in whatever
// ~/.claude and ~/.cursor hold on the machine running this.
writeFileSync(
  join(home, "config.toml"),
  [
    "[plugins]",
    'enabled = ["compact-adviser"]',
    "",
    "[compat.claude]",
    "hooks = false",
    "",
    "[compat.cursor]",
    "hooks = false",
    "",
    "[compat.codex]",
    "hooks = false",
    "",
  ].join("\n"),
);

// Install the plugin as a person would, and read back what Grok says it got.
execFileSync(GROK, ["plugin", "install", PACKAGE, "--trust"], {
  encoding: "utf8",
  env: { ...process.env, GROK_HOME: home },
});
const inventory = execFileSync(GROK, ["inspect"], {
  encoding: "utf8",
  cwd: project,
  env: { ...process.env, GROK_HOME: home },
});

const problems = [];
function check(condition, description, detail = "") {
  if (condition) console.log(`  ok   ${description}`);
  else {
    console.log(`  FAIL ${description}${detail ? `\n       ${detail}` : ""}`);
    problems.push(description);
  }
}

// --- the two local servers ----------------------------------------------------------

/** A finished, hands-on answer long enough to clear the product's own context minimum. */
const ANSWER = `Done: the parser fix is implemented, 12 of 12 tests pass, and it is committed. Nothing is pending.\n\n${"Implementation notes for the parser module, recorded while the work was done. ".repeat(3000)}`;

function responsesReply(model) {
  return {
    id: "resp_fixture",
    object: "response",
    created_at: Math.floor(Date.now() / 1000),
    status: "completed",
    model: model ?? "grok-4.6",
    output: [
      {
        id: "msg_fixture",
        type: "message",
        role: "assistant",
        status: "completed",
        content: [{ type: "output_text", text: ANSWER, annotations: [] }],
      },
    ],
    usage: {
      input_tokens: 220000,
      input_tokens_details: {
        cached_tokens: 0,
        text_tokens: 220000,
        audio_tokens: 0,
        image_tokens: 0,
      },
      output_tokens: 60,
      output_tokens_details: { reasoning_tokens: 0, text_tokens: 60, audio_tokens: 0 },
      total_tokens: 220060,
    },
  };
}

function listen(server) {
  return new Promise((resolve) => {
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      resolve(`http://127.0.0.1:${address.port}`);
    });
  });
}

const xai = createServer((request, response) => {
  let body = "";
  request.on("data", (chunk) => {
    body += chunk;
  });
  request.on("end", () => {
    if (!request.url.includes("responses")) {
      response.writeHead(200, { "content-type": "application/json" });
      response.end(JSON.stringify({ data: [{ id: "grok-4.6", object: "model" }] }));
      return;
    }
    let parsed = {};
    try {
      parsed = JSON.parse(body);
    } catch {
      parsed = {};
    }
    const payload = responsesReply(parsed.model);
    if (parsed.stream) {
      response.writeHead(200, { "content-type": "text/event-stream" });
      response.write(
        `event: response.completed\ndata: ${JSON.stringify({
          type: "response.completed",
          sequence_number: 1,
          response: payload,
        })}\n\n`,
      );
      response.end();
      return;
    }
    response.writeHead(200, { "content-type": "application/json" });
    response.end(JSON.stringify(payload));
  });
});

const jevRequests = [];
const typesafe = createServer((request, response) => {
  let body = "";
  request.on("data", (chunk) => {
    body += chunk;
  });
  request.on("end", () => {
    jevRequests.push(body);
    const choice = (name, p, others) => ({
      type: "choice",
      choice: name,
      confidence: p,
      probabilities: { [name]: p, [others[0]]: (1 - p) / 2, [others[1]]: (1 - p) / 2 },
    });
    response.writeHead(200, { "content-type": "application/json" });
    response.end(
      JSON.stringify({
        model: "jev-1.13.0",
        answers: {
          done: choice("finished", 0.97, ["not_finished", "unclear"]),
          shape: choice("hands_on", 0.96, ["coordinating", "unclear"]),
        },
        usage: { input_tokens: 2500, output_tokens: 40 },
      }),
    );
  });
});

const xaiUrl = await listen(xai);
const typesafeUrl = `${await listen(typesafe)}/v1/systemone`;

// --- the ACP client -----------------------------------------------------------------

function agentEnv() {
  return {
    ...process.env,
    GROK_HOME: home,
    XAI_API_KEY: "xai-fixture-key-not-real",
    TYPESAFE_API_KEY: TYPESAFE_KEY,
    COMPACT_ADVISER_TEST_ENDPOINT: typesafeUrl,
    NO_COLOR: "1",
  };
}

/** One prompt through a fresh `grok agent … stdio` process; resolves with its session id. */
function runTurn(prompt, extraEnv = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(
      GROK,
      [
        "agent",
        "--no-leader",
        "--always-approve",
        "--xai-api-base-url",
        xaiUrl,
        "--cli-chat-proxy-base-url",
        xaiUrl,
        // The debug log is what hookCounts() reads; it is also the first thing to look at
        // when this script fails, so it is always written.
        "--debug",
        "--debug-file",
        join(lab, "grok-debug.log"),
        "stdio",
      ],
      { stdio: ["pipe", "pipe", "pipe"], env: { ...agentEnv(), ...extraEnv } },
    );
    const timer = setTimeout(() => {
      child.kill("SIGKILL");
      reject(new Error(`the agent did not finish the turn in time:\n${stderr.slice(-2000)}`));
    }, 180000);
    let buffer = "";
    let stderr = "";
    let id = 0;
    const pending = new Map();
    const send = (method, params) => {
      const rid = ++id;
      child.stdin.write(`${JSON.stringify({ jsonrpc: "2.0", id: rid, method, params })}\n`);
      return new Promise((res, rej) => pending.set(rid, { res, rej }));
    };
    child.stderr.on("data", (chunk) => {
      stderr += chunk;
    });
    child.stdout.on("data", (chunk) => {
      buffer += chunk;
      for (let cut = buffer.indexOf("\n"); cut >= 0; cut = buffer.indexOf("\n")) {
        const line = buffer.slice(0, cut).trim();
        buffer = buffer.slice(cut + 1);
        if (!line) continue;
        let message;
        try {
          message = JSON.parse(line);
        } catch {
          continue;
        }
        if (
          message.id !== undefined &&
          (message.result !== undefined || message.error !== undefined)
        ) {
          const waiter = pending.get(message.id);
          pending.delete(message.id);
          if (waiter) (message.error ? waiter.rej : waiter.res)(message.error ?? message.result);
        } else if (message.id !== undefined && message.method) {
          // Permission requests: always-approve is on, but answer anything that still asks.
          child.stdin.write(
            `${JSON.stringify({
              jsonrpc: "2.0",
              id: message.id,
              result: { outcome: { outcome: "selected", optionId: "allow" } },
            })}\n`,
          );
        }
      }
    });
    (async () => {
      await send("initialize", { protocolVersion: 1, clientCapabilities: {} });
      const session = await send("session/new", {
        cwd: project,
        mcpServers: [],
        _meta: { yoloMode: true },
      });
      await send("session/prompt", {
        sessionId: session.sessionId,
        prompt: [{ type: "text", text: prompt }],
      });
      // The Stop gate runs on the turn's critical path, so the prompt has already waited for
      // it; give the session a moment to settle before reading what it wrote.
      await new Promise((r) => setTimeout(r, 1500));
      clearTimeout(timer);
      child.kill();
      resolve(session.sessionId);
    })().catch((error) => {
      clearTimeout(timer);
      child.kill("SIGKILL");
      reject(new Error(`${error?.message ?? JSON.stringify(error)}\n${stderr.slice(-2000)}`));
    });
  });
}

function cli(args, env = {}) {
  return execFileSync(process.execPath, [join(PACKAGE, "bin", "adviser.ts"), ...args], {
    encoding: "utf8",
    cwd: project,
    env: { ...agentEnv(), ...env },
  });
}

/** Every "loaded hooks" line Grok logged, when the run asked for its debug log. */
function hookCounts() {
  try {
    const log = readFileSync(join(lab, "grok-debug.log"), "utf8");
    return (log.match(/loaded hooks hook_count=\d+/g) ?? []).join(" ");
  } catch {
    return "";
  }
}

// --- the run ------------------------------------------------------------------------

let failure;
try {
  check(
    /compact-adviser \(user, enabled\)/.test(inventory),
    "grok lists the installed plugin as enabled",
  );
  check(
    /compact-adviser\s+plugin: compact-adviser/.test(inventory),
    "grok lists the plugin's /compact-adviser slash command",
  );
  console.log(cli(["install"]).split("\n")[0]);
  console.log(`Grok ${version}: driving one completed turn with the plugin installed.`);
  const sessionId = await runTurn(
    "Say that the parser fix is implemented, tested, and committed, and that nothing is pending.",
  );

  const dataDir = join(home, "compact-adviser");
  check(
    existsSync(join(dataDir, "status-line.sh")) && existsSync(join(dataDir, "adviser.sh")),
    "session start wrote both launchers at the fixed path",
    dataDir,
  );
  check(
    jevRequests.length === 1,
    `the Stop gate asked TypeSafe once (asked ${jevRequests.length})`,
  );
  const request = JSON.parse(jevRequests[0] ?? "{}");
  check(
    (request.state?.recent ?? []).some((entry) => entry.text?.includes("is committed")),
    "the judge read the answer Grok actually wrote to the transcript",
  );
  check(
    !JSON.stringify(request).includes(TYPESAFE_KEY),
    "the request body carries no TypeSafe key",
  );
  const verdict = join(dataDir, "verdicts", `${sessionId}.json`);
  check(existsSync(verdict), "the qualifying checkpoint recorded a verdict", verdict);

  const payload = JSON.stringify({
    schema_version: 1,
    session_id: sessionId,
    cwd: project,
    workspace: { current_dir: project },
    model: { id: "grok-4.6", display_name: "Grok 4.6" },
    context_window: { context_tokens: 220000, context_window_size: 500000, used_percentage: 44 },
  });
  const row = execFileSync(join(dataDir, "status-line.sh"), [], {
    encoding: "utf8",
    input: payload,
    env: { ...agentEnv() },
  });
  check(row.includes(HINT), "the status-line launcher paints the hint", row.trim());
  check(
    row.includes("Grok 4.6") && row.includes("44% ctx"),
    "the row keeps the built-in segments it stands in for",
    row.trim(),
  );

  // Exactly the handlers `install` registered. If Grok starts loading a plugin's own
  // hooks.json as well, this doubles, and the README's install step can be retired.
  check(
    hookCounts() === "loaded hooks hook_count=6",
    "grok loaded one copy of the six registered handlers",
    hookCounts() || "no loaded-hook lines in the debug log",
  );

  cli(["mode", "off"]);
  await runTurn("Say again that everything is committed and nothing is pending.");
  check(jevRequests.length === 1, "mode off asks TypeSafe nothing");

  cli(["mode", "hint"]);
  const disabledSessionId = await runTurn(
    "Say once more that everything is committed and nothing is pending.",
    { COMPACT_ADVISER_DISABLE: "1" },
  );
  check(jevRequests.length === 1, "the disabled real turn asks TypeSafe nothing");
  const disabledRow = execFileSync(join(dataDir, "status-line.sh"), [], {
    encoding: "utf8",
    input: JSON.stringify({ ...JSON.parse(payload), session_id: disabledSessionId }),
    env: { ...agentEnv(), COMPACT_ADVISER_DISABLE: "1" },
  });
  check(!disabledRow.includes(HINT), "the disabled real turn paints no hint", disabledRow.trim());
} catch (error) {
  failure = error;
} finally {
  xai.close();
  typesafe.close();
  if (process.env.COMPACT_TEST_KEEP_LAB === "1") console.log(`lab kept at ${lab}`);
  else rmSync(lab, { recursive: true, force: true });
}

if (failure) {
  console.error(failure.message ?? failure);
  process.exit(1);
}
if (problems.length > 0) {
  console.error(`Grok ${version}: live regression failed:\n  ${problems.join("\n  ")}`);
  process.exit(1);
}
console.log(`Grok ${version}: live regression passed.`);
