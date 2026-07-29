#!/usr/bin/env python3
"""Claude Code hook → board bridge (the Claude adapter's event edge).

Registered by this adapter's `wire` for SessionStart, PreToolUse(Bash),
PostToolUse, Stop and SessionEnd. Reads Claude's raw hook payload from
stdin, translates it into the board's NORMALIZED event schema — the fixed
contract every adapter speaks — and POSTs it to /api/events.

Normalized event (v1):
    {"v": 1, "session": str, "kind": str, "summary": str,
     "file"?: str, "cmd"?: str, "detail"?: str, "ok"?: bool,
     "running"?: bool, "agent"?: str, "task"?: str}
kinds: session end idle edit read search command test check git plan
       subagent web other

Fails silently and fast: a session must never slow down or break because
the board isn't running.
"""
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}


def _txt(value, cap=600):
    return value[:cap] if isinstance(value, str) else ""


def _relpath(path):
    if not isinstance(path, str):
        return ""
    path = re.sub(r"^.*?/\.worktrees/[^/]+/", "", path)
    root = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    if path.startswith(root):
        path = path[len(root):].lstrip("/")
    return path


def _resp_text(resp):
    if isinstance(resp, str):
        return resp
    if isinstance(resp, dict):
        return "\n".join(str(resp[k]) for k in ("stdout", "stderr") if resp.get(k))
    if isinstance(resp, list):
        return "\n".join(i["text"] for i in resp
                         if isinstance(i, dict) and isinstance(i.get("text"), str))
    return ""


def classify(hook, tool, tool_input, resp):
    if hook == "SessionStart":
        return {"kind": "session", "summary": "session started"}
    if hook == "SessionEnd":
        return {"kind": "end", "summary": "session ended"}
    if hook == "Stop":
        return {"kind": "idle", "summary": "finished responding — idle"}

    if tool in EDIT_TOOLS:
        f = _relpath(tool_input.get("file_path"))
        return {"kind": "edit", "file": f, "summary": f"edited {f or '?'}"}
    if tool == "Read":
        f = _relpath(tool_input.get("file_path"))
        return {"kind": "read", "file": f, "summary": f"read {f or '?'}"}
    if tool in ("Glob", "Grep"):
        return {"kind": "search", "summary": f"searched {_txt(tool_input.get('pattern'), 60) or '…'}"}
    if tool == "TodoWrite":
        todos = tool_input.get("todos") or []
        done = sum(1 for t in todos if t.get("status") == "completed")
        doing = [t.get("content", "") for t in todos if t.get("status") == "in_progress"]
        summary = f"plan: {done}/{len(todos)} done"
        if doing:
            summary += f" — now: {_txt(doing[0], 70)}"
        detail = "\n".join(
            f"[{'x' if t.get('status') == 'completed' else '>' if t.get('status') == 'in_progress' else ' '}] "
            + _txt(t.get("content", ""), 120) for t in todos)
        return {"kind": "plan", "summary": summary, "detail": detail}
    if tool == "Task":
        return {"kind": "subagent", "summary": f"subagent: {_txt(tool_input.get('description'), 60)}"}
    if tool in ("WebFetch", "WebSearch"):
        target = tool_input.get("url") or tool_input.get("query")
        return {"kind": "web", "summary": f"{tool.lower()}: {_txt(target, 60)}"}

    if tool == "Bash":
        cmd = _txt(tool_input.get("command"), 240)
        running = hook == "PreToolUse"
        out = "" if running else _resp_text(resp)[:1200]
        kind, ok = "command", None
        if re.search(r"\bpytest\b", cmd):
            kind = "test"
        elif "lint-imports" in cmd or re.search(r"type-check|vue-tsc|\bnpm (run )?test\b|\bvitest\b", cmd):
            kind = "check"
        elif re.match(r"\s*git (commit|add|push|checkout|switch|merge|worktree)", cmd):
            kind = "git"

        if running:
            summary = f"running: {cmd[:90]}"
        elif kind == "test":
            passed = re.search(r"(\d+) passed", out)
            failed = re.search(r"(\d+) failed", out) or re.search(r"(\d+) error", out)
            if failed:
                ok = False
                summary = f"pytest — {failed.group(0)}" + (f", {passed.group(0)}" if passed else "")
            elif passed:
                ok = True
                summary = f"pytest — {passed.group(0)}"
            else:
                summary = f"ran: {cmd[:90]}"
        elif kind == "check":
            if "broken" in out:
                ok = not re.search(r"[1-9]\d* broken", out)
            summary = f"ran: {cmd[:90]}"
        elif kind == "git":
            m = re.search(r"""-m ["']([^"']{1,90})""", cmd)
            summary = f"git: {m.group(1) if m else cmd[:80]}"
        else:
            summary = f"ran: {cmd[:90]}"
        return {"kind": kind, "running": running, "ok": ok, "summary": summary,
                "cmd": cmd, "detail": out[:900]}

    return {"kind": "other", "summary": tool or hook or "event"}


def board_port():
    port = os.environ.get("BOARD_PORT")
    if port:
        return port
    try:
        env_file = Path(__file__).resolve().parents[3] / "local" / ".env"
        for line in env_file.read_text().splitlines():
            key, _, value = line.strip().partition("=")
            if key.strip() == "BOARD_PORT":
                port = value.strip().strip("'\"")
    except Exception:
        pass
    return port or "26071"


try:
    payload = json.load(sys.stdin)
except Exception:
    payload = {}

tool_input = payload.get("tool_input")
event = {
    "v": 1,
    "session": payload.get("session_id") or "unknown",
    "agent": os.environ.get("BOARD_AGENT_ID"),
    "task": os.environ.get("BOARD_TASK"),
    **classify(payload.get("hook_event_name") or "?",
               payload.get("tool_name") or "",
               tool_input if isinstance(tool_input, dict) else {},
               payload.get("tool_response")),
}

try:
    request = urllib.request.Request(
        f"http://127.0.0.1:{board_port()}/api/events",
        data=json.dumps(event).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    urllib.request.urlopen(request, timeout=1).read()
except Exception:
    pass

sys.exit(0)
