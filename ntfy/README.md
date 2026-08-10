# ntfy — the notification bus

Push notifications for the hub: **an agent finished**, **an agent needs your input**, **an artifact
was published**. Delivered to the ntfy app on your phone.

Hub-only (`profiles: ["hub"]`) — one bus for the whole fleet. Nodes push to the hub's ntfy rather
than running their own, so all notifications land in one place regardless of which machine did the work.

## Why ntfy and not web push

Open WebUI has **no web-push support at all** — no VAPID, no `pushManager`, and `+layout.svelte`
actively *unregisters* service workers. So browser push isn't an available fallback; ntfy is the
correct delivery channel, not a workaround.

`NTFY_UPSTREAM_BASE_URL=https://ntfy.sh` matters on iOS: self-hosted ntfy can't wake an iPhone on
its own, so it relays through ntfy.sh for instant delivery. Only the topic name transits the relay
when the message is delivered this way — keep the topic unguessable.

## ⚠️ `data/lib/user.db` IS your access control

`NTFY_AUTH_DEFAULT_ACCESS=deny-all` means **nothing** is readable or writable without credentials
from that SQLite file. Consequences:

- **Never commit it.** It's gitignored; this repo is public. It holds your notification credentials.
- **Never lose it.** Restore it before starting ntfy anywhere else, or every client 403s.
- It's a single portable SQLite DB, so it moves cleanly — just make sure it actually moves.

Back it up: `cp ntfy/data/lib/user.db <somewhere-safe>/user.db.$(date +%F)`

Manage users/ACLs through the container:
```bash
docker exec -it ntfy ntfy user list
docker exec -it ntfy ntfy access <user> <topic> rw
```

## Config

| Env | Default | Notes |
|---|---|---|
| `NTFY_PUBLIC_URL` | `https://ntfy.kingdomofluna.com` | what the server advertises to clients |
| `NTFY_DOMAIN` | `ntfy.kingdomofluna.com` | Traefik `Host()` rule |
| `NTFY_PORT` | `8095` | host + LAN access |
| `NTFY_DATA` | `./ntfy/data` | `lib/` (user.db) + `cache/` |
| `NTFY_URL` | `http://ntfy` | **internal** URL other containers push to (no public round-trip) |
| `NTFY_TOPIC` / `NTFY_TOKEN` | — | what agent-hub services publish to |

## Publishing from a service

Use the **JSON body** API, not the header API — HTTP headers are Latin-1 only, so a title with an
emoji or em-dash throws `Cannot convert argument to a ByteString` and the push is *silently dropped*:

```jsonc
POST http://ntfy/          // Authorization: Bearer $NTFY_TOKEN
{ "topic": "…", "title": "…", "message": "…", "click": "https://…", "tags": ["white_check_mark"] }
```
