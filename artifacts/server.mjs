import http from 'node:http'
import fs from 'node:fs'
import path from 'node:path'
import { marked } from 'marked'

const ROOT = process.env.CONTENT_DIR || '/content'
const BASE = process.env.BASE_PATH || ''            // external prefix (traefik strips it)
const PORT = process.env.PORT || 8080
const TOKEN = process.env.PUBLISH_TOKEN || ''       // required for POST /api/publish
// ── notification (fires on publish) — config-driven; no-op until ntfy is set ──
const NTFY_URL = process.env.NTFY_URL || ''         // e.g. http://ntfy:80  (internal)
const NTFY_TOPIC = process.env.NTFY_TOPIC || ''
const NTFY_TOKEN = process.env.NTFY_TOKEN || ''
const PUBLIC_BASE = process.env.PUBLIC_BASE || 'https://apps.kingdomofluna.com'

for (const d of ['summaries', 'apps']) fs.mkdirSync(path.join(ROOT, d), { recursive: true })

async function notifyPublish(title, relUrl) {
  if (!NTFY_URL || !NTFY_TOPIC) return              // not configured → skip silently
  try {
    // Use ntfy's JSON publishing API (POST to root with a JSON body) rather than the
    // header-based API: HTTP headers are Latin-1 only, so a Title header containing an
    // emoji or em-dash throws "Cannot convert argument to a ByteString" and the push is
    // silently dropped. The JSON body is UTF-8, so any title works.
    await fetch(`${NTFY_URL.replace(/\/$/, '')}/`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(NTFY_TOKEN ? { Authorization: `Bearer ${NTFY_TOKEN}` } : {}),
      },
      body: JSON.stringify({
        topic: NTFY_TOPIC,
        title: `New artifact: ${title}`,
        message: 'Published — tap to open',
        click: PUBLIC_BASE + relUrl,
        tags: ['card_file_box'],
      }),
    })
  } catch (e) { console.error('ntfy notify failed:', e.message) }
}

const CSS = `<style>
:root{color-scheme:dark}*{box-sizing:border-box}
body{font-family:system-ui,-apple-system,sans-serif;max-width:820px;margin:0 auto;padding:34px 20px;line-height:1.6;background:#0f1115;color:#e6e6e6}
a{color:#8ab4ff;text-decoration:none}a:hover{text-decoration:underline}
h1{font-size:1.7rem;margin:0 0 4px}.sub{color:#8b98a9;margin:0 0 22px;font-size:.92rem}
.card{display:flex;align-items:center;gap:12px;padding:13px 16px;margin:8px 0;background:#161a22;border:1px solid #242a35;border-radius:10px;transition:border-color .12s}
.card:hover{border-color:#3a4658}.badge{font-size:.7rem;padding:2px 8px;border-radius:20px;background:#222834;color:#9fb0c3;white-space:nowrap}
.card .t{font-weight:600;color:#e9edf3}.card .d{font-size:.78rem;color:#8b98a9;margin-top:1px}.grow{flex:1;min-width:0}
.content{background:#12151c;border:1px solid #242a35;border-radius:12px;padding:26px 30px}
.content h2{color:#e9edf3;font-size:1.25rem}.content table{border-collapse:collapse;margin:12px 0}
.content th,.content td{border:1px solid #242a35;padding:6px 11px;text-align:left}
.content code{background:#0b0d11;padding:2px 6px;border-radius:5px}.content pre{background:#0b0d11;padding:14px;border-radius:9px;overflow:auto}
.content blockquote{border-left:3px solid #3a4658;margin:0;padding-left:14px;color:#b9c2ce}
.back{display:inline-block;margin-bottom:18px;color:#8b98a9;font-size:.9rem}
</style>`
const esc = s => String(s).replace(/[&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]))
const page = (t, b) => `<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><title>${esc(t)}</title>${CSS}<body>${b}</body>`
const pretty = n => n.replace(/\.(md|html?)$/i, '').replace(/^\d{4}-\d{2}-\d{2}[-_]?/, '').replace(/[-_]/g, ' ').trim().replace(/\b\w/g, c => c.toUpperCase()) || n
const when = ms => new Date(ms).toLocaleString('en-CA', { dateStyle: 'medium', timeStyle: 'short' })
const safe = p => path.normalize(p).replace(/^(\.\.[/\\])+/, '')
const slug = s => (s || 'untitled').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 60)

function allItems() {
  const out = []
  for (const [kind, sub] of [['summary', 'summaries'], ['app', 'apps']]) {
    const dir = path.join(ROOT, sub)
    for (const name of fs.existsSync(dir) ? fs.readdirSync(dir) : []) {
      if (name.startsWith('.')) continue
      const st = fs.statSync(path.join(dir, name))
      const href = kind === 'app' ? `${BASE}/apps/${encodeURIComponent(name)}/` : `${BASE}/summaries/${encodeURIComponent(name)}`
      out.push({ kind, name, href, t: st.birthtimeMs || st.mtimeMs })
    }
  }
  return out.sort((a, b) => b.t - a.t)   // most-recent first, unified
}
function indexPage() {
  const items = allItems()
  const rows = items.map(i =>
    `<a class=card href="${i.href}"><span class=badge>${i.kind === 'app' ? '🧩 app' : '📄 summary'}</span>` +
    `<span class="grow"><div class=t>${esc(pretty(i.name))}</div><div class=d>${esc(when(i.t))}</div></span></a>`
  ).join('')
  return page('Artifacts',
    `<h1>🗂️ Artifacts</h1><p class=sub>Everything from your work, newest first. Drop a <code>.md</code>/<code>.html</code> in <code>summaries/</code> or a folder in <code>apps/</code> — or POST to the publish API.</p>` +
    (rows || `<p class=sub>nothing yet</p>`))
}

const TYPES = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json', '.png': 'image/png', '.jpg': 'image/jpeg', '.svg': 'image/svg+xml', '.ico': 'image/x-icon', '.md': 'text/markdown' }
const send = (res, code, body, type = 'text/html; charset=utf-8') => { res.writeHead(code, { 'content-type': type }); res.end(body) }

async function readBody(req, max = 5_000_000) {
  let n = 0, chunks = []
  for await (const c of req) { n += c.length; if (n > max) throw new Error('too large'); chunks.push(c) }
  return Buffer.concat(chunks).toString('utf8')
}

const server = http.createServer(async (req, res) => {
  const url = decodeURIComponent(req.url.split('?')[0])

  // ── publish API (token-gated) ── POST {title, kind:summary|app, format:md|html, content}
  if (req.method === 'POST' && url === '/api/publish') {
    if (!TOKEN || req.headers['x-publish-token'] !== TOKEN) return send(res, 401, JSON.stringify({ error: 'bad token' }), 'application/json')
    try {
      const { title, kind = 'summary', format = 'md', content = '' } = JSON.parse(await readBody(req))
      if (!content) return send(res, 400, JSON.stringify({ error: 'content required' }), 'application/json')
      const date = new Date().toISOString().slice(0, 10)
      const base = `${date}-${slug(title)}`
      let rel
      if (kind === 'app') { fs.mkdirSync(path.join(ROOT, 'apps', base), { recursive: true }); fs.writeFileSync(path.join(ROOT, 'apps', base, 'index.html'), content); rel = `/apps/${base}/` }
      else { const ext = format === 'html' ? 'html' : 'md'; fs.writeFileSync(path.join(ROOT, 'summaries', `${base}.${ext}`), content); rel = `/summaries/${base}.${ext}` }
      notifyPublish(title || pretty(path.basename(rel)), BASE + rel)   // fire-and-forget push
      return send(res, 200, JSON.stringify({ ok: true, url: BASE + rel }), 'application/json')
    } catch (e) { return send(res, 400, JSON.stringify({ error: String(e.message || e) }), 'application/json') }
  }

  if (req.method !== 'GET') return send(res, 405, 'method not allowed')
  if (url === '/' || url === '') return send(res, 200, indexPage())

  let m = url.match(/^\/summaries\/(.+)$/)
  if (m) {
    const f = path.join(ROOT, 'summaries', safe(m[1]))
    if (!fs.existsSync(f)) return send(res, 404, page('404', 'Not found'))
    const raw = fs.readFileSync(f, 'utf8')
    if (/\.md$/i.test(f)) return send(res, 200, page(pretty(path.basename(f)), `<a class=back href="${BASE}/">← board</a><article class=content>${marked.parse(raw)}</article>`))
    return send(res, 200, raw)
  }
  m = url.match(/^\/apps\/([^/]+)(\/.*)?$/)
  if (m) {
    let rel = safe(m[2] || '/'); if (rel === '/' || rel === '') rel = '/index.html'
    const f = path.join(ROOT, 'apps', m[1], rel)
    if (!fs.existsSync(f) || fs.statSync(f).isDirectory()) return send(res, 404, page('404', 'Not found'))
    return send(res, 200, fs.readFileSync(f), TYPES[path.extname(f)] || 'application/octet-stream')
  }
  send(res, 404, page('404', `<a href="${BASE}/">← board</a><p>Not found</p>`))
})
server.listen(PORT, () => console.log(`artifacts board on :${PORT} base='${BASE}' publish=${TOKEN ? 'on' : 'off'}`))
