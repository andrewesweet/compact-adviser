// Persistent preferences, with the same semantics as the other hosts' configuration.
//
// Grok's own `config.toml` is not the place for them: `grok inspect` reports keys this
// version does not know, so the adviser owns one JSON file instead. Both halves of the
// product read it, and only this package writes it, so every write is a whole-file atomic
// rename rather than an in-place edit.
//
// `auto` is deliberately not a mode here. Grok exposes no way for another process to run
// `/compact` on a live session, so this host is hint-only; see the README.

import { mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";

export type Mode = "hint" | "off";
export const MODES: readonly Mode[] = ["hint", "off"];
export const DEFAULT_MINIMUM = 40000;
export const MAX_SAVED_API_KEY_LENGTH = 1024;
/** The built-in status-line segments this package can paint in place of Grok's own row. */
export const STATUS_LINE_ITEMS = ["cwd", "model", "context", "cost", "session-name"] as const;
export type StatusLineItem = (typeof STATUS_LINE_ITEMS)[number];
export const DEFAULT_STATUS_LINE_ITEMS: readonly StatusLineItem[] = ["cwd", "model", "context"];

export interface Settings {
  version: 1;
  mode: Mode;
  minContextTokens: number;
  logRequests: boolean;
  /** Saved TypeSafe key; a non-empty `TYPESAFE_API_KEY` in the environment still wins. */
  typesafeApiKey: string;
  /** Built-in segments the hint row keeps, since it replaces the user's only status row. */
  statusLineItems: StatusLineItem[];
}

export const DEFAULT_SETTINGS: Readonly<Settings> = Object.freeze({
  version: 1,
  mode: "hint",
  minContextTokens: DEFAULT_MINIMUM,
  logRequests: false,
  typesafeApiKey: "",
  statusLineItems: [...DEFAULT_STATUS_LINE_ITEMS],
});

export class SettingsError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "SettingsError";
  }
}

export function parseMinimum(text: string): number {
  const value = text.trim();
  const number = Number(value);
  if (!/^\d+$/.test(value) || !Number.isSafeInteger(number) || number <= 0) {
    throw new SettingsError("Enter a positive whole number of tokens, for example 40000.");
  }
  return number;
}

export function parseMode(text: string): Mode {
  const value = text.trim().toLowerCase();
  if (value === "auto") {
    throw new SettingsError(
      "Automatic compaction is not available on Grok: nothing outside the session can run /compact. This host is hint-only.",
    );
  }
  if (!MODES.includes(value as Mode)) throw new SettingsError("Choose hint or off.");
  return value as Mode;
}

export function parseSavedApiKey(text: string): string {
  const value = text.trim();
  if (!value) throw new SettingsError("Enter a TypeSafe API key, or clear it with `key clear`.");
  if (value.length > MAX_SAVED_API_KEY_LENGTH) {
    throw new SettingsError("That value is too long to save as a TypeSafe API key.");
  }
  for (let i = 0; i < value.length; i++) {
    const code = value.charCodeAt(i);
    if (code < 32 || code === 127) {
      throw new SettingsError("The key cannot contain control characters.");
    }
  }
  return value;
}

export function parseStatusLineItems(text: string): StatusLineItem[] {
  const parts = text
    .split(/[\s,]+/)
    .map((part) => part.trim())
    .filter(Boolean);
  if (!parts.length)
    throw new SettingsError(`Name at least one of: ${STATUS_LINE_ITEMS.join(", ")}.`);
  const items: StatusLineItem[] = [];
  for (const part of parts) {
    if (!STATUS_LINE_ITEMS.includes(part as StatusLineItem)) {
      throw new SettingsError(
        `Unknown status-line item "${part}". Choose from: ${STATUS_LINE_ITEMS.join(", ")}.`,
      );
    }
    if (!items.includes(part as StatusLineItem)) items.push(part as StatusLineItem);
  }
  return items;
}

/**
 * A stored record is either usable or reported. A field of the wrong kind is never silently
 * replaced: the hook then does nothing at all, which is the contract's "leave context alone".
 */
export function parseSettings(value: unknown): Settings {
  if (value === undefined)
    return { ...DEFAULT_SETTINGS, statusLineItems: [...DEFAULT_STATUS_LINE_ITEMS] };
  const s = value as Partial<Settings> | null;
  if (!s || typeof s !== "object" || Array.isArray(s) || s.version !== 1) {
    throw new SettingsError("Cannot read the compact-adviser settings file; no action is taken.");
  }
  const mode = s.mode ?? DEFAULT_SETTINGS.mode;
  if (typeof mode !== "string" || !MODES.includes(mode as Mode)) {
    throw new SettingsError("Cannot read the compact-adviser mode setting; no action is taken.");
  }
  const minimum = s.minContextTokens ?? DEFAULT_MINIMUM;
  if (typeof minimum !== "number" || !Number.isSafeInteger(minimum) || minimum <= 0) {
    throw new SettingsError(
      "Cannot read the compact-adviser minimum context setting; no action is taken.",
    );
  }
  const logRequests = s.logRequests ?? false;
  if (typeof logRequests !== "boolean") {
    throw new SettingsError(
      "Cannot read the compact-adviser request-log setting; no action is taken.",
    );
  }
  const key = s.typesafeApiKey ?? "";
  if (typeof key !== "string") {
    throw new SettingsError(
      "Cannot read the compact-adviser TypeSafe key setting; no action is taken.",
    );
  }
  const rawItems = s.statusLineItems ?? DEFAULT_STATUS_LINE_ITEMS;
  if (
    !Array.isArray(rawItems) ||
    !rawItems.every((item) => STATUS_LINE_ITEMS.includes(item as StatusLineItem))
  ) {
    throw new SettingsError(
      "Cannot read the compact-adviser status-line items; no action is taken.",
    );
  }
  return {
    version: 1,
    mode: mode as Mode,
    minContextTokens: minimum,
    logRequests,
    typesafeApiKey: key,
    statusLineItems: [...(rawItems as StatusLineItem[])],
  };
}

export function readSettings(path: string): Settings {
  let text: string;
  try {
    text = readFileSync(path, "utf8");
  } catch {
    return parseSettings(undefined);
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    throw new SettingsError(
      "The compact-adviser settings file is not valid JSON; no action is taken.",
    );
  }
  return parseSettings(parsed);
}

/** Whole-file write through a temporary name, so a crashed hook cannot leave half a record. */
export function writeSettings(path: string, settings: Settings): void {
  mkdirSync(dirname(path), { recursive: true });
  const temporary = join(dirname(path), `.${process.pid}-${Date.now()}.settings.json`);
  writeFileSync(temporary, `${JSON.stringify(settings, null, 2)}\n`, { mode: 0o600 });
  renameSync(temporary, path);
}

export function formatTokens(count: number): string {
  return count.toLocaleString("en-US");
}
