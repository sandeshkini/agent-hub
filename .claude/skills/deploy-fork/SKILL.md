---
name: deploy-fork
description: >-
  Ship / deploy / release a change to the Open WebUI FORK (owui-fork/) in the agent-hub repo.
  Use whenever a change under owui-fork/upstream/ (Svelte in src/, Python in backend/) needs to go
  live on prod (operator.kingdomofluna.com / :3000). Handles the mandatory patch-regen, the
  render-gated build→staging→promote pipeline, in-container verification, and rollback. Do NOT use
  for adapter changes (owui-claude/hermes/opencode — those are plain container rebuilds) or host
  services (owui-terminal, pin — those are systemctl restarts).
---

# Deploy the Open WebUI fork (safely)

Ship fork changes ONLY through the render gate. A broken fork build still returns HTTP 200 but a BLANK
page (Svelte throws at mount), so `curl`/health checks pass and a white screen ships to prod. The gate
renders the candidate in a headless browser on a staging container (`:3001`, own volume) BEFORE prod,
and keeps a `:prev` image for instant rollback.

> **The one rule:** never run `docker compose up -d --force-recreate open-webui` by hand. Use `deploy.sh`.

## Steps

Run everything from the repo root (`~/Documents/apps/agent-hub`).

### 1. Edit the fork source
Files live under `owui-fork/upstream/` — Svelte in `src/`, FastAPI in `backend/`. This tree is a
gitignored pinned checkout; your edits are NOT the source of truth (the patch is — step 2).

### 2. Regenerate the patch  ← do this or your edit is WIPED
`build.sh` does `git reset --hard HEAD` on `upstream/`. Any edit not captured in the patch is lost and
the build silently uses the OLD patch.
```bash
UP=owui-fork/upstream
git -C "$UP" add -A
git -C "$UP" diff --cached > owui-fork/patches/0001-terminal-page.patch
git -C "$UP" reset -q
```

### 3. Ship it through the gate
```bash
./owui-fork/deploy.sh            # build → staging → RENDER-GATE → promote to prod → verify (one shot)
# or, to eyeball staging before prod:
./owui-fork/deploy.sh staging    # build → staging → render-gate → STOP  (review, then:)
./owui-fork/deploy.sh promote    # render-gate staging → promote → verify
```
- Gate failure prints `❌ … PROD UNTOUCHED` — fix, regen patch (step 2), retry. Prod never moved.
- Post-promote prod failure auto-rolls-back to `:prev`.
- Review staging off-box: `ssh -L 3001:localhost:3001 aibo` → `http://localhost:3001/`.

### 4. Verify the change actually landed in the running container
Docker's COPY cache has shipped stale images before. Confirm your change is really in prod:
```bash
docker exec open-webui sh -c "grep -rl '<a string from your change>' /app/build | head"   # frontend
docker exec open-webui sh -c "grep -c '<marker>' /app/backend/open_webui/routers/<file>.py" # backend
```

### 5. Commit + push (only the patch is tracked; upstream/ is gitignored)
```bash
git add -A && git commit -m "…"          # end with the Co-Authored-By: Claude Opus 4.8 trailer
git pull --rebase origin main && git push origin main
```

## If prod ever white-screens
```bash
./owui-fork/deploy.sh rollback           # instant revert to the last-good image (:prev)
```
Then reproduce + fix on staging before promoting again.

## Reference
- Full guide: `owui-fork/DEPLOYING.md`
- Build mechanics + gotchas: `CLAUDE.md` §2–§3
- The gate script: `owui-fork/smoke-test.sh` (exit 0 rendered · 1 blank/crash · 2 no chromium)
- The driver: `owui-fork/deploy.sh` (`deploy|staging|promote|rollback|smoke`)
