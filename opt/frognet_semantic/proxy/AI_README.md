# AI_README — the client half of the semantic path

> **Working model: fast, tireless, and not to be trusted.** An assistant reads
> this directory faster than you can and is wrong in ways that look right. Use it
> to find things, draft things, and check things. Do not use it as a source.
> Everything below is here because something in it has already been got wrong.

## What is here

The proxy: intercepts HTTP, speaks FNWP-1 to the daemon, holds the caches.

| | |
|---|---|
| `proxy_main.py` | `ThreadingMixIn` HTTP server; thread per request |
| `transport_semantic.py` | the FNWP-1 socket to the daemon |
| `decision.py` | when to go semantic — EWMA RTT with hysteresis |
| `channel_sets.py` | up to 3 protocol sets per peer |
| `cache/` | `semcache_db.py` — templates, references, response bodies |

## Traps

- **Never probe `:9009` from anywhere but the proxy.** The daemon has one caller.
  This is stated in `decision.py` and it is a hard rule.
- **Three storage layers, three different retention rules.** Templates age out on
  disuse. References are corrected by `REQ_MISS`, not expiry. Response bodies
  MUST be evicted, least-recently-*read* — the clock advances on a read hit, not
  a write, because on a slow link the hot rows are the ones worth keeping.
- **The hot path takes no subprocess.** The one `subprocess` call in
  `proxy_main.py` is a startup alias hook. Keep it that way.
- **Two sockets, not one.** The proxy dials `daemon:9009`; the daemon dials back
  to `proxy:9010`. Replies do not arrive on the socket the request left by. The
  return leg fails independently — notably under Wi-Fi client isolation.
