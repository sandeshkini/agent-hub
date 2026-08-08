#!/usr/bin/env bash
# build.sh — build the Agent-Hub OWUI fork: pinned upstream + patches/ -> image.
# Usage:
#   ./build.sh            # reset upstream, apply patches, docker build the fork image
#   ./build.sh --check    # only verify all patches apply cleanly (no build)
#   ./build.sh --clone    # (re)clone upstream at the pinned tag, then build
set -euo pipefail
cd "$(dirname "$0")"

TAG="v0.11.0"                                   # PINNED upstream (matches the running image)
IMAGE="agent-hub/open-webui:${TAG}-fork"
UP="upstream"
REPO="https://github.com/open-webui/open-webui.git"

if [ "${1:-}" = "--clone" ] || [ ! -d "$UP/.git" ]; then
  echo "== clone $REPO @ $TAG =="
  rm -rf "$UP"; git clone --depth 1 --branch "$TAG" "$REPO" "$UP"
  [ "${1:-}" = "--clone" ] && shift || true
fi

echo "== reset upstream to clean $TAG =="
# `git checkout -- .` + `clean -fd` is NOT enough: neither touches the INDEX. Our patches stage new
# files (git apply --3way), and a failed apply leaves unmerged (UU) entries, so both survive and the
# next apply lands on an already-patched tree -> guaranteed conflict. The first build worked and every
# rebuild after it failed. reset --hard clears index + tracked files; clean -fd drops the rest.
git -C "$UP" reset --hard HEAD >/dev/null 2>&1 || true
git -C "$UP" clean -fd >/dev/null 2>&1 || true  # drop untracked; keep node_modules cache if present

echo "== apply patches/ (in order) =="
shopt -s nullglob
patches=(patches/*.patch)
if [ ${#patches[@]} -eq 0 ]; then
  echo "  (no patches — building vanilla $TAG for parity/de-risk)"
else
  for p in "${patches[@]}"; do
    echo "  applying $p"
    git -C "$UP" apply --3way "../$p" || { echo "!! PATCH FAILED: $p"; exit 1; }
  done
fi

if [ "${1:-}" = "--check" ]; then echo "OK: all patches apply cleanly."; exit 0; fi

echo "== docker build $IMAGE =="
docker build -t "$IMAGE" "$UP"
echo "== built $IMAGE =="
