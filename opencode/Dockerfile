# opencode — headless `opencode serve` engine (fronted by owui-opencode)
FROM node:22-bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
      git ripgrep ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g opencode-ai@1.18.14 && npm cache clean --force

WORKDIR /workspace
ENV HOME=/root
# opencode config = guardrail deny-list + shared MCP tools. The compose overrides HOME=/home/node at
# runtime and opencode reads $HOME/.config/opencode/opencode.json — the old /root-only copy never
# loaded. Install to both locations to be robust to the runtime HOME.
RUN mkdir -p /root/.config/opencode /home/node/.config/opencode
COPY opencode.json /root/.config/opencode/opencode.json
COPY opencode.json /home/node/.config/opencode/opencode.json
RUN chown -R node:node /home/node/.config 2>/dev/null || true
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
EXPOSE 4096
ENTRYPOINT ["/entrypoint.sh"]
