# slack-gateway — Slack as a first-class Agent Hub frontend

Chat with **any hub model × adapter** from Slack. **Thread = session** (mapped to an OWUI chat, so the
same conversation also appears in OWUI). Renders like OWUI: streaming text + tool cards + thinking.
Runs in **Socket Mode** (no public URL). OWUI's terminal + everything else is unchanged.

## Setup (one time)
1. https://api.slack.com/apps → **Create New App → From an app manifest** → paste `slack-app-manifest.yaml`.
2. **Socket Mode** on; **App-Level Token** (scope `connections:write`) → `SLACK_APP_TOKEN` (xapp-…).
3. **Install to Workspace** → Bot User OAuth Token → `SLACK_BOT_TOKEN` (xoxb-…).
4. Put both in the repo `.env` plus `OWUI_API_KEY` (an OWUI API key or minted token). Then:
   `docker compose up -d --build slack-gateway`
5. In Slack: **DM the bot**, or **@mention** it in a channel thread. `/model` lists models;
   `@agent-hub model:claude-opus-5` switches the thread's model.

## What you get
- Any model/agent (claude·hermes·opencode·personas), streamed live (message edits).
- **Tool calls + thinking** surfaced as Slack context blocks (mirrors OWUI's cards).
- Each thread ↔ an OWUI chat (continue it in either place).
- (E9.4) AskUserQuestion → native Slack buttons.
