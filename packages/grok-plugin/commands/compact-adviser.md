---
description: Show or change compact-adviser settings (install, mode, threshold, status)
argument-hint: "[install|status|mode <hint|off>|threshold <tokens|default>|log <on|off>|snooze|dismiss]"
allowed-tools: run_terminal_command
---

Run the compact-adviser CLI and show the person its output verbatim.

Always run the currently installed plugin, not a previously written launcher (that path goes
stale when the plugin updates):

```
node "$(node -e 'const l=JSON.parse(require("child_process").execFileSync("grok",["plugin","list","--json"],{encoding:"utf8"})); const p=(Array.isArray(l)?l:[]).find(x=>x&&x.name==="compact-adviser"); if(!p||typeof p.path!=="string") throw new Error("compact-adviser is not installed"); process.stdout.write(p.path)')/bin/adviser.ts"
```

Append at most one allowlisted subcommand, and only when the person asked for that action:
`install`, `status`, `mode hint`, `mode off`, `threshold` plus a positive integer or `default`,
`log on`, `log off`, `snooze`, or `dismiss`. With no subcommand the CLI prints its own usage.

Do not append any other words. Do not pass a TypeSafe API key or a `key` subcommand.

Rules for this command:

- Run the CLI. Do not edit files under `${GROK_HOME:-~/.grok}` or any other file yourself,
  and do not guess at a setting the CLI did not report.
- Print what the CLI printed. It never prints a TypeSafe API key, only where the key in effect
  came from, so there is nothing to redact - and nothing to paraphrase either.
- Never pass a TypeSafe API key to the CLI. Tell the person to set `TYPESAFE_API_KEY` in the
  launch environment or a cwd `.env` instead (or run the CLI from a shell outside this session).
- Never ask the CLI for the hint text, and never paste or paraphrase the hint into the
  conversation. The hint is only for the status row.
- `install` is the one-time setup: it registers the hooks in the person's own Grok home and
  prints the `[ui.status_line]` block they must paste into the config.toml path it named.
  Only they can do that second step; a plugin cannot, and neither can you.
- If the person asks for automatic compaction, tell them Grok has no automatic mode: nothing
  outside a running session can trigger `/compact`, so this host suggests and they decide.
