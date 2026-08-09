# owui-claude — OpenAI-compatible adapter over the Claude Agent SDK.
# The SDK (0.3.222) spawns the `claude` CLI from PATH; we install the matching
# build (2.1.222) globally. Auth is the subscription OAuth token (env), never
# ANTHROPIC_API_KEY.
FROM node:22-bookworm-slim

# git + ripgrep are used by Claude Code's tools; ca-certificates for TLS.
RUN apt-get update && apt-get install -y --no-install-recommends \
      git ripgrep ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY package.json ./
RUN npm install --omit=dev \
    && npm install -g @anthropic-ai/claude-code@2.1.223 \
    && npm cache clean --force
COPY server.mjs ./

RUN mkdir -p /workspace
ENV NODE_ENV=production HOME=/root OWUI_CLAUDE_PORT=9212 WORKSPACE=/workspace
EXPOSE 9212
CMD ["node", "server.mjs"]

# Pre-create the SDK's config dir OWNED BY node. Without this, bind-mounting a file into
# /home/node/.claude makes Docker create the parent as root, and the node user then cannot write
# its own state there -> every tool call dies with
#   EACCES: permission denied, mkdir '/home/node/.claude/session-env'
RUN mkdir -p /home/node/.claude && chown -R node:node /home/node/.claude
