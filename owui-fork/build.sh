#!/usr/bin/env bash
# build.sh — build the Agent-Hub OWUI fork: pinned upstream + patches/ -> image.
#
# ⚠️  AI/DEV CONTRACT (see ../CLAUDE.md §2 + gotchas #1–#3):
#   • This script does `git reset --hard` on upstream/ and re-applies patches/*.patch. Any edit you
#     made under upstream/ that is NOT yet captured in the patch WILL BE WIPED. So BEFORE running this,
#     regenerate the patch:
#         git -C upstream add -A && git -C upstream diff --cached > patches/0001-terminal-page.patch && git -C upstream reset -q
#   • Run ONLY ONE build at a time (no double-backgrounding). Two concurrent builds race on upstream/.
#   • Docker's COPY cache has shipped STALE images — the self-heal block at the end sha1-checks a
#     sentinel file in the image and rebuilds --no-cache if it drifted. Keep it. Then verify in the
#     running container: `docker exec open-webui sh -c "grep -rl '<your change>' /app/build"`.
#
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

# GUARD: docker's COPY layer cache has been observed serving a STALE copy of the source — a fork file's
# new content silently didn't land in the image, shipping an old build that looked successful. Verify a
# fork-added file inside the image matches the freshly-patched tree; if not, rebuild without cache.
SENTINEL="backend/open_webui/routers/agent_nodes.py"
want="$(sha1sum "$UP/$SENTINEL" 2>/dev/null | cut -d' ' -f1)"
have="$(docker run --rm --entrypoint sh "$IMAGE" -c "sha1sum /app/$SENTINEL 2>/dev/null | cut -d' ' -f1" 2>/dev/null)"
if [ -n "$want" ] && [ "$want" != "$have" ]; then
  echo "!! image source is STALE (docker COPY cache served old content) — rebuilding --no-cache"
  docker build --no-cache -t "$IMAGE" "$UP"
fi
echo "== built $IMAGE =="
