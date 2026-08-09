#!/usr/bin/env bash
# deploy.sh — the SAFE way to ship an OWUI fork change. It renders the candidate in a HEADLESS browser
# on a throwaway STAGING container (:3001, own volume) BEFORE it can ever touch prod (:3000). A broken
# build (white screen that still returns HTTP 200) is caught on staging → prod is never disturbed.
#
# WHY THIS EXISTS: `docker compose up -d --force-recreate open-webui` deploys blindly — a component that
# throws during Svelte mount ships a blank app and takes the hub down. This wraps build+deploy in a
# render gate + an instant rollback point. AGENTS: use THIS, never a bare `up --force-recreate open-webui`.
#
#   ./deploy.sh              build → staging → render-gate → (pass) promote to PROD → verify   [one shot]
#   ./deploy.sh staging      build → staging → render-gate → STOP. Review at the staging URL, then `promote`.
#   ./deploy.sh promote      render-gate the CURRENT staging image → promote to PROD → verify (post-review).
#   ./deploy.sh rollback     restore the previous prod image (tag :prev) instantly.
#   ./deploy.sh smoke [url]  just run the render smoke-test (default: prod).
#
# Staging is left RUNNING after a deploy so it doubles as the review URL. Stop it with:
#   docker compose --profile staging stop open-webui-staging
set -uo pipefail
cd "$(dirname "$0")/.."                         # repo root (agent-hub/)
FORK=owui-fork
IMAGE="${OWUI_IMAGE:-agent-hub/open-webui:v0.11.0-fork}"
PREV="agent-hub/open-webui:prev"                # rollback point (last-known-good prod image)
PROD_URL="http://localhost:3000/"
STAGE_URL="http://localhost:${OWUI_STAGING_PORT:-3001}/"
CMD="${1:-deploy}"

say(){ printf '\n\033[1m== %s ==\033[0m\n' "$*"; }

wait_health(){                                   # $1 = container name
  local c="$1" s=
  for _ in $(seq 1 45); do
    s=$(docker inspect "$c" --format '{{.State.Health.Status}}' 2>/dev/null || true)
    [ "$s" = healthy ] && return 0
    sleep 2
  done
  echo "  ($c health=$s — continuing; the render smoke-test is the real gate)"
}

save_prev(){                                     # snapshot the image PROD currently runs → :prev
  local cur; cur=$(docker inspect open-webui --format '{{.Image}}' 2>/dev/null || true)
  if [ -n "$cur" ]; then docker tag "$cur" "$PREV" && echo "  rollback point saved → $PREV"
  else echo "  (no running prod container to snapshot — skipping rollback point)"; fi
}

build(){    say "build candidate image"; ./"$FORK"/build.sh; }
up_staging(){ say "staging up ($STAGE_URL)"; docker compose --profile staging up -d --force-recreate open-webui-staging; wait_health open-webui-staging; }
up_prod(){    say "prod recreate ($PROD_URL)"; docker compose up -d --force-recreate open-webui; wait_health open-webui; }
smoke(){    ./"$FORK"/smoke-test.sh "$1"; }      # exit 0 = rendered, 1 = blank/crash, 2 = no chromium

case "$CMD" in
  smoke)
    smoke "${2:-$PROD_URL}"; exit $? ;;

  rollback)
    docker image inspect "$PREV" >/dev/null 2>&1 || { echo "❌ no $PREV image — nothing to roll back to."; exit 1; }
    say "ROLLING BACK to $PREV"
    docker tag "$PREV" "$IMAGE"; up_prod
    if smoke "$PROD_URL"; then echo "✅ ROLLED BACK — prod renders again."; else echo "⚠️  rolled back but prod still not rendering — investigate."; exit 1; fi ;;

  staging)
    build; up_staging
    if smoke "$STAGE_URL"; then
      echo "✅ STAGING renders. Review it at $STAGE_URL (or staging.operator.kingdomofluna.com),"
      echo "   then ship it with:  ./$FORK/deploy.sh promote"
    else
      echo "❌ candidate does NOT render on staging — PROD UNTOUCHED. Fix the change and retry."; exit 1
    fi ;;

  promote)
    up_staging                                   # ensure staging is running the current candidate image
    smoke "$STAGE_URL" || { echo "❌ staging doesn't render — refusing to promote. Run './deploy.sh staging' first."; exit 1; }
    save_prev; up_prod
    if smoke "$PROD_URL"; then echo "✅ PROMOTED — prod renders. Rollback: ./$FORK/deploy.sh rollback"
    else echo "❌ prod render FAILED after promote — auto-rolling back to $PREV"; docker tag "$PREV" "$IMAGE"; up_prod; exit 1; fi ;;

  deploy|"")
    build; up_staging
    if ! smoke "$STAGE_URL"; then
      echo "❌ candidate render FAILED on staging — PROD UNTOUCHED. Fix the change and retry."; exit 1
    fi
    echo "✓ candidate renders on staging — promoting to prod…"
    save_prev; up_prod
    if smoke "$PROD_URL"; then
      echo "✅ DEPLOYED — prod renders. Instant rollback available: ./$FORK/deploy.sh rollback"
    else
      echo "❌ prod render FAILED — auto-rolling back to $PREV"; docker tag "$PREV" "$IMAGE"; up_prod; exit 1
    fi ;;

  *) echo "usage: deploy.sh [deploy|staging|promote|rollback|smoke <url>]"; exit 2 ;;
esac
