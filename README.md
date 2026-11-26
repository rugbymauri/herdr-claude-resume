# Claude Resume

A mod for the [Herdr](https://herdr.dev) desktop app that brings Claude Code
sessions back after a reboot.

herdr-core keeps terminals alive while the app is closed, but a terminal lost to
a reboot, logout or core crash only gets a fresh shell in its last folder — the
program that ran there is never started again. The Herdr TUI resumes agents on
its own; the app does not. This mod closes that gap: it types
`claude --resume <id>` (plus the original `--model`, `--permission-mode`,
`--dangerously-skip-permissions`, …) into the recovered shell, in the folder
the session ran in.

## How it works

- **Hook** (`claude-resume.py hook`, registered for `SessionStart` and
  `SessionEnd` in `~/.claude/settings.json`): stores the session id, folder,
  start time and launch flags as `claude.*` labels on the core terminal
  (`$HC_TERMINAL_ID`). Outside a herdr-core terminal it does nothing. Leaving
  Claude yourself (`/exit`, Ctrl+C, Ctrl+D) removes the labels, so a session you
  closed on purpose does not come back.
- **Mod** (`main.js`): every 10 s it hands the terminals carrying a
  `claude.session` label to `claude-resume.py check`. A terminal is resumed when
  its shell is idle at the prompt and *younger* than the recorded Claude
  process — that is, a recovery shell. Each shell is tried once. Resumes show up
  as a toast; the status bar shows how many sessions are watched (click to check
  now).
- **Adopt**: when the mod starts, Claude instances started before the hook was
  installed are picked up from `~/.claude/sessions/<pid>.json`.

## Install

```sh
./install.sh
```

Then put the mod into a layout, e.g. next to the status bar:

```json
{ "id": "status-row", "type": "row", "size": { "px": 28 }, "children": [
  { "id": "status", "type": "block", "content": { "mod": "workspace-status" } },
  { "id": "claude-resume", "type": "block", "content": { "mod": "claude-resume" }, "size": { "px": 110 } }
] }
```

## Limits

- Sessions are resumed once the Herdr app is open after boot; the mod only runs
  while its block is in the shown layout.
- Launch flags outside the allowlist (`BOOL_FLAGS` / `VALUE_FLAGS` in
  `claude-resume.py`) are not carried over.
- Linux only (reads process start times from `/proc`).
