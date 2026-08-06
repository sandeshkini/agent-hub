#!/usr/bin/env node
// owui-claude — standalone OpenAI-compatible adapter over the Claude Agent SDK.
// Reuses your Claude Code OAuth subscription (CLAUDE_CODE_OAUTH_TOKEN). Streams
// text + tool activity, real interrupt() on Stop, per-chat session via resume,
// vision (forwards OWUI images), and a canUseTool guardrail (blocks destructive
// bash). Standalone — no dependency on the chat-agent / cm / dispatch.
import http from "node:http";
import { homedir } from "node:os";
import { existsSync, mkdirSync } from "node:fs";
import { query } from "@anthropic-ai/claude-agent-sdk";

const PORT = Number(process.env.OWUI_CLAUDE_PORT || 9212);   // dedicated var — PORT may be polluted
const ADAPTER_KEY = process.env.ADAPTER_KEY || "";
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
const canUseTool = async (tool, input) => {
  if ((tool === "Bash" || tool === "Shell") && isDestructive(input)) {
    return { behavior: "deny", message: "BLOCKED by system-safety guardrail: irreversible/system-destroying command is forbidden." };
  }
  return { behavior: "allow", updatedInput: input };
};

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

// ── the turn: yields {content}/{usage} ──
async function* streamTurn(chatId, model, messages) {
  const um = lastUser(messages);
  if (!um) { yield { content: "_(no user message)_" }; return; }
  const sys = systemOf(messages);
  let prompt = buildPrompt(um);
  if (sys && typeof prompt === "string") prompt = sys + "\n\n" + prompt;   // persona instructions
  const conv = chatId ? "id:" + chatId : "h:" + textOf(um.content).slice(0, 40);
  const resume = cacheGet(conv);
  const opts = { canUseTool, cwd: WORKSPACE, includePartialMessages: true, model: model || DEFAULT_MODEL };
  if (sys) opts.appendSystemPrompt = sys;                                   // also as a real system prompt (covers image turns)
  if (resume) opts.resume = resume;

  let q;
  try { q = query({ prompt, options: opts }); }
  catch (e) { yield { content: `\n_Claude adapter error: ${e && e.message || e}_` }; return; }

  let streamedText = "", tool = {}, toolJson = {}, completed = false;
  try {
    for await (const m of q) {
      if (m.type === "system" && m.subtype === "init") { if (m.session_id) cachePut(conv, m.session_id); }
      else if (m.type === "stream_event") {
        const ev = m.event;
        if (ev?.type === "content_block_start" && ev.content_block?.type === "tool_use") {
          tool[ev.index] = { name: ev.content_block.name }; toolJson[ev.index] = "";
          yield { content: `\n\n🔧 **${ev.content_block.name}**\n` };
        } else if (ev?.type === "content_block_delta" && ev.delta) {
          if (ev.delta.type === "text_delta" && ev.delta.text) { streamedText += ev.delta.text; yield { content: ev.delta.text }; }
          else if (ev.delta.type === "input_json_delta" && tool[ev.index]) toolJson[ev.index] += (ev.delta.partial_json || "");
        } else if (ev?.type === "content_block_stop" && tool[ev.index]) { delete tool[ev.index]; delete toolJson[ev.index]; }
      } else if (m.type === "assistant") {
        const norm = streamedText.replace(/\s+/g, "");
        for (const b of (m.message?.content || [])) {
          if (b.type === "text" && b.text) { const bn = b.text.replace(/\s+/g, ""); if (!(norm && bn && norm.includes(bn))) yield { content: b.text }; }
        }
        streamedText = "";
      } else if (m.type === "user") {
        const c = m.message?.content;
        if (Array.isArray(c)) for (const b of c) if (b.type === "tool_result") {
          let txt = typeof b.content === "string" ? b.content : Array.isArray(b.content) ? b.content.filter(x => x.type === "text").map(x => x.text).join("\n") : "";
          txt = (txt || "").trim(); if (txt.length > 4000) txt = txt.slice(0, 4000) + "\n…(truncated)";
          yield { content: (txt ? "```\n" + txt + "\n```\n" : "") + (b.is_error ? "⚠️\n" : "✅\n") };
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
    yield { content: `\n\n_Claude error: ${String(e && e.message || e).slice(0, 300)}_` };
  } finally {
    // interrupt ONLY if the turn didn't finish (client aborted). On normal completion the
    // process is already gone and interrupt() rejects — catch that (it's a promise, not a throw).
    if (!completed) { try { const p = q && q.interrupt && q.interrupt(); if (p && p.catch) p.catch(() => {}); } catch {} }
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
  }
  async _post(content) {
    try {
      await fetch(`${OWUI_BASE}/api/v1/chats/${this.cid}/messages/${this.mid}`, {
        method: "POST",
        headers: { "Authorization": `Bearer ${this.jwt}`, "Content-Type": "application/json" },
        body: JSON.stringify({ content }),
      });
    } catch { /* OWUI unreachable — best-effort mirror */ }
  }
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

function authed(req) { return !ADAPTER_KEY || req.headers["authorization"] === `Bearer ${ADAPTER_KEY}`; }
function sendJson(res, code, obj) { const b = JSON.stringify(obj); res.writeHead(code, { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(b) }); res.end(b); }

const server = http.createServer((req, res) => {
  if (!authed(req)) return sendJson(res, 401, { error: "unauthorized" });
  const url = req.url || "";
  if (req.method === "GET" && (url.endsWith("/models") || url.endsWith("/models/"))) {
    return sendJson(res, 200, { object: "list", data: MODELS.map(id => ({ id, object: "model", created: 0, owned_by: "claude" })) });
  }
  if (req.method === "GET" && url.includes("/health")) return sendJson(res, 200, { ok: true });
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
    const gen = streamTurn(chatId, model, messages);
    const mirror = new Mirror(chatId, req.headers["x-openwebui-message-id"], req.headers["x-openwebui-user-jwt"]);
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
    try { res.end(); } catch {}
  });
});
server.listen(PORT, "0.0.0.0", () => console.log(`[owui-claude] Agent-SDK adapter on 0.0.0.0:${PORT} (auth=${ADAPTER_KEY ? "on" : "off"}, workspace=${WORKSPACE})`));
