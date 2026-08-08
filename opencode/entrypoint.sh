#!/bin/sh
set -e
# Seed opencode's auth from the env key (opencode Zen provider). Shape mirrors
# ~/.local/share/opencode/auth.json: {"opencode":{"type":"api","key":"..."}}
mkdir -p "$HOME/.local/share/opencode"
if [ -n "$OPENCODE_API_KEY" ]; then
  printf '{"opencode":{"type":"api","key":"%s"}}\n' "$OPENCODE_API_KEY" \
    > "$HOME/.local/share/opencode/auth.json"
fi
# External MCP hub: merge MCP_SERVERS (one place in .env -> all adapters) into opencode's mcp config.
# Standard MCP entries -> opencode format: HTTP/SSE {name,url[,headers]} -> {type:"remote"}, and
# stdio {name,command[,args][,env]} -> {type:"local",command:[...]}. See mcp/README.md.
node -e '
const fs=require("fs"),os=require("os"),p=os.homedir()+"/.config/opencode/opencode.json";
let cfg={}; try{cfg=JSON.parse(fs.readFileSync(p,"utf8"))}catch(e){cfg={}}
cfg.mcp=cfg.mcp||{};
let extra=[]; try{extra=JSON.parse(process.env.MCP_SERVERS||"[]")}catch(e){}
for(const s of extra){
  if(!s||!s.name) continue;
  if(s.url){ cfg.mcp[s.name]={type:"remote",url:s.url,enabled:true, ...(s.headers?{headers:s.headers}:{})}; }
  else if(s.command){ const cmd=Array.isArray(s.command)?s.command:[s.command,...(s.args||[])]; cfg.mcp[s.name]={type:"local",command:cmd,enabled:true, ...(s.env?{environment:s.env}:{})}; }
}
fs.writeFileSync(p,JSON.stringify(cfg,null,2));
console.log("[opencode] mcp servers:",Object.keys(cfg.mcp).join(", "));
' 2>/dev/null || true

exec opencode serve --hostname 0.0.0.0 --port 4096
