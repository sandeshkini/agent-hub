# opencode — headless `opencode serve` engine (fronted by owui-opencode)
FROM node:22-bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
      git ripgrep ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g opencode-ai@1.15.10 && npm cache clean --force

WORKDIR /workspace
ENV HOME=/root
# destructive-command guardrail (parity with deny-destructive.py) via opencode permissions
COPY opencode.json /root/.config/opencode/opencode.json
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
EXPOSE 4096
ENTRYPOINT ["/entrypoint.sh"]
