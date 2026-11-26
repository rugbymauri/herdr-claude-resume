#!/bin/sh
# Copies the mod into the Herdr app's home and registers its Claude Code hook.
# Herdr reloads a mod when files under its mods/ folder change, not through a
# symlink, so the mod is copied, not linked.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
home=${HERDR_APP_HOME:-$HOME/.config/herdr-app}
dest="$home/mods/claude-resume"
[ -L "$dest" ] && rm "$dest"
mkdir -p "$dest"
rsync -a --delete --exclude '*.d.ts' --exclude jsconfig.json --exclude '*.error' --exclude __pycache__ \
  "$here/mods/claude-resume/" "$dest/"
chmod +x "$dest/claude-resume.py"

# SessionStart/SessionEnd hook in ~/.claude/settings.json (idempotent).
python3 -I - "$dest/claude-resume.py" <<'PY'
import json, os, sys
script = sys.argv[1]
path = os.path.expanduser("~/.claude/settings.json")
data = json.load(open(path)) if os.path.exists(path) else {}
cmd = f"python3 -I {script} hook"
hooks = data.setdefault("hooks", {})
changed = False
for event in ("SessionStart", "SessionEnd"):
    groups = hooks.setdefault(event, [])
    if not any(h.get("command") == cmd for g in groups for h in g.get("hooks", [])):
        groups.append({"hooks": [{"type": "command", "command": cmd, "timeout": 10}]})
        changed = True
if changed:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    print(f"hook registered in {path}")
PY

herdr-app check "$dest" | tail -1
