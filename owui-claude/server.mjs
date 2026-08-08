#!/usr/bin/env node
// owui-claude — standalone OpenAI-compatible adapter over the Claude Agent SDK.
// Reuses your Claude Code OAuth subscription (CLAUDE_CODE_OAUTH_TOKEN). Streams
// text + tool activity, real interrupt() on Stop, per-chat session via resume,
// vision (forwards OWUI images), and a canUseTool guardrail (blocks destructive
// bash). Standalone — no dependency on the chat-agent / cm / dispatch.
import http from "node:http";
import { homedir } from "node:os";
import { existsSync, mkdirSync, writeFileSync, readFileSync, unlinkSync, readdirSync } from "node:fs";
import { query } from "@anthropic-ai/claude-agent-sdk";

const PORT = Number(process.env.OWUI_CLAUDE_PORT || 9212);   // dedicated var — PORT may be polluted
const ADAPTER_KEY = process.env.ADAPTER_KEY || "";
const MCP_TOOLS_URL = process.env.MCP_TOOLS_URL || "http://mcp-tools:8000/mcp";   // shared MCP tools
const WORKSPACE = process.env.WORKSPACE || homedir() + "/.owui-claude-workspace";
const DEFAULT_MODEL = process.env.CLAUDE_MODEL || "claude-sonnet-4-5";
const MODELS = (process.env.CLAUDE_MODELS || "claude-sonnet-4-5,claude-opus-4-1").split(",").map(s => s.trim()).filter(Boolean);
if (!existsSync(WORKSPACE)) mkdirSync(WORKSPACE, { recursive: true });

// never let a stray rejection/exception kill the whole adapter
process.on("unhandledRejection", (e) => console.error("[owui-claude] unhandledRejection:", e && e.message || e));
process.on("uncaughtException", (e) => console.error("[owui-claude] uncaughtException:", e && e.message || e));

const sessions = new Map();            // chat_id -> resume session_id (bounded)
const CACHE_MAX = 512;
function cacheGet(k) { const v = sessions.get(k); if (v) { sessions.delete(k); sessions.set(k, v); } return v; }
function cachePut(k, v) { if (!v) return; sessions.set(k, v); while (sessions.size > CACHE_MAX) sessions.delete(sessions.keys().next().value); }

// ── guardrail (mirror of the Hermes deny-destructive denylist) ──
const DENY = [
  /\brm\b[^\n|;&]*\s-[a-z]*r[a-z]*f[a-z]*\b[^\n|;&]*\s(\/|\/\*|~|\$HOME|\/home|\/etc|\/usr|\/var|\/boot|\/bin|\/lib|\/sbin|\/opt|\/root)(\s|\/|\*|$)/i,
  /\brm\b[^\n]*--no-preserve-root/i,
  /\bmkfs(\.\w+)?\b/i, /\bwipefs\b/i, /\bblkdiscard\b/i, /\bcryptsetup\b\s+luksformat/i,
  /\bdd\b[^\n]*\bof=\/dev\/(sd|nvme|vd|hd|disk|mmcblk|loop)/i, />\s*\/dev\/(sd|nvme|vd|hd|mmcblk)/i,
  /\b(parted|fdisk|sfdisk|gdisk|sgdisk)\b[^\n]*\/dev\//i,
  /\b(shutdown|reboot|poweroff|halt)\b/i, /\binit\s+[06]\b/i, /\bsystemctl\b\s+(poweroff|reboot|halt|suspend)/i,
  /:\s*\(\s*\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:/,
  /\brm\b[^\n]*-[a-z]*r[a-z]*\b[^\n]*\/home\/beastblaster(\s|\/\*|$)/i,
  /\brm\b[^\n]*-[a-z]*r[a-z]*\b[^\n]*\.hermes(\s|\/\*|$)/i,
];
function isDestructive(input) {
  const s = JSON.stringify(input || {});
  return DENY.some(rx => rx.test(s));
}
// ── interactive AskUserQuestion (E7) ──
// When Claude calls AskUserQuestion, we POST a `agent:question` socket event into the OWUI chat
// message (the fork frontend renders option cards) and AWAIT the user's answer, which the browser
// POSTs to /api/agent/answer (fork) → forwarded to this adapter's POST /answer → resolves the pending
// promise → canUseTool returns {allow, updatedInput:{questions,answers}} so Claude continues with it.
const pendingQ = new Map();   // qid -> { resolve, timer }
const ANSWER_TIMEOUT = Number(process.env.ASK_TIMEOUT_MS || 300000);   // 5 min then proceed unanswered
const randId = () => Math.random().toString(36).slice(2) + Date.now().toString(36);

async function emitQuestionEvent(chatId, mid, jwt, qid, questions) {
  try {
    await fetch(`${OWUI_BASE}/api/v1/chats/${chatId}/messages/${mid}/event`, {
      method: "POST",
      headers: { "Authorization": `Bearer ${jwt}`, "Content-Type": "application/json" },
      body: JSON.stringify({ type: "agent:question", data: { id: qid, questions } }),
    });
  } catch (e) { console.error("[owui-claude] emit question failed:", e && e.message || e); }
}

// Per-turn canUseTool: guardrail always; interactive AskUserQuestion only when a browser is attached
// (ctx.interactive with mid+jwt). Non-stream / fire-and-forget runs auto-allow (no one to ask).
function makeCanUseTool(chatId, ctx) {
  return async (tool, input) => {
    if ((tool === "Bash" || tool === "Shell") && isDestructive(input)) {
      return { behavior: "deny", message: "BLOCKED by system-safety guardrail: irreversible/system-destroying command is forbidden." };
    }
    if (tool === "AskUserQuestion" && ctx && ctx.interactive && ctx.mid && ctx.jwt && Array.isArray(input?.questions)) {
      const qid = randId();
      await emitQuestionEvent(chatId, ctx.mid, ctx.jwt, qid, input.questions);
      ctx.onWait && ctx.onWait();
      const answers = await new Promise((resolve) => {
        const timer = setTimeout(() => { pendingQ.delete(qid); resolve(null); }, ANSWER_TIMEOUT);
        pendingQ.set(qid, { resolve, timer });
      });
      ctx.onResume && ctx.onResume();
      // proceed even if unanswered/timeout (empty answers) rather than hang the turn
      return { behavior: "allow", updatedInput: { questions: input.questions, answers: answers || {} } };
    }
    return { behavior: "allow", updatedInput: input };
  };
}

// ── message helpers ──
function textOf(content) {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) return content.filter(p => p && (p.type === "text" || p.type == null)).map(p => p.text || "").join(" ");
  return "";
}
function lastUser(messages) { for (let i = messages.length - 1; i >= 0; i--) if (messages[i].role === "user") return messages[i]; return null; }
// Persona/system prompt (OWUI Models send a system message) — forward it to the agent.
function systemOf(messages) { return (messages || []).filter(m => m && m.role === "system").map(m => textOf(m.content)).filter(Boolean).join("\n\n"); }
function chatIdFrom(req, body) {
  return req.headers["x-openwebui-chat-id"] || (body.metadata && body.metadata.chat_id) || body.chat_id || null;
}
// Build the SDK prompt. Text-only -> string. With images -> a one-shot async
// iterable yielding a user message with text + image blocks (vision).
function buildPrompt(userMsg) {
  const c = userMsg.content;
  if (typeof c === "string") return c;
  if (!Array.isArray(c)) return String(c || "");
  const blocks = [];
  for (const p of c) {
    if (!p || typeof p !== "object") continue;
    if (p.type === "text" && p.text) blocks.push({ type: "text", text: p.text });
    else if (p.type === "image_url" && p.image_url && p.image_url.url) {
      const m = /^data:(image\/[a-zA-Z.+-]+);base64,(.*)$/s.exec(p.image_url.url);
      if (m) blocks.push({ type: "image", source: { type: "base64", media_type: m[1], data: m[2] } });
    }
  }
  const hasImage = blocks.some(b => b.type === "image");
  if (!hasImage) return blocks.map(b => b.text).join(" ");
  return (async function* () { yield { type: "user", message: { role: "user", content: blocks } }; })();
}

// HTML-attribute escape for the native <details type="tool_calls"> card.
function htmlAttr(s) {
  return String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}
// Build the markup OWUI renders as a NATIVE tool card ("✓ View Result from <name>", expandable to
// args+result). Verified via screenshot that OWUI renders <details type="tool_calls"> from stored content.
function toolCard(name, argsJson, resultText, isError) {
  let res = resultText || "";
  // FIX(bug2): neutralize backticks (-> U+02BB) BEFORE truncation so a ``` near the 4000-char
  // cut can't break the HTML attribute/fence (same approach as owui-hermes _fence()).
  res = res.replace(/`/g, "ʻ");
  if (res.length > 4000) res = res.slice(0, 4000) + "\n…(truncated)";
  return `\n<details type="tool_calls" done="true" name="${htmlAttr(name)}" arguments="${htmlAttr(argsJson || "{}")}" result="${htmlAttr(JSON.stringify(res))}"${isError ? ' error="true"' : ""}>\n<summary>Tool Executed</summary>\n</details>\n`;
}
// FIX(bug1): render accumulated thinking text as a collapsible OWUI reasoning block.
function reasoningCard(text) {
  return `\n<details type="reasoning" done="true">\n<summary>Thinking</summary>\n${text}\n</details>\n`;
}

// ── the turn: yields {content}/{usage} ──
async function* streamTurn(chatId, model, messages, ctx = {}) {
  const um = lastUser(messages);
  if (!um) { yield { content: "_(no user message)_" }; return; }
  const sys = systemOf(messages);
  let prompt = buildPrompt(um);
  if (sys && typeof prompt === "string") prompt = sys + "\n\n" + prompt;   // persona instructions
  const conv = chatId ? "id:" + chatId : "h:" + textOf(um.content).slice(0, 40);
  const resume = cacheGet(conv);
  const opts = { canUseTool: makeCanUseTool(chatId, ctx), cwd: WORKSPACE, includePartialMessages: true, model: model || DEFAULT_MODEL };
  // shared MCP tools (publish_artifact + notify) — same server all agents use; calls render as native cards.
  // strictMcpConfig: load ONLY this MCP server (ignore any host/project .mcp.json so the agent doesn't
  // pick up unrelated servers like claude-monitor via the /home/beastblaster mount).
  if (MCP_TOOLS_URL) { opts.mcpServers = { tools: { type: "http", url: MCP_TOOLS_URL } }; opts.strictMcpConfig = true; }
  if (sys) opts.appendSystemPrompt = sys;                                   // also as a real system prompt (covers image turns)
  if (resume) opts.resume = resume;

  let q;
  try { q = query({ prompt, options: opts }); }
  catch (e) { yield { content: `\n_Claude adapter error: ${e && e.message || e}_` }; return; }

  let streamedText = "", tool = {}, toolJson = {}, toolQueue = [], pendingTools = {}, completed = false;
  let thinking = {};   // FIX(bug1): per-block accumulator for thinking_delta text
  try {
    for await (const m of q) {
      if (m.type === "system" && m.subtype === "init") { if (m.session_id) cachePut(conv, m.session_id); }
      else if (m.type === "stream_event") {
        const ev = m.event;
        if (ev?.type === "content_block_start" && ev.content_block?.type === "tool_use") {
          tool[ev.index] = { name: ev.content_block.name, id: ev.content_block.id }; toolJson[ev.index] = "";
          toolQueue.push(ev.content_block.name);   // fallback label if we can't correlate by id
        } else if (ev?.type === "content_block_start" && ev.content_block?.type === "thinking") {
          thinking[ev.index] = "";   // FIX(bug1): start accumulating a thinking block
        } else if (ev?.type === "content_block_delta" && ev.delta) {
          if (ev.delta.type === "text_delta" && ev.delta.text) { streamedText += ev.delta.text; yield { content: ev.delta.text }; }
          else if (ev.delta.type === "thinking_delta" && ev.delta.thinking != null && thinking[ev.index] != null) thinking[ev.index] += ev.delta.thinking;   // FIX(bug1)
          else if (ev.delta.type === "input_json_delta" && tool[ev.index]) toolJson[ev.index] += (ev.delta.partial_json || "");
        } else if (ev?.type === "content_block_stop" && tool[ev.index]) {
          // FIX(bug3): guard the partial-JSON parse so malformed/partial input never throws or
          // produces a broken card (mirror chat-agent's JSON.parse(... || "{}") guarded pattern).
          let input;
          try { input = JSON.stringify(JSON.parse(toolJson[ev.index] || "{}")); }
          catch { input = "{}"; }
          pendingTools[tool[ev.index].id] = { name: tool[ev.index].name, input };   // keep name+args for the card
          delete tool[ev.index]; delete toolJson[ev.index];
        } else if (ev?.type === "content_block_stop" && thinking[ev.index] != null) {
          const t = (thinking[ev.index] || "").trim();   // FIX(bug1): emit reasoning block on close
          delete thinking[ev.index];
          if (t) yield { content: reasoningCard(t) };
        }
      } else if (m.type === "assistant") {
        const norm = streamedText.replace(/\s+/g, "");
        for (const b of (m.message?.content || [])) {
          if (b.type === "text" && b.text) { const bn = b.text.replace(/\s+/g, ""); if (!(norm && bn && norm.includes(bn))) yield { content: b.text }; }
        }
        streamedText = "";
      } else if (m.type === "user") {
        const c = m.message?.content;
        if (Array.isArray(c)) for (const b of c) if (b.type === "tool_result") {
          try {   // one bad tool result must not kill the turn
            // Handle every tool_result shape so the card's result is never blank: plain string,
            // an array of blocks (text -> text, image -> [image], other -> its text or JSON), or
            // a bare object. Old code only kept type==="text" blocks -> image/structured = empty.
            let txt = typeof b.content === "string" ? b.content
              : Array.isArray(b.content)
                ? b.content.map(x => x?.type === "text" ? (x.text || "")
                    : x?.type === "image" ? "[image]"
                    : (x?.text ?? JSON.stringify(x))).join("\n")
                : (b.content == null ? "" : (typeof b.content === "object" ? JSON.stringify(b.content) : String(b.content)));
            txt = (txt || "").trim();
            const pt = pendingTools[b.tool_use_id] || {};
            const name = pt.name || toolQueue.shift() || "tool";
            if (b.tool_use_id) delete pendingTools[b.tool_use_id];
            // Native OWUI tool card (collapsible "View Result from <name>", expands to args + result).
            yield { content: toolCard(name, pt.input, txt, b.is_error) };
          } catch (te) { console.error("[owui-claude] tool_result render error:", te && te.message || te); }
        }
      } else if (m.type === "result") {
        if (m.session_id) cachePut(conv, m.session_id);
        if (m.usage) yield { usage: m.usage };
        if (m.subtype && m.subtype !== "success" && !streamedText) yield { content: `\n_(${m.subtype})_` };
        completed = true;
        break;
      }
    }
  } catch (e) {
    console.error("[owui-claude] streamTurn error:", e && e.stack || e && e.message || e);   // never swallow silently
    yield { content: `\n\n_⚠️ Claude adapter hiccup (${String(e && e.message || e).slice(0, 200)}). Partial result is above — resend to continue._` };
  } finally {
    // interrupt ONLY if the turn didn't finish (client aborted). On normal completion the
    // process is already gone and interrupt() rejects — catch that (it's a promise, not a throw).
    if (!completed) { try { const p = q && q.interrupt && q.interrupt(); if (p && p.catch) p.catch(() => {}); } catch (e) { console.error('[owui-claude] interrupt error:', e); } }   // FIX(bug4): don't swallow silently
  }
}

function openaiUsage(u) {
  return { prompt_tokens: u.input_tokens || 0, completion_tokens: u.output_tokens || 0, total_tokens: (u.input_tokens || 0) + (u.output_tokens || 0) };
}

// ── HTTP (OpenAI-compatible) ──
// ── native fire-and-forget: mirror output into the OWUI chat message ──
// OWUI forwards X-OpenWebUI-Chat-Id / -Message-Id / -User-Jwt. POSTing
// {content} to /api/v1/chats/{cid}/messages/{mid} both persists AND emits a
// live socket update, so a detached run's progress + result land in the chat
// whether or not a browser is watching.
const OWUI_BASE = process.env.OWUI_BASE || "http://open-webui:8080";
const MIRROR_MS = Number(process.env.MIRROR_THROTTLE_MS || 1500);
class Mirror {
  constructor(cid, mid, jwt) {
    this.cid = cid; this.mid = mid; this.jwt = jwt;
    this.on = !!(cid && mid && jwt);
    this.last = 0; this.pending = null; this.timer = null;
    this.chain = Promise.resolve();   // serialize writes so a stale one can't overwrite the final
  }
  _rawPost(content) {
    return fetch(`${OWUI_BASE}/api/v1/chats/${this.cid}/messages/${this.mid}`, {
      method: "POST",
      headers: { "Authorization": `Bearer ${this.jwt}`, "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
    }).catch(() => {});   // OWUI unreachable — best-effort mirror
  }
  // Chain every post so they land in call order → the final done() write always wins (prevents the
  // "last words missing" / "response gone on reload" race where a stale update overwrote the final).
  _post(content) { this.chain = this.chain.then(() => this._rawPost(content)); return this.chain; }
  update(content) {
    if (!this.on) return;
    this.pending = content;
    const now = Date.now();
    if (now - this.last > MIRROR_MS) { this.last = now; const c = this.pending; this.pending = null; this._post(c); }
    else if (!this.timer) {
      this.timer = setTimeout(() => {
        this.timer = null; this.last = Date.now();
        if (this.pending != null) { const c = this.pending; this.pending = null; this._post(c); }
      }, MIRROR_MS);
    }
  }
  async done(content) {
    if (!this.on) return;
    if (this.timer) { clearTimeout(this.timer); this.timer = null; }
    await this._post(content);
  }
}

// ── FF4: durable mirrored runs ──
// Persist each mirrored run to /ffstate (a bind-mounted volume). If the adapter is
// killed mid-run (restart/reboot), the in-flight Claude generation dies — so on startup
// we RE-ISSUE the stored turn and mirror the fresh output into the same OWUI message,
// so the answer still lands. Everything is guarded; if /ffstate is unavailable, normal
// streaming is unaffected.
const FF_DIR = process.env.FF_STATE_DIR || "/ffstate";
let FF_ON = false;
try { mkdirSync(FF_DIR, { recursive: true }); FF_ON = true; } catch { FF_ON = false; }
const ffFile = (cid, mid) => `${FF_DIR}/claude__${Buffer.from(`${cid}|${mid}`).toString("hex")}.json`;
function ffWrite(rec) { if (!FF_ON) return; try { writeFileSync(ffFile(rec.cid, rec.mid), JSON.stringify(rec)); } catch {} }
function ffClear(cid, mid) { if (!FF_ON) return; try { unlinkSync(ffFile(cid, mid)); } catch {} }
async function recoverFF() {
  if (!FF_ON) return;
  let files = [];
  try { files = readdirSync(FF_DIR).filter(f => f.startsWith("claude__") && f.endsWith(".json")); } catch { return; }
  for (const f of files) {
    const p = `${FF_DIR}/${f}`;
    let rec; try { rec = JSON.parse(readFileSync(p, "utf8")); } catch { try { unlinkSync(p); } catch {} continue; }
    const mirror = new Mirror(rec.cid, rec.mid, rec.jwt);
    if (!mirror.on) { try { unlinkSync(p); } catch {} continue; }
    console.log(`[owui-claude] FF4: re-issuing interrupted run cid=${rec.cid}`);
    let full = "_↻ Auto-resumed after an adapter restart._\n\n";
    try {
      await mirror.done(full);
      for await (const d of streamTurn(rec.cid, rec.model, rec.messages)) { if (d.content) { full += d.content; mirror.update(full); } }
      await mirror.done(full);
    } catch (e) {
      try { await mirror.done(full + `\n\n_⚠️ Could not finish auto-resume (${String(e && e.message || e).slice(0, 120)}). Resend to retry._`); } catch {}
    }
    try { unlinkSync(p); } catch {}
  }
}

function authed(req) { return !ADAPTER_KEY || req.headers["authorization"] === `Bearer ${ADAPTER_KEY}`; }
function sendJson(res, code, obj) { const b = JSON.stringify(obj); res.writeHead(code, { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(b) }); res.end(b); }

const server = http.createServer((req, res) => {
  if (!authed(req)) return sendJson(res, 401, { error: "unauthorized" });
  const url = req.url || "";
  if (req.method === "GET" && (url.endsWith("/models") || url.endsWith("/models/"))) {
    return sendJson(res, 200, { object: "list", data: MODELS.map(id => ({ id, object: "model", created: 0, owned_by: "claude" })) });
  }
  if (req.method === "GET" && url.includes("/health")) return sendJson(res, 200, { ok: true });
  // E7: the fork's /api/agent/answer proxies the user's AskUserQuestion answer here → resolve the turn.
  if (req.method === "POST" && url.includes("/answer")) {
    let raw = ""; req.on("data", c => raw += c);
    req.on("end", () => {
      let b; try { b = JSON.parse(raw || "{}"); } catch { b = {}; }
      const p = b.id && pendingQ.get(b.id);
      if (p) { clearTimeout(p.timer); pendingQ.delete(b.id); p.resolve(b.answers || {}); return sendJson(res, 200, { ok: true }); }
      return sendJson(res, 404, { ok: false, error: "no pending question" });
    });
    return;
  }
  if (req.method !== "POST" || !url.includes("chat/completions")) return sendJson(res, 404, { error: "not found" });

  let raw = "";
  req.on("data", c => raw += c);
  req.on("end", async () => {
    let body; try { body = JSON.parse(raw || "{}"); } catch (e) { return sendJson(res, 400, { error: String(e) }); }
    const messages = body.messages || [];
    const stream = !!body.stream;
    const model = body.model || DEFAULT_MODEL;
    const chatId = chatIdFrom(req, body);
    const cid = "chatcmpl-claude";
    const created = Math.floor(Date.now() / 1000);

    if (!stream) {
      let parts = [], usage = null;
      try { for await (const d of streamTurn(chatId, model, messages)) { if (d.content) parts.push(d.content); else if (d.usage) usage = openaiUsage(d.usage); } }
      catch (e) { parts.push(`\n_adapter error: ${e}_`); }
      return sendJson(res, 200, { id: cid, object: "chat.completion", created, model, choices: [{ index: 0, message: { role: "assistant", content: parts.join("") }, finish_reason: "stop" }], usage: usage || { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 } });
    }

    res.writeHead(200, { "Content-Type": "text/event-stream", "Cache-Control": "no-cache" });
    const send = (delta, finish = null) => res.write(`data: ${JSON.stringify({ id: cid, object: "chat.completion.chunk", created, model, choices: [{ index: 0, delta, finish_reason: finish }] })}\n\n`);
    const mid = req.headers["x-openwebui-message-id"];
    const jwt = req.headers["x-openwebui-user-jwt"];
    // E7: keep the SSE stream alive (comment pings) while an AskUserQuestion is awaiting the user's
    // answer, so OWUI's upstream read doesn't time out during the pause.
    let kaTimer = null;
    const ctx = {
      mid, jwt, interactive: true,
      onWait: () => { if (!kaTimer) kaTimer = setInterval(() => { try { res.write(": keepalive\n\n"); } catch {} }, 15000); },
      onResume: () => { if (kaTimer) { clearInterval(kaTimer); kaTimer = null; } },
    };
    const gen = streamTurn(chatId, model, messages, ctx);
    const mirror = new Mirror(chatId, mid, jwt);
    if (mirror.on) ffWrite({ cid: chatId, mid: mirror.mid, jwt: mirror.jwt, model, messages, ts: Date.now() });
    let clientGone = false;
    // res "close" = the browser dropped (tab closed / Stop). If we're mirroring into
    // a real OWUI chat, DETACH: keep the run going and keep mirroring so the result
    // lands in the chat (native fire-and-forget). Without a mirror target, abort as
    // before. (req.on("close") is wrong — it fires right after the body is read.)
    res.on("close", () => {
      if (res.writableFinished) return;
      clientGone = true;
      if (!mirror.on && gen.return) gen.return();   // no chat to mirror to → abort like before
    });
    let full = "";
    try {
      send({ role: "assistant" });
      for await (const d of gen) {
        if (clientGone && !mirror.on) break;        // detached only when mirroring
        if (d.usage) { if (!clientGone) { try { res.write(`data: ${JSON.stringify({ id: cid, object: "chat.completion.chunk", created, model, choices: [], usage: openaiUsage(d.usage) })}\n\n`); } catch { clientGone = true; } } }
        else if (d.content) {
          full += d.content;
          if (!clientGone) { try { send({ content: d.content }); } catch { clientGone = true; } }
          mirror.update(full);
        }
      }
      await mirror.done(full);
      if (!clientGone) { send({}, "stop"); res.write("data: [DONE]\n\n"); }
    } catch { try { await mirror.done(full); } catch {} }
    if (kaTimer) { clearInterval(kaTimer); kaTimer = null; }   // E7: stop any AskUserQuestion keepalive
    if (mirror.on) ffClear(chatId, mirror.mid);   // run finished → drop the durable record
    try { res.end(); } catch {}
  });
});
server.listen(PORT, "0.0.0.0", () => console.log(`[owui-claude] Agent-SDK adapter on 0.0.0.0:${PORT} (auth=${ADAPTER_KEY ? "on" : "off"}, workspace=${WORKSPACE}, ff4=${FF_ON ? "on" : "off"})`));
recoverFF().catch(() => {});   // FF4: re-issue any runs interrupted by the last restart
