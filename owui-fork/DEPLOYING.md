# DEPLOYING the OWUI fork — the SAFE workflow (read before you ship)

**TL;DR for agents & humans:** never run `docker compose up -d --force-recreate open-webui` by hand.
Ship every fork change with **`./owui-fork/deploy.sh`**. It render-tests the candidate on a throwaway
staging container BEFORE prod, and keeps an instant rollback point.

---

## Why this exists

A broken fork build still returns **HTTP 200** — the FastAPI backend and the SPA shell serve fine, so
`curl`/health checks are green — while the Svelte app throws during mount and the whole page is **blank**
(a "white screen"). We shipped exactly this to prod more than once (e.g. an undeclared `barItems` in
`Sidebar.svelte`). A plain `--force-recreate` deploys that blank straight to `operator.kingdomofluna.com`
and takes the hub down.

The only thing that catches a white screen is **actually rendering the page in a browser**. That's what
this pipeline does, on staging, before prod is ever touched.

## The two safety layers

1. **Render gate** — `owui-fork/smoke-test.sh <url>` loads the page in **headless chromium**, lets the JS
   run, dumps the post-render DOM, and asserts the app actually mounted (sidebar markers present, size
   sane). Exit `0` rendered · `1` blank/crash · `2` no chromium. Headless = no X display, so it does **not**
   disturb `cua-driver` on `:1`.
2. **Staging canary** — `open-webui-staging` (compose profile `staging`, `127.0.0.1:3001`, its **own**
   volume `open-webui-staging`). It runs the exact prod image + env (aliased via the `&owui-env` YAML
   anchor in `docker-compose.yml`), so it's a faithful preview. `deploy.sh` builds → deploys the candidate
   **here first** → render-gates it → only then promotes to prod. Publicly reviewable at
   `staging.operator.kingdomofluna.com` (see `## Staging URL`).

## Commands (`./owui-fork/deploy.sh …`)

| Command | What it does |
|---|---|
| `deploy` *(default)* | build → staging → **render-gate** → (pass) save rollback point → promote to PROD → verify prod renders. Any gate failure ⇒ **prod untouched**. Post-promote prod failure ⇒ **auto-rollback**. |
| `staging` | build → staging → render-gate → **STOP**. Review at the staging URL, then `promote`. Use when you want a human/visual check before prod. |
| `promote` | render-gate the current staging image → save rollback point → promote to PROD → verify. Run after `staging` + review. |
| `rollback` | retag `agent-hub/open-webui:prev` → recreate prod → verify. Instant revert to the last-known-good image. |
| `smoke [url]` | just run the render smoke-test (default: prod `:3000`). |

`deploy.sh` leaves the staging container **running** so it doubles as the review URL. Stop it with
`docker compose --profile staging stop open-webui-staging`.

## The normal loop (frontend or backend fork change)

```bash
# 1. edit files under owui-fork/upstream/…            (Svelte in src/, Python in backend/)
# 2. REGENERATE THE PATCH  ← skip this and build.sh's `git reset --hard` WIPES your edit (see CLAUDE.md §3.1)
UP=owui-fork/upstream
git -C "$UP" add -A
git -C "$UP" diff --cached > owui-fork/patches/0001-terminal-page.patch
git -C "$UP" reset -q
# 3. SHIP IT — the gate does build + staging render-test + promote + rollback-point for you
./owui-fork/deploy.sh                 # one-shot, or:  ./owui-fork/deploy.sh staging  (review first)
# 4. commit the regenerated patch (upstream/ is gitignored — only the patch is tracked)
git add -A && git commit -m "…"       # end with the Co-Authored-By trailer
git pull --rebase origin main && git push origin main
```

If the gate fails you'll see `❌ candidate does NOT render on staging — PROD UNTOUCHED`. Fix the change,
regen the patch, run `deploy.sh` again. Prod never moved.

## Rules (do NOT break these)

- ❌ **Never** `docker compose up -d --force-recreate open-webui` directly. That's the blind deploy this
  file exists to prevent. Use `deploy.sh`.
- ✅ Regenerate the patch **before** running `deploy.sh` (it calls `build.sh`, which resets `upstream/`).
- ✅ Adapter/host-service changes don't go through this pipeline — they're not the fork. See CLAUDE.md §2
  (`docker compose build <svc>` for adapters; `systemctl --user restart …` for host services).
- ✅ If prod ever does white-screen, `./owui-fork/deploy.sh rollback` gets you back to the last image
  immediately, then debug on staging.

## Staging URL — `https://staging.operator.kingdomofluna.com` ✅ LIVE (provisioned 2026-08-09)

Behind the same Pangolin SSO as prod → aibo `:3001` (the staging container). Use it to review a candidate
build off-box (phone/laptop) during `./deploy.sh staging`. The staging container is left running after any
deploy so the URL stays live; if you ever `docker compose --profile staging stop open-webui-staging`, the
URL 502s after SSO until the next staging/deploy run brings it back.

**How it was provisioned** (for reference / a second env): DNS is a wildcard `*.operator.kingdomofluna.com`
→ `46.62.218.143` (Cloudflare, manual — Turnstile-walled, no on-box token). The Pangolin resource was
**cloned from prod `operator` (resourceId 20) in `db.sqlite`** on the VPS (`root@46.62.218.143`,
`/root/config/db/db.sqlite`) — a new `resources` row (subdomain `staging.operator`, domain2, ssl+sso),
a `targets` row (site 1 = aibo, `localhost:3001`, unique `internalPort`), and a `roleResources` grant
(roleId 1) — then the restart cascade: **VPS `docker restart pangolin gerbil traefik`** + **aibo
`sudo systemctl restart newt`** (so the tunnel learns the new target — otherwise Bad Gateway). Full detail:
`~/Documents/aibo-server/agent-hub/exposure/CLAUDE.md`. Back up `db.sqlite` before any such edit.

Local review without the subdomain still works: `ssh -L 3001:localhost:3001 aibo` → `http://localhost:3001/`.

## Files

| File | Role |
|---|---|
| `owui-fork/deploy.sh` | the gated deploy driver (deploy/staging/promote/rollback/smoke) |
| `owui-fork/smoke-test.sh` | headless-chromium render gate (exit 0/1/2) |
| `owui-fork/build.sh` | reset upstream → apply patch → docker build (+ stale-image self-heal) |
| `docker-compose.yml` → `open-webui-staging` | the `staging`-profile canary (`:3001`, own volume, `*owui-env`) |
