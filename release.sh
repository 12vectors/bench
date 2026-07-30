#!/usr/bin/env bash
# Build and publish a bench release from this repo.
#
#     ./release.sh                                # tag v<VERSION> and publish
#     ./release.sh --tarball <out> [--source o/r] # build the artifact only
#
# The artifact is built from manager/core/release-manifest — a curated
# tarball that never contained bench's own cards, local/ content, state,
# tests or .claude/ in the first place. Its contents sit at the tarball
# root and the asset name is stable (bench.tar.gz): both are what the
# README's tokenless releases/latest/download/ one-liner depends on.
#
# Publishing refuses on a dirty tree and on an already-existing tag, so
# tag v<VERSION> always names exactly one committed tree. Notes default
# to the core version; set BENCH_RELEASE_NOTES to say more.
#
# This script is bench-repo tooling — the manifest deliberately leaves it
# out of the artifact, so installs never carry it.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MANIFEST="$ROOT/manager/core/release-manifest"
ASSET="bench.tar.gz"

# The starter manager/local/ notes a fresh install unpacks — bench's own
# repo has its real ones, so these are generated, never copied. AGENTS.md
# is the content; CLAUDE.md is the compatibility pointer, mirroring the
# root pair (task 13).
seed_local_agents_md() {
  cat <<'MD'
# Project-specific workflow notes

This file is yours — updates never touch manager/local/. Put here what an
agent or teammate needs that the core doc cannot know: post-merge chores,
what the driver assumes, what each local command is for.

Settings live in manager/local/.env (gitignored); every option and its
default is documented in manager/core/.env.example. A `checks` file here
replaces core/checks as the Focus view's definition-of-done panel.
MD
}

seed_local_claude_md() {
  cat <<'MD'
<!-- Compatibility pointer, load-bearing: the project notes live in AGENTS.md
(the vendor-neutral name all coding agents read); this file makes Claude Code
CLIs without native AGENTS.md support load it via the import below. -->

@AGENTS.md
MD
}

# origin's URL as "owner/repo" — what gets stamped into the shipped
# update.sh so a fresh install can update with zero configuration.
source_repo() {
  local url
  url="$(git -C "$ROOT" remote get-url origin 2>/dev/null)" || return 1
  url="${url#ssh://}"
  url="${url#git@github.com:}"
  url="${url#https://github.com/}"
  url="${url#http://github.com/}"
  url="${url#github.com/}"
  url="${url%.git}"
  url="${url%/}"
  [ -n "$url" ] || return 1
  printf '%s\n' "$url"
}

build_tarball() { # build_tarball <out.tar.gz> <source-repo-or-empty>
  local out="$1" source="$2" stage kind path
  stage="$(mktemp -d)"
  # shellcheck disable=SC2064 — expand $stage now, it is gone by EXIT
  trap "rm -rf '$stage'" RETURN

  while read -r kind path _; do
    case "$kind" in
      copy|once)
        [ -f "$ROOT/$path" ] || { echo "manifest names a missing file: $path" >&2; return 1; }
        mkdir -p "$stage/$(dirname "$path")"
        cp "$ROOT/$path" "$stage/$path"
        ;;
      tree)
        [ -d "$ROOT/$path" ] || { echo "manifest names a missing directory: $path" >&2; return 1; }
        mkdir -p "$stage/$path"
        rsync -a --exclude='__pycache__/' --exclude='.DS_Store' \
          "$ROOT/$path/" "$stage/$path/"
        ;;
      keep)
        mkdir -p "$stage/$path"
        touch "$stage/$path/.gitkeep"
        ;;
      seed)
        case "$path" in
          manager/local/AGENTS.md)
            mkdir -p "$stage/$(dirname "$path")"
            seed_local_agents_md > "$stage/$path"
            ;;
          manager/local/CLAUDE.md)
            mkdir -p "$stage/$(dirname "$path")"
            seed_local_claude_md > "$stage/$path"
            ;;
          *) echo "manifest seeds a file this script cannot generate: $path" >&2; return 1;;
        esac
        ;;
      ''|'#'*) ;;
      *) echo "manifest has an unknown entry class: $kind $path" >&2; return 1;;
    esac
  done < "$MANIFEST"

  if [ -n "$source" ]; then
    sed "s|^BENCH_SOURCE_DEFAULT=.*|BENCH_SOURCE_DEFAULT=\"$source\"|" \
      "$stage/update.sh" > "$stage/update.sh.tmp"
    mv "$stage/update.sh.tmp" "$stage/update.sh"
  fi
  chmod +x "$stage"/start.sh "$stage"/stop.sh "$stage"/update.sh
  find "$stage/manager/core/adapters" \( -name run -o -name wire \) \
    -exec chmod +x {} + 2>/dev/null || true

  # Contents at the tarball root — extraction into .task-manager/ must
  # not land one directory too deep. COPYFILE_DISABLE keeps macOS tar
  # from smuggling AppleDouble ._* entries into the artifact.
  COPYFILE_DISABLE=1 tar -czf "$out" -C "$stage" .
}

# --tarball mode: just build, no git/gh — for tests and inspection.
if [ "${1:-}" = "--tarball" ]; then
  out="${2:?usage: ./release.sh --tarball <out.tar.gz> [--source owner/repo]}"
  source=""
  [ "${3:-}" = "--source" ] && source="${4:?--source needs owner/repo}"
  [ -n "$source" ] || source="$(source_repo || true)"
  build_tarball "$out" "$source"
  echo "Built $out (source stamp: ${source:-none})."
  exit 0
fi

# Publish mode from here on: clean committed tree, fresh tag, gh.
version="$(cat "$ROOT/manager/core/VERSION")"
tag="v$version"

if [ -n "$(git -C "$ROOT" status --porcelain)" ]; then
  echo "Working tree is dirty — commit (or stash) first; a release must be reproducible from its tag." >&2
  exit 1
fi
if git -C "$ROOT" rev-parse -q --verify "refs/tags/$tag" >/dev/null \
   || [ -n "$(git -C "$ROOT" ls-remote --tags origin "refs/tags/$tag" 2>/dev/null)" ]; then
  echo "Tag $tag already exists — bump manager/core/VERSION before releasing." >&2
  exit 1
fi
source="$(source_repo)" || { echo "No origin remote to publish to." >&2; exit 1; }

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
build_tarball "$tmp/$ASSET" "$source"

git -C "$ROOT" tag "$tag"
git -C "$ROOT" push origin "$tag"
gh release create "$tag" --repo "$source" --verify-tag \
  --title "bench $tag" \
  --notes "${BENCH_RELEASE_NOTES:-bench core version $version}" \
  "$tmp/$ASSET"
echo "Published $tag ($source): asset $ASSET."
