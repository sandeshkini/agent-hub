#!/usr/bin/env bash
# register-node.sh — add/remove a remote agent machine as an OWUI admin Connection at RUNTIME
# (no restart, no OWUI fork). Uses OWUI's documented admin API: GET /openai/config +
# POST /openai/config/update. Each node gets a unique prefix_id so model ids never collide
# (aibo.claude-sonnet-5, mac.claude-sonnet-5, ...). Dead/asleep nodes just drop their models
# (10s model-list timeout) — the picker keeps working. See research/agent-hub/multi-machine-plan.md.
#
# Usage:
#   register-node.sh add   <prefix> <base_url> <key> [model_id,model_id,...]
#   register-node.sh remove <prefix>
#   register-node.sh list
# Env: OWUI=http://localhost:3000  OWUI_TOKEN=<admin jwt (or /tmp/owui_tok.txt)>
set -euo pipefail
OWUI="${OWUI:-http://localhost:3000}"
TOKEN="${OWUI_TOKEN:-$(cat /tmp/owui_tok.txt 2>/dev/null || true)}"
[ -n "$TOKEN" ] || { echo "need OWUI_TOKEN (admin jwt)"; exit 1; }
ACT="${1:-list}"
export OWUI OWUI_TOKEN_RESOLVED="$TOKEN"

python3 - "$ACT" "${2:-}" "${3:-}" "${4:-}" "${5:-}" <<'PY'
import json, os, sys, urllib.request
OWUI=os.environ.get("OWUI","http://localhost:3000"); TOK=os.environ["OWUI_TOKEN_RESOLVED"]
act,prefix,base,key,models = (sys.argv[1:6]+[""]*5)[:5]
H={"Authorization":"Bearer "+TOK,"Content-Type":"application/json"}
def call(method,path,body=None):
    r=urllib.request.Request(OWUI+path,data=(json.dumps(body).encode() if body is not None else None),method=method,headers=H)
    return json.loads(urllib.request.urlopen(r,timeout=20).read().decode() or "null")
cfg=call("GET","/openai/config")
urls=list(cfg.get("OPENAI_API_BASE_URLS") or []); keys=list(cfg.get("OPENAI_API_KEYS") or [])
confs=dict(cfg.get("OPENAI_API_CONFIGS") or {})
def idx_of_prefix(p):
    for i,c in confs.items():
        if isinstance(c,dict) and c.get("prefix_id")==p: return int(i)
    return None
if act=="list":
    for i,u in enumerate(urls):
        c=confs.get(str(i),{}) or {}
        print(f"  [{i}] prefix={c.get('prefix_id','-'):<8} {u:<40} models={c.get('model_ids') or 'auto'}")
    sys.exit(0)
if act=="add":
    assert prefix and base and key, "add needs <prefix> <base_url> <key>"
    ex=idx_of_prefix(prefix)
    conf={"enable":True,"prefix_id":prefix,"tags":[{"name":prefix}],
          "model_ids":[m for m in models.split(",") if m]}
    if ex is not None:                      # idempotent: replace in place
        urls[ex]=base; keys[ex]=key; confs[str(ex)]=conf
    else:
        urls.append(base); keys.append(key); confs[str(len(urls)-1)]=conf
elif act=="remove":
    ex=idx_of_prefix(prefix); assert ex is not None, f"no connection with prefix {prefix}"
    urls.pop(ex); keys.pop(ex)
    # reindex configs after the removed slot
    newc={}
    for i,c in confs.items():
        i=int(i)
        if i==ex: continue
        newc[str(i-1 if i>ex else i)]=c
    confs=newc
else:
    print("usage: add|remove|list"); sys.exit(1)
call("POST","/openai/config/update",{"ENABLE_OPENAI_API":True,"OPENAI_API_BASE_URLS":urls,
      "OPENAI_API_KEYS":keys,"OPENAI_API_CONFIGS":confs})
print(f"OK: {act} {prefix} -> now {len(urls)} connections")
PY
