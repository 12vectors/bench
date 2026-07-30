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

# manager/ — the same distance up from core/adapters/claude/ as from a
# local/adapters/claude/ override, so both copies resolve the same files.
MANAGER = Path(__file__).resolve().parents[3]


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


def check_defs():
    """The project's definition-of-done checks: `<label>: <command regex>`
    per line, local/checks replacing core/checks wholesale — the same file
    the board serves to the Focus panel, read here for classification so
    the two never drift. (Core's config.py mirrors this parser; the bridge
    stays standalone.) Read fresh per event; must never raise."""
    for base in (MANAGER / "local", MANAGER / "core"):
        try:
            text = (base / "checks").read_text(encoding="utf-8")
        except OSError:
            continue
        defs = []
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            label, sep, pattern = line.partition(":")
            label, pattern = label.strip(), pattern.strip()
            if not sep or not label or not pattern:
                continue
            try:
                defs.append((label, re.compile(pattern)))
            except re.error:
                continue
        return defs
    return []


def judge(out):
    """Generic pass/fail from a check's output — the hook payload carries
    no exit status (stdout/stderr/interrupted only), so judgment rests on
    the summaries test tools print: counted results ('3 passed',
    '1 failed', '2 errors'), lint-style broken totals, and OK/FAILED
    verdict lines. Returns (ok, summary bit); (None, '') when the output
    says nothing recognizable either way."""
    failed = re.search(r"\b\d+ (?:failed|errors?)\b", out)
    passed = re.search(r"\b\d+ passed\b", out)
    if failed:
        return False, failed.group(0) + (f", {passed.group(0)}" if passed else "")
    if passed:
        return True, passed.group(0)
    broken = re.search(r"\b(\d+) broken\b", out)
    if broken:
        return broken.group(1) == "0", broken.group(0)
    verdict = re.search(r"^(OK|FAILED)\b.*", out, re.M)
    if verdict:
        return verdict.group(1) == "OK", verdict.group(0)[:60]
    return None, ""


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
        kind, ok, label = "command", None, None
        for name, pattern in check_defs():
            if pattern.search(cmd):
                kind, label = "check", name
                break
        if kind == "command" and re.match(r"\s*git (commit|add|push|checkout|switch|merge|worktree)", cmd):
            kind = "git"

        if running:
            summary = f"running: {cmd[:90]}"
        elif kind == "check":
            ok, bits = judge(out)
            summary = f"{label} — {bits}" if bits else f"ran: {cmd[:90]}"
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
        env_file = MANAGER / "local" / ".env"
        for line in env_file.read_text().splitlines():
            key, _, value = line.strip().partition("=")
            if key.strip() == "BOARD_PORT":
                port = value.strip().strip("'\"")
    except Exception:
        pass
    return port or "26071"


def main():
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


if __name__ == "__main__":
    main()
