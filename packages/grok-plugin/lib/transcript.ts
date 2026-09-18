// Grok's `chat_history.jsonl` read as the bounded message list snapshot.ts expects.
//
// The file is documented as "raw chat messages sent to the model" but its record shape is
// not, so every field is checked and an unreadable record is skipped rather than guessed at.
// Observed records: `system`, `user` (typed content blocks), `assistant` (string content plus
// `tool_calls`), `reasoning`, `tool_result` (string content keyed by `tool_call_id`), and
// `backend_tool_call`. Anything else is ignored, which is also what a schema change looks like.
//
// Grok wraps the person's own words in `<user_query>` and pads the same message with
// `<user_info>`, `<git_status>`, and `<system-reminder>` envelopes the harness wrote. Those
// are not user constraints, so they are stripped: what Jev reads is what the person asked.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { MESSAGE_LIMIT, type MessageLike, type ToolUseLike } from "./snapshot.ts";

export interface Transcript {
  messages: MessageLike[];
  /** A user message carried a non-text block, which the snapshot cannot show Jev. */
  hasImages: boolean;
  /** A line in the file was not readable JSON; the snapshot says its coverage is partial. */
  unreadableLines: number;
}

const EMPTY: Transcript = { messages: [], hasImages: false, unreadableLines: 0 };

const QUERY = /<user_query>([\s\S]*?)<\/user_query>/g;
const ENVELOPES = [
  /<system-reminder>[\s\S]*?<\/system-reminder>/g,
  /<user_info>[\s\S]*?<\/user_info>/g,
  /<git_status>[\s\S]*?<\/git_status>/g,
  /<env>[\s\S]*?<\/env>/g,
];

/** The person's own words: the `<user_query>` payload, else the message minus the envelopes. */
export function userText(raw: string): string {
  const queries: string[] = [];
  for (const match of raw.matchAll(QUERY)) {
    const inner = match[1]?.trim();
    if (inner) queries.push(inner);
  }
  if (queries.length) return queries.join("\n\n");
  let rest = raw;
  for (const envelope of ENVELOPES) rest = rest.replace(envelope, "");
  return rest.trim();
}

function blockText(content: unknown): { text: string; hasImages: boolean } {
  if (typeof content === "string") return { text: content, hasImages: false };
  if (!Array.isArray(content)) return { text: "", hasImages: false };
  let hasImages = false;
  const parts: string[] = [];
  for (const block of content) {
    const b = block as { type?: unknown; text?: unknown } | null;
    if (b && typeof b.text === "string") parts.push(b.text);
    else if (b && b.type !== "text") hasImages = true;
  }
  return { text: parts.join("\n"), hasImages };
}

function toolInput(value: unknown): Record<string, unknown> {
  if (value && typeof value === "object" && !Array.isArray(value)) {
    return value as Record<string, unknown>;
  }
  if (typeof value !== "string") return {};
  try {
    const parsed = JSON.parse(value) as unknown;
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? (parsed as Record<string, unknown>)
      : {};
  } catch {
    return {};
  }
}

interface ToolResult {
  text: string;
  isError: boolean;
}

function toolResult(record: Record<string, unknown>): ToolResult {
  const { text } = blockText(record.content);
  const flagged = record.is_error === true || record.isError === true;
  return { text, isError: flagged };
}

/** Parses the whole file; only the last `MESSAGE_LIMIT` messages are kept, newest last. */
export function parseChatHistory(text: string): Transcript {
  const results = new Map<string, ToolResult>();
  const records: Record<string, unknown>[] = [];
  let unreadableLines = 0;
  for (const line of text.split("\n")) {
    if (!line.trim()) continue;
    let record: unknown;
    try {
      record = JSON.parse(line);
    } catch {
      unreadableLines++;
      continue;
    }
    if (!record || typeof record !== "object" || Array.isArray(record)) {
      unreadableLines++;
      continue;
    }
    const value = record as Record<string, unknown>;
    if (value.type === "tool_result" && typeof value.tool_call_id === "string") {
      results.set(value.tool_call_id, toolResult(value));
    }
    records.push(value);
  }

  const messages: MessageLike[] = [];
  let hasImages = false;
  for (const record of records) {
    if (record.type === "user") {
      const parsed = blockText(record.content);
      hasImages ||= parsed.hasImages;
      const own = userText(parsed.text);
      if (own) messages.push({ role: "user", text: own, toolUses: [] });
      continue;
    }
    if (record.type !== "assistant") continue;
    const { text } = blockText(record.content);
    const calls = Array.isArray(record.tool_calls) ? record.tool_calls : [];
    const toolUses: ToolUseLike[] = [];
    for (const call of calls) {
      const c = call as { id?: unknown; name?: unknown; arguments?: unknown } | null;
      if (!c || typeof c.name !== "string") continue;
      const result = typeof c.id === "string" ? results.get(c.id) : undefined;
      toolUses.push({
        ...(typeof c.id === "string" ? { tool_use_id: c.id } : {}),
        tool: c.name,
        input: toolInput(c.arguments),
        text: result?.text ?? "",
        ...(result?.isError ? { isError: true as const } : {}),
      });
    }
    if (!text && !toolUses.length) continue;
    messages.push({ role: "assistant", text, toolUses });
  }
  return { messages: messages.slice(-MESSAGE_LIMIT), hasImages, unreadableLines };
}

/** Reads `<sessionDir>/chat_history.jsonl`; a missing or unreadable file is an empty transcript. */
export function readTranscript(sessionDir: string | undefined): Transcript {
  if (!sessionDir) return EMPTY;
  try {
    return parseChatHistory(readFileSync(join(sessionDir, "chat_history.jsonl"), "utf8"));
  } catch {
    return EMPTY;
  }
}
