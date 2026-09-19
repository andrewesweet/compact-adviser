/** Re-gate stored answers through production score/floor functions, never a model call. */
import { readFileSync } from "node:fs";
import { writePrivate } from "../dataset.ts";
import { floorFor, parseJudgment, qualifies, score } from "../../src/judge.ts";

const [checkpointFile, resultFile, settingsFile, output] = process.argv.slice(2);
if (!output) throw new Error("Usage: compare.ts CHECKPOINTS RESULTS SESSION_SETTINGS OUTPUT");
const jsonl = (path: string): Record<string, any>[] => readFileSync(path, "utf8").split("\n").filter(Boolean).map((line) => JSON.parse(line));
const settings = JSON.parse(readFileSync(settingsFile, "utf8")) as Record<string, { window: number; autoCompactThreshold?: number; autoCompactEnabled?: boolean; source: string }>;
const results = new Map(jsonl(resultFile).map((row) => [row.id, row]));
const positive = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value) && value > 0;
const rows = jsonl(checkpointFile).map((checkpoint) => {
  const result = results.get(checkpoint.id);
  const setting = settings[checkpoint.session];
  if (!result?.ok || !setting) return { id: checkpoint.id, ok: false };
  const judgment = parseJudgment({
    answers: {
      done: { type: "choice", choice: result.done, probabilities: result.doneP, confidence: result.doneConf },
      shape: { type: "choice", choice: result.shape, probabilities: result.shapeP, confidence: result.shapeConf },
    },
    model: result.model, usage: { input_tokens: result.inputTokens, output_tokens: result.outputTokens },
  });
  const unpatchedUsage = positive(setting.window) ? checkpoint.contextTokens / setting.window : Number.NaN;
  const threshold = setting.autoCompactEnabled !== false && positive(setting.autoCompactThreshold)
    ? setting.autoCompactThreshold : setting.window;
  const patchedUsage = positive(threshold) ? checkpoint.contextTokens / threshold : Number.NaN;
  return { id: checkpoint.id, ok: true, score: score(judgment), source: setting.source,
    unpatchedUsage, unpatchedFloor: floorFor(unpatchedUsage), unpatchedHint: qualifies(judgment, unpatchedUsage),
    patchedUsage, patchedFloor: floorFor(patchedUsage), patchedHint: qualifies(judgment, patchedUsage) };
});
writePrivate(output, rows.map((row) => `${JSON.stringify(row)}\n`).join(""));
console.log(`Re-gated ${rows.filter((row) => row.ok).length}/${rows.length} rows. Threshold inference is the caller's explicit input.`);
