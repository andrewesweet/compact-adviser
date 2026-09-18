// Where the adviser keeps its own files, and where Grok keeps a session's.
//
// The Stop hook is a plugin hook and gets `GROK_PLUGIN_DATA`, but the status-line script is
// not a plugin hook and gets nothing, so both halves have to agree on a path neither is told:
// `${GROK_HOME:-~/.grok}/compact-adviser`. Grok reads `GROK_HOME` the same way for its own
// sessions tree, so one variable moves the whole product.

import { existsSync, readdirSync, readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

export type Env = Readonly<Record<string, string | undefined>>;

/** Grok's own home, which `GROK_HOME` moves; the sessions tree and our data live under it. */
export function grokHome(env: Env = process.env): string {
  const override = env.GROK_HOME?.trim();
  return override ? override.replace(/[\\/]+$/, "") : join(homedir(), ".grok");
}

/** Settings, per-session cooldowns, verdicts, request logs, and the two launcher scripts. */
export function dataDir(env: Env = process.env): string {
  return join(grokHome(env), "compact-adviser");
}

export function settingsPath(env: Env = process.env): string {
  return join(dataDir(env), "settings.json");
}

export function sessionStatePath(sessionId: string, env: Env = process.env): string {
  return join(dataDir(env), "sessions", `${safeId(sessionId)}.json`);
}

export function verdictPath(sessionId: string, env: Env = process.env): string {
  return join(dataDir(env), "verdicts", `${safeId(sessionId)}.json`);
}

/** A session id is a UUID in practice, but it reaches us from a payload, so never trust it. */
export function safeId(sessionId: string): string {
  return sessionId.replace(/[^A-Za-z0-9._-]+/g, "_").replace(/^\.+/, "") || "session";
}

/** Grok names a session group by URL-encoding the working directory. */
export function encodeCwd(cwd: string): string {
  return encodeURIComponent(cwd);
}

/**
 * The directory Grok wrote this session to. The encoded-cwd name is documented, but a long
 * path falls back to a slug plus a hash, so a miss re-reads the tree and matches on the
 * session id itself rather than guessing at the slug. Returns undefined when neither finds it.
 */
export function sessionDir(
  cwd: string,
  sessionId: string,
  env: Env = process.env,
): string | undefined {
  const root = join(grokHome(env), "sessions");
  const direct = join(root, encodeCwd(cwd), sessionId);
  if (existsSync(direct)) return direct;
  let groups: string[];
  try {
    groups = readdirSync(root, { withFileTypes: true })
      .filter((entry) => entry.isDirectory())
      .map((entry) => entry.name);
  } catch {
    return undefined;
  }
  for (const group of groups) {
    const candidate = join(root, group, sessionId);
    if (existsSync(candidate)) return candidate;
  }
  return undefined;
}

/** The original path a slug-named group stands for, when Grok recorded one. */
export function groupCwd(groupDir: string): string | undefined {
  try {
    return readFileSync(join(groupDir, ".cwd"), "utf8").trim() || undefined;
  } catch {
    return undefined;
  }
}
