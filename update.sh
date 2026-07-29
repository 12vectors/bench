#!/usr/bin/env bash
# Update the task manager from its distribution repo.
#
#     ./.task-manager/update.sh
#
# Replaces manager/core/ WHOLESALE plus the top-level core-owned files
# (CLAUDE.md, install.py, start.sh, stop.sh, update.sh). Never touches
# tasks/, plans/, reference/, or manager/local/ — your project's tasks,
# driver, adapters, prompt overrides, .env and state survive every update.
#
# Source repo: BENCH_SOURCE in manager/local/.env (a git URL).
set -euo pipefail

TM="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$TM/manager/local/.env"

src="${BENCH_SOURCE:-}"
if [ -z "$src" ] && [ -f "$ENV_FILE" ]; then
  src="$(sed -n 's/^[[:space:]]*BENCH_SOURCE[[:space:]]*=[[:space:]]*//p' "$ENV_FILE" | tail -1 | tr -d "'\"")"
fi
if [ -z "$src" ]; then
  echo "No source repo configured — set BENCH_SOURCE=<git url> in manager/local/.env" >&2
  exit 1
fi
# A specific release: BENCH_REF=v3 ./update.sh (any tag or branch; default = latest main)
ref="${BENCH_REF:-}"

before="$(cat "$TM/manager/core/VERSION" 2>/dev/null || echo '?')"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
echo "Fetching $src ${ref:+(ref $ref) }…"
git clone --quiet --depth 1 ${ref:+--branch "$ref"} "$src" "$tmp/dist"

if [ ! -d "$tmp/dist/manager/core" ]; then
  echo "That repo does not look like a task-manager distribution (no manager/core/)." >&2
  exit 1
fi

rsync -a --delete "$tmp/dist/manager/core/" "$TM/manager/core/"
for f in CLAUDE.md README.md install.py start.sh stop.sh update.sh; do
  [ -f "$tmp/dist/$f" ] && cp "$tmp/dist/$f" "$TM/$f"
done
chmod +x "$TM"/start.sh "$TM"/stop.sh "$TM"/update.sh 2>/dev/null || true
find "$TM/manager/core/adapters" -name run -o -name wire | xargs chmod +x 2>/dev/null || true

after="$(cat "$TM/manager/core/VERSION" 2>/dev/null || echo '?')"
echo "Updated core: version $before → $after."
echo "Now run: python3 $TM/install.py   (re-wires the project; idempotent)"
echo "Then restart the board: $TM/stop.sh && $TM/start.sh"
