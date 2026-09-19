/** Offline only. All output must be ignored by Git. See dataset.md for commands. */
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { buildSession, type DatasetCheckpoint, type Source, worksheet, writePrivate } from "../dataset.ts";

const [command, input, output, host] = process.argv.slice(2);
if (!input || !output) throw new Error("Usage: dataset.ts census ROOT OUTPUT HOST | build SOURCES OUTDIR | worksheets CHECKPOINTS OUTDIR");
if (command === "census") {
  if (host !== "claude" && host !== "pi") throw new Error("HOST must be claude or pi.");
  const directories = readdirSync(input, { withFileTypes: true }).filter((entry) => entry.isDirectory());
  const rows = [];
  for (const directory of directories) {
    const path = join(input, directory.name);
    for (const file of readdirSync(path, { withFileTypes: true })) {
      if (!file.isFile() || !file.name.endsWith(".jsonl")) continue;
      const data = buildSession({ host, stratum: directory.name, file: join(path, file.name) });
      rows.push({ source: data.source, session: data.session, settled: data.settled, sized: data.sized, eligible: data.eligible, unique: data.checkpoints.length });
    }
  }
  writePrivate(output, `${JSON.stringify({ at: new Date().toISOString(), directories: directories.length, rows }, null, 2)}\n`);
  console.log(`Scanned ${directories.length} directories and ${rows.length} files.`);
} else if (command === "build") {
  const sources: Source[] = JSON.parse(readFileSync(input, "utf8"));
  const sessions = sources.map(buildSession);
  writePrivate(join(output, "sessions.json"), `${JSON.stringify(sessions.map(({ checkpoints, ...rest }) => ({ ...rest, count: checkpoints.length })), null, 2)}\n`);
  writePrivate(join(output, "checkpoints.jsonl"), sessions.flatMap((session) => session.checkpoints).map((row) => `${JSON.stringify(row)}\n`).join(""));
  console.log(`Built ${sessions.length} sessions and ${sessions.reduce((sum, session) => sum + session.checkpoints.length, 0)} checkpoints.`);
} else if (command === "worksheets") {
  const rows: DatasetCheckpoint[] = readFileSync(input, "utf8").split("\n").filter(Boolean).map((line) => JSON.parse(line));
  for (const row of rows) writePrivate(join(output, `${row.id}.md`), worksheet(row));
  console.log(`Wrote ${rows.length} worksheets.`);
} else {
  throw new Error("Unknown dataset command.");
}
