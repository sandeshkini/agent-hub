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
