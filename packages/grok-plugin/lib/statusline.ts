// The hint surface. Grok gives a Stop hook no display channel at all — "a hook that ran and
// allowed leaves no trace" — so the hint is painted by a `[ui.status_line] type = "command"`
// script instead, which the person opts into in their own Grok-home `config.toml`.
//
// There is exactly one status row, so this script has to stand in for the built-in segments
// it displaces. It paints them itself, in Grok's own order and separator, and adds the hint
// as a second line only while a verdict applies. The hint never reaches the model: the status
// row is drawn for the person, and nothing here writes to the conversation.

export const HINT =
  "Compact adviser: work appears completed or recorded. Run /compact to save tokens.";

const SEPARATOR = " │ ";
const BOLD = "\u001b[1m";
const AMBER = "\u001b[33m";
const RESET = "\u001b[0m";
/** Grok cuts each row line at 1024 characters, counting the escapes; stay well inside that. */
export const MAX_LINE = 900;

export interface StatusPayload {
  cwd?: string;
  session_id?: string;
  prompt_id?: string;
  model?: { id?: string; display_name?: string };
  workspace?: { current_dir?: string };
  context_window?: {
    context_tokens?: number;
    context_window_size?: number;
    used_percentage?: number;
    auto_compact_threshold_percent?: number;
  };
}

export function parsePayload(text: string): StatusPayload {
  try {
    const value = JSON.parse(text) as unknown;
    return value && typeof value === "object" && !Array.isArray(value)
      ? (value as StatusPayload)
      : {};
  } catch {
    return {};
  }
}

function basename(path: string): string {
  const trimmed = path.replace(/[\\/]+$/, "");
  const cut = Math.max(trimmed.lastIndexOf("/"), trimmed.lastIndexOf("\\"));
  return cut >= 0 ? trimmed.slice(cut + 1) : trimmed;
}

function sanitize(text: string): string {
  return text.replace(/[\u0000-\u001f\u007f-\u009f]/g, "");
}

function elide(text: string, limit: number): string {
  return text.length <= limit ? text : `${text.slice(0, Math.max(0, limit - 1))}…`;
}

export function itemsLine(payload: StatusPayload): string {
  const parts: string[] = [];
  const dir = payload.workspace?.current_dir ?? payload.cwd;
  if (dir) {
    const folder = sanitize(basename(dir));
    if (folder) parts.push(elide(folder, 40));
  }
  const name = payload.model?.display_name ?? payload.model?.id;
  if (name) {
    const model = sanitize(name);
    if (model) parts.push(elide(model, 30));
  }
  const percent = payload.context_window?.used_percentage;
  if (typeof percent === "number" && Number.isFinite(percent)) {
    parts.push(`${Math.round(percent)}% ctx`);
  }
  return parts.join(SEPARATOR);
}

/**
 * The row Grok paints. A script that prints nothing takes the row away, so the items line is
 * always printed — even empty — and the hint is an extra line rather than a replacement, so
 * the row does not jump a line as a hint comes and goes.
 */
export function statusLine(payload: StatusPayload, hint: boolean, color = true): string {
  const first = itemsLine(payload);
  const highlighted = color ? `${BOLD}${AMBER}${HINT}${RESET}` : HINT;
  const lines = [first.slice(0, MAX_LINE)];
  if (hint) lines.push(highlighted.slice(0, MAX_LINE));
  return `${lines.join("\n")}\n`;
}
