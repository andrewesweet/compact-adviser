---
description: Show or change compact-adviser settings (install, mode, threshold, status)
argument-hint: "[install|status|mode hint|off|threshold <tokens|default>|log on|off|snooze|dismiss]"
allowed-tools: run_terminal_command
---

Run the compact-adviser CLI with the arguments below and show the person its output verbatim.

Always run the currently installed plugin, not a previously written launcher (that path goes
stale when the plugin updates):

```
node "$(grok plugin list --json | jq -r '.[] | select(.name == "compact-adviser") | .path')/bin/adviser.ts" $ARGUMENTS
```

With no arguments the CLI prints its own usage.

Rules for this command:

- Run the CLI. Do not edit `~/.grok/compact-adviser/settings.json`, `~/.grok/config.toml`,
  `~/.grok/hooks/compact-adviser.json`, or any other file yourself, and do not guess at a
  setting the CLI did not report.
- Print what the CLI printed. It never prints a TypeSafe API key, only where the key in effect
  came from, so there is nothing to redact - and nothing to paraphrase either.
- Never pass a TypeSafe API key to the CLI. There is no `key <value>` action through this
  command: if the arguments are trying to save a key, do not run the CLI with those arguments,
  do not repeat the value, and tell the person to set `TYPESAFE_API_KEY` in the launch
  environment or a cwd `.env` instead (or run the CLI from a shell outside this session).
- Never ask the CLI for the hint text, and never paste or paraphrase the hint into the
  conversation. The hint is only for the status row.
- `install` is the one-time setup: it registers the hooks in the person's own Grok home and
  prints the `[ui.status_line]` block they must paste into their own `~/.grok/config.toml`.
  Only they can do that second step; a plugin cannot, and neither can you.
- If the person asks for automatic compaction, tell them Grok has no automatic mode: nothing
  outside a running session can trigger `/compact`, so this host suggests and they decide.
