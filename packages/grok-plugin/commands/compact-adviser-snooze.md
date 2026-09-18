---
description: Snooze compact-adviser hints for three exchanges
allowed-tools: run_terminal_command
---

Run exactly this command and show the person its output verbatim. Extra words after this slash
command are ignored; do not pass them to the CLI.

```
node "$(node -e 'const l=JSON.parse(require("child_process").execFileSync("grok",["plugin","list","--json"],{encoding:"utf8"})); const p=(Array.isArray(l)?l:[]).find(x=>x&&x.name==="compact-adviser"); if(!p||typeof p.path!=="string") throw new Error("compact-adviser is not installed"); process.stdout.write(p.path)')/bin/adviser.ts" snooze
```

Rules for this command:

- Run that CLI command only. Do not edit files under `${GROK_HOME:-~/.grok}` or any other file
  yourself, and do not guess at a setting the CLI did not report.
- Print what the CLI printed.
- Never pass a TypeSafe API key to the CLI. Do not type secrets after this slash command; Grok appends extra words to the model.
