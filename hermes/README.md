# hermes/ — versioned Hermes brain(s) for the Agent Hub

Hermes runs on the **host** (its `computer_use` drives the real GUI — X11 on Linux, Accessibility +
Screen Recording on macOS — so it can't be containerized). This dir makes our Hermes setup
**reproducible + `hermes update`-proof + cross-platform**.

## What's captured (the part we own)
- `config.yaml.template` — the Hermes config with secrets/paths as `${ENV}` / `__HERMES_HOME__`.
- `bin/cua-driver` — the **self-heal** cua wrapper: bounds the AT-SPI walk AND transparently recovers
  from "session expired" (respawn + replay handshake + resend) so computer_use never flails.
- `hooks/deny-destructive.py` — the hard guardrail (rm -rf /, mkfs, dd to /dev, shutdown, fork bombs…).
- `install.sh` — applies all of the above + installs the gateway service (systemd/launchd).

## Prereqs
- **base hermes-agent** — `NousResearch/hermes-agent` is **public**; install it with the official
  one-liner (`curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash`), which sets up uv,
  Python 3.11, node, ripgrep and ffmpeg and lands at `~/.hermes/hermes-agent` + `~/.local/bin/hermes`.
  Heads-up: it writes its own `~/.hermes/config.yaml`, which **`./hermes/install.sh` then overwrites**
  from `config.yaml.template` — that's the point, but back the original up if you want to diff it.
  (Historical note: this used to be a private repo + manual venv.) `install.sh` verifies
  it and stops with instructions if missing (or runs `HERMES_INSTALL_CMD` if you set it).
- Python 3, and on macOS grant `cua-driver` **Accessibility + Screen Recording** when prompted.
- Secrets in the repo `.env`: `OPENROUTER_API_KEY`, `HERMES_DASH_USER`, `HERMES_DASH_PW_HASH`.

## Install / refresh a brain
```bash
./hermes/install.sh                      # the default brain on :9119 (~/.hermes)
./hermes/install.sh --name coder --port 9120   # a second brain (E8.2) -> ~/.hermes-coder
```
Idempotent — re-run any time (e.g. after `hermes update`) to re-apply config + wrapper + hooks + service.

## Survives `hermes update`
`hermes update` touches the hermes-agent code, not this dir. The cua wrapper lives in the brain's
`bin/` and is pointed at via `HERMES_CUA_DRIVER_CMD`, so it survives. Re-running `install.sh` restores
config/hooks/wrapper if an update ever disturbs them.

## Multiple brains (E8.2)
Each brain = its own `~/.hermes-<name>/` (config + memory) + port + service, each surfaced as a
selectable model in OWUI via the hermes adapter. Guardrail + self-heal wrapper apply to every brain.

## Model routing: what runs where, and why

| Layer | Runs on | Notes |
|---|---|---|
| **Main brain** (the agent loop) | `z-ai/glm-5.2` via OpenRouter | Current choice. glm-5.2 has no vision — hence `agent.image_input_mode: text`, which routes every image through `auxiliary.vision`. |
| **Auxiliary** (vision, web_extract, compression, title_generation) | `owui-claude` on `localhost:9212` | The Claude Agent SDK on a Claude Code subscription. No marginal API cost. |
| **Last-resort fallback** | OpenRouter, `:free` SKUs only | `auxiliary.free_only: true` + `auxiliary.openrouter_model` pinned to a real `:free` model. |

### PARKED: putting Hermes' *main brain* on a Claude subscription

Auxiliary already runs on the Claude Agent SDK (see the table above). The obvious next step — point the
main brain at it too — **does not work as-is, and would cost you Hermes.**

`owui-claude/server.mjs` never reads `body.tools`. It builds `opts.mcpServers` + `canUseTool` and runs
the **Agent SDK's own** agentic loop with the **Agent SDK's own** tools. That is exactly why it is
perfect for auxiliary tasks (plain completions, no tools involved) and wrong for the brain: Hermes
would stop being able to reach `computer_use`/cua, its memory and learning loop, its skills, and its
subagents. You would get Claude Code wearing a Hermes hat — the dashboard and the gateway, none of the
agent.

The distinction worth holding onto: **Hermes is a harness; the model is a brain it calls.** Swapping the
brain keeps Hermes. Swapping the harness does not.

**The actual work**, when we pick this up:

1. Add a provider to Hermes' `PROVIDER_REGISTRY` (`hermes_cli/auth.py`) — an `anthropic-oauth` /
   `claude-code` entry modeled on `openai-codex`, which is `auth_type: "oauth_external"` and already
   does this trick for the Codex CLI's credentials.
2. It must expose a **plain completions surface** so Hermes keeps driving its own loop and passing its
   own tool definitions. This is the whole ballgame — an endpoint that runs its own loop is a harness,
   not a provider.
3. **Use a different Claude account's subscription than `CLAUDE_CODE_OAUTH_TOKEN`.** Hermes running a
   long-horizon loop on the same sub as `owui-claude` (and Claude Code itself) means three agents
   contending for one account's limits. Give it its own token — a separate `.env` var, e.g.
   `HERMES_CLAUDE_OAUTH_TOKEN`, not the shared one.

Possibly-already-there: Hermes' existing `anthropic` provider lists `CLAUDE_CODE_OAUTH_TOKEN` among its
`api_key_env_vars`, so some of the plumbing may exist. **Untested** — verify before building anything.
