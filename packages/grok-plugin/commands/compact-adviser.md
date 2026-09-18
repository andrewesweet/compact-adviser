---
description: Show or change compact-adviser settings (mode, threshold, TypeSafe key, status line)
argument-hint: "[status|mode hint|off|threshold <tokens|default>|key <value>|key clear|log on|off|items <list>|snooze|dismiss|setup|doctor]"
allowed-tools: run_terminal_command
---

Run the compact-adviser CLI with the arguments below and show the person its output verbatim.

Command to run:

```
"$HOME/.grok/compact-adviser/adviser.sh" $ARGUMENTS
```

If `GROK_HOME` is set, the launcher is at `$GROK_HOME/compact-adviser/adviser.sh` instead.
With no arguments the CLI prints its own usage.

Rules for this command:

- Run the CLI. Do not edit `~/.grok/compact-adviser/settings.json`, `~/.grok/config.toml`, or
  any other file yourself, and do not guess at a setting the CLI did not report.
- Print what the CLI printed. It never prints a TypeSafe API key, only where the key in effect
  came from, so there is nothing to redact - and nothing to paraphrase either.
- If the person asks for automatic compaction, tell them Grok has no automatic mode: nothing
  outside a running session can trigger `/compact`, so this host suggests and they decide.
- If the launcher is missing, tell them to start a new Grok session with the plugin enabled -
  the plugin writes the launcher at session start - and to check `/plugins`.
