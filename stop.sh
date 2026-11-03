#!/usr/bin/env bash
# Stop the task manager board.
#
#     ./.task-manager/stop.sh            # refuses while agents are running
#     ./.task-manager/stop.sh --force    # stop anyway (agents keep running,
#                                        # but the board loses their endings:
#                                        # no auto-move, no PR, no decline)
#
# Only ever stops OUR board: the process is identified by asking the port's
# /api/state for its tasks root — a foreign process on the port is left alone.
set -euo pipefail

TM="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CORE="$TM/manager/core"
ENV_FILE="$TM/manager/local/.env"
FORCE="${1:-}"

# Who this install is: tasks root, application, tool. Same resolution and
# the same fallback as start.sh's — and the same reason for all three:
# stopping is the one place where adopting somebody else's board as ours
# does real damage, so a preview install of the same work state, or an
# operator managing a different application, must not match (task 56).
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

port="${BOARD_PORT:-}"
if [ -z "$port" ] && [ -f "$ENV_FILE" ]; then
  port="$(sed -n 's/^[[:space:]]*BOARD_PORT[[:space:]]*=[[:space:]]*//p' "$ENV_FILE" | tail -1 | tr -d "'\"")"
fi
port="${port:-26071}"
IDENTITY="$(board_identity)"
TASKS_ROOT="$(printf '%s' "$IDENTITY" | cut -f1)"
APP_ROOT="$(printf '%s' "$IDENTITY" | cut -f2)"
TOOL_ROOT="$(printf '%s' "$IDENTITY" | cut -f3)"

# One probe answers both questions: is this our board, and are agents running?
probe="$(python3 -c '
import json, sys, urllib.request
import urllib.error
try:
    with urllib.request.urlopen("http://127.0.0.1:%s/api/state" % sys.argv[1], timeout=2) as r:
        data = json.load(r)
except urllib.error.HTTPError:
    print("foreign"); sys.exit(0)   # something HTTP answered, but not our API
except Exception:
    print("none"); sys.exit(0)
layout = data.get("layout") or {}
if data.get("board", {}).get("root") != sys.argv[2]:
    print("foreign"); sys.exit(0)
for served, wanted in ((layout.get("app"), sys.argv[3]),
                       (layout.get("tool"), sys.argv[4])):
    if wanted and served is not None and served != wanted:
        print("foreign"); sys.exit(0)
running = [a for a in data.get("agents", []) if a.get("status") == "running"]
print("ours " + ",".join(
    "%s on %s" % (a.get("name") or "an agent", a.get("task", "?")) for a in running))
' "$port" "$TASKS_ROOT" "$APP_ROOT" "$TOOL_ROOT")"

case "$probe" in
  none)
    if lsof -ti tcp:"$port" >/dev/null 2>&1; then
      echo "Port $port is occupied by something that isn't this project's board — leaving it alone." >&2
      exit 1
    fi
    echo "Nothing answering on port $port — the board is not running."
    exit 0;;
  foreign)
    echo "Port $port is serving something that is not this project's board — leaving it alone." >&2
    exit 1;;
esac

agents="${probe#ours }"
if [ -n "$agents" ] && [ "$FORCE" != "--force" ]; then
  echo "Agents are still working: $agents"
  echo "Stopping now would lose their endings (auto-move, PR, decline handling)."
  echo "Wait for them, hold them from the board, or run: ./.task-manager/stop.sh --force"
  exit 1
fi

pids="$(lsof -ti tcp:"$port" 2>/dev/null || true)"
if [ -z "$pids" ]; then
  echo "Board answered but no local process found on port $port — nothing to do."
  exit 1
fi
kill $pids
for _ in 1 2 3 4 5 6 7 8 9 10; do
  sleep 0.3
  lsof -ti tcp:"$port" >/dev/null 2>&1 || { echo "Board stopped."; exit 0; }
done
echo "Still up after SIGTERM — you may need: kill -9 $pids" >&2
exit 1
