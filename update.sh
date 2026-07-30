#!/usr/bin/env bash
# Update the task manager from its published releases.
#
#     ./.task-manager/update.sh              # latest release
#     BENCH_REF=v3 ./.task-manager/update.sh # an exact release tag
#
# Downloads the bench.tar.gz asset of a GitHub Release — a curated
# artifact that never contained the distribution's own cards or local/
# content — then replaces manager/core/ WHOLESALE plus the top-level
# core-owned files named by the artifact's own manifest. Never touches
# tasks/, plans/, reference/, or manager/local/ — your project's tasks,
# driver, adapters, prompt overrides, .env and state survive every update.
#
# Source repo: BENCH_SOURCE (environment, then manager/local/.env),
# falling back to the default below. No published release → this script
# says so and changes nothing; there is no git fallback. Developers
# working on bench itself should clone the repo and pull instead.
set -euo pipefail
{

# Stamped by release.sh at build time with the repo the artifact was
# built from ("owner/repo"), so a fresh install updates with zero
# configuration. Empty in a git checkout — set BENCH_SOURCE to override.
BENCH_SOURCE_DEFAULT=""

ASSET="bench.tar.gz"
TM="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$TM/manager/local/.env"

read_env() { # read_env KEY — last value in local/.env, quotes stripped
  [ -f "$ENV_FILE" ] || return 0
  sed -n "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*//p" "$ENV_FILE" \
    | tail -1 | tr -d "'\"" || true
}

src="${BENCH_SOURCE:-$(read_env BENCH_SOURCE)}"
src="${src:-$BENCH_SOURCE_DEFAULT}"
if [ -z "$src" ]; then
  echo "No release source known: this update.sh carries no build stamp (a git" >&2
  echo "checkout rather than a release?) and BENCH_SOURCE is not set. Either" >&2
  echo "set BENCH_SOURCE=<owner/repo> in manager/local/.env, or — if you are" >&2
  echo "developing bench itself — update the clone with git instead." >&2
  exit 1
fi

# Normalize any GitHub remote spelling to "owner/repo" — releases are a
# GitHub mechanism, so that is the one shape both gh and curl need.
repo="$src"
repo="${repo#ssh://}"
repo="${repo#git@github.com:}"
repo="${repo#https://github.com/}"
repo="${repo#http://github.com/}"
repo="${repo#github.com/}"
repo="${repo%.git}"
repo="${repo%/}"
case "$repo" in
  *://*|*@*|*:*)
    echo "BENCH_SOURCE=$src is not a GitHub repo — releases need an owner/repo on github.com." >&2
    exit 1;;
  */*) ;;
  *)
    echo "BENCH_SOURCE=$src is not a GitHub repo — expected the owner/repo form." >&2
    exit 1;;
esac

ref="${BENCH_REF:-}"
gh_bin="${BOARD_GH_BIN:-$(read_env BOARD_GH_BIN)}"
gh_bin="${gh_bin:-gh}"

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
asset="$tmp/$ASSET"
tag=""

# The tokenless path: GitHub redirects releases/latest to the tag page,
# which is how the latest tag is learned without the API's rate limits.
latest_tag_curl() {
  local final
  final="$(curl -fsSLo /dev/null -w '%{url_effective}' \
    "https://github.com/$repo/releases/latest" 2>/dev/null)" || return 1
  case "$final" in
    */releases/tag/*) printf '%s\n' "${final##*/releases/tag/}";;
    *) return 1;;
  esac
}

fetch_release() { # sets $tag and downloads $asset; 1 = nothing published
  if command -v "$gh_bin" >/dev/null 2>&1; then
    if [ -n "$ref" ]; then tag="$ref"; else
      tag="$("$gh_bin" release view --repo "$repo" --json tagName \
        --jq .tagName 2>/dev/null || true)"
    fi
    if [ -n "$tag" ] && "$gh_bin" release download "$tag" --repo "$repo" \
        --pattern "$ASSET" --output "$asset" --clobber 2>/dev/null; then
      return 0
    fi
  fi
  tag=""
  if command -v curl >/dev/null 2>&1; then
    if [ -n "$ref" ]; then tag="$ref"; else
      tag="$(latest_tag_curl || true)"
    fi
    if [ -n "$tag" ] && curl -fsSL -o "$asset" \
        "https://github.com/$repo/releases/download/$tag/$ASSET" 2>/dev/null; then
      return 0
    fi
  fi
  return 1
}

echo "Fetching ${ref:-the latest release} of ${repo}…"
if ! fetch_release; then
  echo "No published release found for $repo${ref:+ at tag $ref}." >&2
  echo "The repo has no release yet (or none reachable from here — private" >&2
  echo "repos need gh auth). Ask its maintainer to run ./release.sh; if you" >&2
  echo "are developing bench itself, clone the repo and use git instead." >&2
  echo "Nothing was changed." >&2
  exit 1
fi

mkdir "$tmp/dist"
# Nothing in the asset may name a path outside its own root — tar
# implementations differ on how much of that they refuse themselves.
if tar -tzf "$asset" | grep -E '^/|(^|/)\.\.(/|$)' >/dev/null; then
  echo "The $tag asset contains paths that escape its own root. Refusing to install it; nothing was changed." >&2
  exit 1
fi
tar -xzf "$asset" -C "$tmp/dist"
dist="$tmp/dist"
manifest="$dist/manager/core/release-manifest"
if [ ! -d "$dist/manager/core" ] || [ ! -f "$manifest" ]; then
  echo "The $tag asset does not look like a bench release (no manager/core/release-manifest). Nothing was changed." >&2
  exit 1
fi

# Tag and contents must agree — a mismatch means a mislabeled asset, and
# installing it would leave a version number that lies about the code.
artifact_version="$(cat "$dist/manager/core/VERSION" 2>/dev/null || echo '?')"
if [ "v$artifact_version" != "$tag" ]; then
  echo "Release $tag contains core VERSION $artifact_version — the asset disagrees with its tag. Refusing to install it; nothing was changed." >&2
  exit 1
fi

# The manifest's copy paths land under $TM verbatim, so none may reach
# outside it — checked before anything is replaced, leaving a poisoned
# manifest no partial update to hide behind.
while read -r kind path _; do
  [ "$kind" = "copy" ] || continue
  case "/$path/" in *//*|*/../*)
    echo "Release $tag manifest names an unsafe path: $path. Refusing to install it; nothing was changed." >&2
    exit 1;;
  esac
done < "$manifest"

before="$(cat "$TM/manager/core/VERSION" 2>/dev/null || echo '?')"
rsync -a --delete "$dist/manager/core/" "$TM/manager/core/"
# The artifact's manifest names the top-level core-owned files (class
# `copy`) — read the new list, so a release adding a script updates it.
while read -r kind path _; do
  [ "$kind" = "copy" ] || continue
  [ -f "$dist/$path" ] || continue
  mkdir -p "$TM/$(dirname "$path")"
  cp "$dist/$path" "$TM/$path"
done < "$manifest"
chmod +x "$TM"/start.sh "$TM"/stop.sh "$TM"/update.sh "$TM"/install.py 2>/dev/null || true
find "$TM/manager/core/adapters" -name run -o -name wire | xargs chmod +x 2>/dev/null || true

after="$(cat "$TM/manager/core/VERSION" 2>/dev/null || echo '?')"
echo "Updated core from $tag: version $before → $after."
echo "Now run: python3 $TM/install.py   (re-wires the project; idempotent)"
echo "Then restart the board: $TM/stop.sh && $TM/start.sh"
exit 0
}
