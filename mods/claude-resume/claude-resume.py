#!/usr/bin/env python3
"""Claude-Sessions in der Herdr-App nach einem Reboot fortsetzen.

herdr-core ersetzt verlorene Terminals (Reboot, Logout, Core-Crash) durch eine
Shell im letzten Ordner, startet das ursprüngliche Programm aber nie neu. Dieses
Skript schliesst die Lücke in zwei Teilen:

  hook   Claude-Code-Hook (SessionStart/SessionEnd). Merkt sich am hc-Terminal
         ($HC_TERMINAL_ID) Session-ID, Ordner, Startzeit und Startoptionen als
         Labels `claude.*`. Beendet man Claude selbst (/exit, Ctrl+D, …), werden
         die Labels entfernt: diese Session soll nicht zurückkommen.

  check  Vom Mod `claude-resume` periodisch aufgerufen. Sucht Terminals mit
         `claude.session`, deren Shell *jünger* als die gemerkte Claude-Instanz
         ist (= Recovery-Shell nach Verlust) und in denen gerade nichts anderes
         läuft, und tippt dort `claude --resume <id>` ein. Gibt JSON aus.
"""

import json
import os
import shlex
import subprocess
import sys
import time

PREFIX = "claude."
SHELLS = {"zsh", "bash", "fish", "sh", "dash", "nu", "ksh", "tcsh"}
# Shell muss so lange laufen, bevor wir tippen (Prompt/rc-Dateien fertig).
MIN_SHELL_AGE_S = 3

# Startoptionen, die beim Fortsetzen wieder mitgegeben werden.
BOOL_FLAGS = {
    "--dangerously-skip-permissions",
    "--allow-dangerously-skip-permissions",
    "--verbose",
    "--ide",
    "--chrome",
    "--no-chrome",
    "--strict-mcp-config",
}
VALUE_FLAGS = {
    "--model",
    "--permission-mode",
    "--agent",
    "--effort",
    "--fallback-model",
    "--settings",
    "--append-system-prompt",
    "--system-prompt",
}


def hc(*args, check=False):
    return subprocess.run(
        ["hc", *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=check
    )


def _btime():
    with open("/proc/stat") as f:
        for line in f:
            if line.startswith("btime "):
                return int(line.split()[1])
    return 0


def proc_start_ms(pid):
    """Startzeit eines Prozesses als Epoch-Millisekunden, None falls weg."""
    try:
        with open(f"/proc/{pid}/stat") as f:
            stat = f.read()
    except OSError:
        return None
    fields = stat[stat.rindex(")") + 2 :].split()
    ticks = int(fields[19])  # Feld 22 (starttime), nach comm/state verschoben
    hz = os.sysconf("SC_CLK_TCK")
    return int((_btime() + ticks / hz) * 1000)


def proc_cmdline(pid):
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            return [a.decode(errors="replace") for a in f.read().split(b"\0") if a]
    except OSError:
        return []


def proc_ppid(pid):
    try:
        with open(f"/proc/{pid}/stat") as f:
            stat = f.read()
        return int(stat[stat.rindex(")") + 2 :].split()[1])
    except (OSError, ValueError, IndexError):
        return 0


def find_claude_pid():
    """Den Claude-Prozess oberhalb des Hooks finden."""
    pid = os.getppid()
    for _ in range(8):
        if pid <= 1:
            break
        argv = proc_cmdline(pid)
        names = [os.path.basename(a) for a in argv[:2]]
        if "claude" in names or any(a.endswith("/claude") for a in argv[:2]):
            return pid
        pid = proc_ppid(pid)
    return None


def resume_flags(argv):
    out, i = [], 1
    while i < len(argv):
        a = argv[i]
        name = a.split("=", 1)[0]
        if a in BOOL_FLAGS:
            out.append(a)
        elif name in VALUE_FLAGS:
            if "=" in a:
                out.append(a)
            elif i + 1 < len(argv):
                out += [a, argv[i + 1]]
                i += 1
        i += 1
    return out


def labels_of(terminal):
    r = hc("get", terminal, "--json")
    if r.returncode != 0:
        return {}
    try:
        return json.loads(r.stdout).get("labels") or {}
    except json.JSONDecodeError:
        return {}


def cmd_hook():
    terminal = os.environ.get("HC_TERMINAL_ID")
    if not terminal:
        return  # nicht in einem herdr-core-Terminal
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError:
        return
    event = data.get("hook_event_name")
    session = data.get("session_id") or ""
    if not session:
        return

    if event == "SessionStart":
        pid = find_claude_pid()
        argv = proc_cmdline(pid) if pid else []
        flags = shlex.join(resume_flags(argv))
        started = proc_start_ms(pid) if pid else int(time.time() * 1000)
        hc(
            "label",
            terminal,
            f"{PREFIX}session={session}",
            f"{PREFIX}cwd={data.get('cwd') or os.getcwd()}",
            f"{PREFIX}started={started}",
            f"{PREFIX}args={flags}",
            f"{PREFIX}resumed=",
        )
    elif event == "SessionEnd":
        # "other" ist u. a. das Beenden durch Signal (Shutdown, Logout): behalten.
        # Alles andere heisst: der Mensch hat die Session verlassen.
        if data.get("reason") == "other":
            return
        if labels_of(terminal).get(f"{PREFIX}session") != session:
            return  # schon von einer neuen Session (/clear, /resume) überschrieben
        hc(
            "label",
            terminal,
            *(f"{PREFIX}{k}=" for k in ("session", "cwd", "started", "args", "resumed")),
        )


def get_terminal(address):
    r = hc("get", address, "--json")
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return None


def cmd_check(addresses):
    """addresses: Kandidaten (vom Mod vorgefiltert); leer = alle live Terminals."""
    resumed, watched = [], 0
    if not addresses:
        r = hc("ls", "--json")
        if r.returncode != 0:
            print(json.dumps({"error": r.stderr.strip(), "resumed": [], "watched": 0}))
            return
        data = json.loads(r.stdout)
        summary = data if isinstance(data, list) else data.get("terminals", [])
        addresses = [t["address"] for t in summary if t.get("address")]
    now = time.time() * 1000
    for address in addresses:
        t = get_terminal(address)
        if not t:
            continue
        labels = t.get("labels") or {}
        session = labels.get(f"{PREFIX}session")
        if not session or t.get("state") != "live":
            continue
        watched += 1
        fg = t.get("foreground") or {}
        shell_pid = t.get("pid")
        # Nur eine nackte Shell ohne Vordergrundprogramm.
        if not shell_pid or fg.get("pid") != shell_pid or fg.get("name") not in SHELLS:
            continue
        if labels.get(f"{PREFIX}resumed") == str(shell_pid):
            continue  # in dieser Shell schon versucht
        shell_start = proc_start_ms(shell_pid)
        try:
            claude_start = int(labels.get(f"{PREFIX}started", "0"))
        except ValueError:
            claude_start = 0
        if shell_start is None or shell_start <= claude_start:
            continue  # Claude lief in genau dieser Shell und wurde dort beendet
        if now - shell_start < MIN_SHELL_AGE_S * 1000:
            continue  # Shell startet noch
        cwd = labels.get(f"{PREFIX}cwd") or t.get("cwd") or ""
        args = labels.get(f"{PREFIX}args", "")
        cmd = f"claude --resume {shlex.quote(session)}"
        if args:
            cmd += " " + args
        if cwd:
            cmd = f"cd {shlex.quote(cwd)} && {cmd}"
        hc("label", address, f"{PREFIX}resumed={shell_pid}")
        s = hc("send", address, cmd)
        resumed.append(
            {
                "address": address,
                "where": labels.get("where") or cwd,
                "session": session,
                "ok": s.returncode == 0,
                "error": s.stderr.strip() if s.returncode else None,
            }
        )
    print(json.dumps({"resumed": resumed, "watched": watched}))


def cmd_adopt():
    """Schon laufende Claude-Instanzen (vor dem Hook gestartet) nachträglich erfassen."""
    adopted = []
    r = hc("ls", "--json")
    if r.returncode != 0:
        print(json.dumps({"error": r.stderr.strip(), "adopted": []}))
        return
    data = json.loads(r.stdout)
    summary = data if isinstance(data, list) else data.get("terminals", [])
    sessions_dir = os.path.expanduser("~/.claude/sessions")
    for item in summary:
        address = item.get("address")
        if not address or item.get("state") != "live":
            continue
        t = get_terminal(address)
        if not t or (t.get("labels") or {}).get(f"{PREFIX}session"):
            continue
        fg = t.get("foreground") or {}
        if fg.get("name") != "claude" or not fg.get("pid"):
            continue
        pid = fg["pid"]
        try:
            with open(os.path.join(sessions_dir, f"{pid}.json")) as f:
                info = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        session = info.get("sessionId")
        if not session:
            continue
        hc(
            "label",
            address,
            f"{PREFIX}session={session}",
            f"{PREFIX}cwd={info.get('cwd') or fg.get('cwd') or ''}",
            f"{PREFIX}started={proc_start_ms(pid) or int(time.time() * 1000)}",
            f"{PREFIX}args={shlex.join(resume_flags(proc_cmdline(pid)))}",
        )
        adopted.append({"address": address, "session": session})
    print(json.dumps({"adopted": adopted}))


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "hook":
        try:
            cmd_hook()
        except Exception as e:  # ein Hook darf Claude nie stören
            print(f"claude-resume: {e}", file=sys.stderr)
    elif cmd == "adopt":
        cmd_adopt()
    elif cmd == "check":
        cmd_check(sys.argv[2:])
    else:
        print("usage: claude-resume.py hook|adopt|check [terminal...]", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
