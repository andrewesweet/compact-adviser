---
description: Show or change compact-adviser settings (install, mode, threshold, TypeSafe key, status line)
argument-hint: "[install|status|mode hint|off|threshold <tokens|default>|key <value>|key clear|log on|off|items <list>|snooze|dismiss|setup|doctor]"
allowed-tools: run_terminal_command
---

Run the compact-adviser CLI with the arguments below and show the person its output verbatim.

Preferred command, once the plugin has written its launcher:

```
"${GROK_HOME:-$HOME/.grok}/compact-adviser/adviser.sh" $ARGUMENTS
```

If that file does not exist yet - which is the case before the first `install` - find the
installed package and run its entry point directly:

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
- `install` is the one-time setup: it registers the hooks in the person's own Grok home and
  prints the `[ui.status_line]` block they must paste into their own `~/.grok/config.toml`.
  Only they can do that second step; a plugin cannot, and neither can you.
- If the person asks for automatic compaction, tell them Grok has no automatic mode: nothing
  outside a running session can trigger `/compact`, so this host suggests and they decide.
