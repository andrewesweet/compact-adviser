// The two records the two halves of the product pass between processes.
//
// Cooldown state belongs to the Stop hook alone. The verdict is the whole conversation
// between the hook that judges and the status line that paints: one small file per session,
// written when a checkpoint qualifies and removed the moment it stops being true.

import { mkdirSync, readFileSync, renameSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { initialState, restoreState, type SessionState } from "./state.ts";

/** A verdict older than this is never painted, whatever else happened. */
export const VERDICT_MAX_AGE_MS = 6 * 60 * 60 * 1000;

export interface Verdict {
  version: 1;
  sessionId: string;
  /** The turn the hint was earned at; the next turn's own id retires it. */
  promptId: string | null;
  at: number;
  /** Context tokens when the hint was earned; a big drop means a compaction already ran. */
  tokens: number | null;
  score: number;
  floor: number;
}

function writeAtomic(path: string, text: string): void {
  mkdirSync(dirname(path), { recursive: true });
  const temporary = join(dirname(path), `.${process.pid}-${Date.now()}.tmp`);
  writeFileSync(temporary, text, { mode: 0o600 });
  renameSync(temporary, path);
}

function readJson(path: string): unknown {
  try {
    return JSON.parse(readFileSync(path, "utf8"));
  } catch {
    return undefined;
  }
}

export function loadSessionState(path: string, now: number): SessionState {
  let text: string;
  try {
    text = readFileSync(path, "utf8");
  } catch (error) {
    if ((error as { code?: string }).code === "ENOENT") return initialState(false, now);
    throw new Error("Cannot read the compact-adviser session state file; no action is taken.");
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    throw new Error(
      "The compact-adviser session state file is not valid JSON; no action is taken.",
    );
  }
  return restoreState(parsed, now);
}

export function saveSessionState(path: string, state: SessionState): void {
  writeAtomic(path, `${JSON.stringify(state)}\n`);
}

export function resetSessionState(path: string, now: number): SessionState {
  const state = initialState(true, now);
  saveSessionState(path, state);
  return state;
}

export function saveVerdict(path: string, verdict: Verdict): void {
  writeAtomic(path, `${JSON.stringify(verdict)}\n`);
}

export function clearVerdict(path: string): void {
  rmSync(path, { force: true });
}

export function loadVerdict(path: string): Verdict | undefined {
  const value = readJson(path);
  if (!value || typeof value !== "object" || Array.isArray(value)) return undefined;
  const v = value as Partial<Verdict>;
  if (
    v.version !== 1 ||
    typeof v.sessionId !== "string" ||
    typeof v.at !== "number" ||
    !Number.isFinite(v.at) ||
    !(v.promptId === null || typeof v.promptId === "string") ||
    !(v.tokens === null || (typeof v.tokens === "number" && Number.isFinite(v.tokens))) ||
    typeof v.score !== "number" ||
    typeof v.floor !== "number"
  ) {
    return undefined;
  }
  return v as Verdict;
}

/**
 * Whether the hint the hook earned is still the truth on screen.
 *
 * The next turn retires it (its `prompt_id` differs), a compaction retires it (the window
 * dropped under the tokens it was earned at), and age retires it, so a status line that
 * outlives the hooks — a resumed session, a reload — cannot paint a stale hint.
 */
export function verdictApplies(
  verdict: Verdict,
  now: number,
  promptId: string | undefined,
  tokens: number | undefined,
): boolean {
  if (now - verdict.at > VERDICT_MAX_AGE_MS || now < verdict.at - 60000) return false;
  if (promptId !== undefined && verdict.promptId !== null && promptId !== verdict.promptId) {
    return false;
  }
  if (tokens !== undefined && verdict.tokens !== null && tokens < verdict.tokens * 0.8) {
    return false;
  }
  return true;
}

/**
 * A one-line note about the last TypeSafe outcome, for `status`. The hook has no way to speak
 * to the person — its stdout is a control channel — so a rejected key would otherwise be
 * invisible forever. It never holds a key or a judgment, only the error kind.
 */
export function diagnosticPath(dir: string, sessionId: string): string {
  return join(dir, "diagnostics", `${sessionId.replace(/[^A-Za-z0-9._-]+/g, "_")}.txt`);
}

export function saveDiagnostic(path: string, kind: string): void {
  writeAtomic(path, `${kind}\n`);
}

export function clearDiagnostic(path: string): void {
  rmSync(path, { force: true });
}

export function loadDiagnostic(path: string): string | undefined {
  try {
    const text = readFileSync(path, "utf8").trim();
    return text && text.length <= 64 ? text : undefined;
  } catch {
    return undefined;
  }
}
