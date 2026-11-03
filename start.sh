#!/usr/bin/env bash
# Start the task manager: wire the project, sort out the port, serve the board.
#
#     ./.task-manager/start.sh              # foreground; Ctrl-C stops the board
#     ./.task-manager/start.sh --no-open    # extra args pass through to board.py
#
# Port logic (BOARD_PORT from env, else manager/local/.env, else 26071):
#   - our own board already answering there  -> just open the browser
#   - port free                              -> start on it
#   - held by something else                 -> wait a few seconds for it to
#     clear (a restart races its own predecessor's shutdown far more often
#     than a stranger takes the port), and only then take the next free port
#     AND persist it to manager/local/.env, so the hooks and agents (which
#     read the same file) follow the board instead of reporting into the void.
#
# The probe binds exactly as the board does — 127.0.0.1 with SO_REUSEADDR,
# which is what ThreadingHTTPServer sets — because a probe stricter than the
# server lies: a socket the just-stopped board left in TIME_WAIT would read
# as occupied and hop the board off a port its user deliberately pinned.
set -euo pipefail

TM="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MANAGER="$TM/manager"
CORE="$MANAGER/core"
ENV_FILE="$MANAGER/local/.env"

# Seconds to let a held port clear before walking off it. Shutdown is quick;
# this is grace for a predecessor, not patience for a squatter. Anything
# that isn't a number falls back to the default rather than failing a start
# on an arithmetic comparison.
busy_wait="${BOARD_PORT_WAIT:-5}"
case "$busy_wait" in ''|*[!0-9]*) busy_wait=5 ;; esac

# Who this install is: the tasks directory it serves, the application it
# manages, and the tool it is. The work state can live outside the tool
# entirely (task 56), so "$TM/tasks" is no longer the answer on its own —
# and two boards can share one work state while managing different
# applications, which is exactly the pair that must not adopt or stop one
# another. Printed as three tab-separated fields; a stripped install tree
# with no config.py falls back to the tasks path alone, which is all that
# layout ever meant.
board_identity() {
  python3 - "$CORE" "$TM/tasks" "$TM" <<'RESOLVE'
import sys
sys.path.insert(0, sys.argv[1])
try:
    import config
    print("\t".join([str(config.TASKS), str(config.REPO), str(config.TM_ROOT)]))
except Exception:
    print("\t".join([sys.argv[2], "", sys.argv[3]]))
RESOLVE
}

# Every reason the configured layout cannot be served. Empty is fine, and
# so is "config is not importable here" — that is a stripped tree, not a
# broken layout.
layout_problems() {
  python3 - "$CORE" <<'RESOLVE'
import sys
sys.path.insert(0, sys.argv[1])
try:
    import config
except Exception:
    raise SystemExit(0)
for problem in config.problems():
    print(problem)
RESOLVE
}

port="${BOARD_PORT:-}"
if [ -z "$port" ] && [ -f "$ENV_FILE" ]; then
  port="$(sed -n 's/^[[:space:]]*BOARD_PORT[[:space:]]*=[[:space:]]*//p' "$ENV_FILE" | tail -1 | tr -d "'\"")"
fi
port="${port:-26071}"

# Idempotent project wiring; a project without .claude/ still gets the board,
# so a failed wire is tolerated on purpose.
python3 "$TM/install.py" || true
echo

# A failed wire is not the same thing as a layout that cannot be served,
# and `|| true` above would have swallowed both. This is the fail-closed
# half: asked after the wire, because that run may have just written the
# layout, and asked before the port dance, because hopping ports and
# launching a board that will refuse to serve is a worse way to be told.
problems="$(layout_problems)"
if [ -n "$problems" ]; then
  echo "This board cannot serve the layout it is configured with:" >&2
  printf '  - %s\n' "$problems" >&2
  echo >&2
  echo "Settings live in $ENV_FILE." >&2
  exit 2
fi

IDENTITY="$(board_identity)"
TASKS_ROOT="$(printf '%s' "$IDENTITY" | cut -f1)"
APP_ROOT="$(printf '%s' "$IDENTITY" | cut -f2)"
TOOL_ROOT="$(printf '%s' "$IDENTITY" | cut -f3)"

is_free() {
  python3 - "$1" <<'PY'
import socket, sys
s = socket.socket()
# The board's ThreadingHTTPServer sets this; a probe without it would call a
# TIME_WAIT remnant "busy" on a port the server itself could bind.
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    s.bind(("127.0.0.1", int(sys.argv[1])))
except OSError:
    sys.exit(1)
finally:
    s.close()
PY
}

is_our_board() {
  # All three identities, not just the tasks root: two operators can share
  # one work state while managing different applications, and a preview
  # install of the same board is a different tool root. Either of those
  # adopted as "ours" would reopen — or, in stop.sh, kill — somebody
  # else's board. A field the server does not report (an older board) is
  # not compared rather than assumed to differ.
  python3 -c '
import json, sys, urllib.request
try:
    with urllib.request.urlopen("http://127.0.0.1:%s/api/state" % sys.argv[1], timeout=2) as r:
        data = json.load(r)
except Exception:
    sys.exit(1)
layout = data.get("layout") or {}
pairs = [(data.get("board", {}).get("root"), sys.argv[2]),
         (layout.get("app"), sys.argv[3]),
         (layout.get("tool"), sys.argv[4])]
for served, wanted in pairs:
    if wanted and served is not None and served != wanted:
        sys.exit(1)
sys.exit(0 if data.get("board", {}).get("root") == sys.argv[2] else 1)
' "$1" "$TASKS_ROOT" "$APP_ROOT" "$TOOL_ROOT"
}

open_board() {
  echo "Board already running at http://127.0.0.1:$1/ — opening it."
  python3 -m webbrowser -t "http://127.0.0.1:$1/" >/dev/null
}

if is_our_board "$port"; then
  open_board "$port"
  exit 0
fi

if ! is_free "$port"; then
  # Held by someone. Give it a moment: the usual holder is the board this
  # start is replacing, and it lets go within a second or two.
  echo "Port $port is busy — waiting up to ${busy_wait}s for it to clear."
  waited=0
  while [ "$waited" -lt "$busy_wait" ] && ! is_free "$port"; do
    sleep 1
    waited=$((waited + 1))
    if is_our_board "$port"; then   # a board came up during the wait
      open_board "$port"
      exit 0
    fi
  done
fi

if ! is_free "$port"; then
  original=$port
  for offset in $(seq 1 20); do
    candidate=$((original + offset))
    if is_free "$candidate"; then port=$candidate; break; fi
  done
  if [ "$port" = "$original" ]; then
    echo "error: ports $original-$((original + 20)) all busy — set BOARD_PORT yourself." >&2
    exit 1
  fi
  echo "Port $original is held by another process — using $port instead."
  echo "Rewriting BOARD_PORT in manager/local/.env: $original → $port, so the hooks and agents follow the live board."
  echo "To reclaim $original: free it, then set BOARD_PORT=$original in manager/local/.env."
  python3 - "$ENV_FILE" "$port" <<'PY'
import pathlib, sys
path, port = pathlib.Path(sys.argv[1]), sys.argv[2]
lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
out, replaced = [], False
for line in lines:
    if line.strip().startswith("BOARD_PORT"):
        out.append(f"BOARD_PORT={port}")
        replaced = True
    else:
        out.append(line)
if not replaced:
    out.append(f"BOARD_PORT={port}")
path.write_text("\n".join(out) + "\n", encoding="utf-8")
PY
fi

exec python3 "$CORE/board.py" --port "$port" "$@"
