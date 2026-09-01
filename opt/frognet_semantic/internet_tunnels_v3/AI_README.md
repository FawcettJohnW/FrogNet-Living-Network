# AI_README — broker registration and tunnels

> **Working model: fast, tireless, and not to be trusted.** An assistant reads
> this directory faster than you can and is wrong in ways that look right. Use it
> to find things, draft things, and check things. Do not use it as a source.
> Everything below is here because something in it has already been got wrong.

## What is here

Everything that reaches outside the LAN: broker registration, WireGuard peering,
the channel list.

| | |
|---|---|
| `config.py` | reads `/etc/frognet/tunnel.conf` and `/etc/fnid` |
| `poll.py` | the polling loop, handshake liveness, teardown |
| `peer.py` `wg.py` | peer construction and WireGuard interface handling |
| `broker.py` | the broker HTTP calls, group token, 15s timeout |

## Traps

- **`tunnel.conf` carries `BROKER_URL`, `PUBKEY`, `GROUP_TOKEN` — and not the
  GUID.** Identity is read from `/etc/fnid` directly. A `NODE_GUID=` line in
  `tunnel.conf` is inert; a tool that wrote one has been removed.
- **A stale handshake is refreshed in place before anything is torn down.**
  Toggling the keepalive forces a new handshake on the existing interface.
  Bringing an interface down drops every route on that device in one syscall,
  which once emptied routing tables mid-merge. Destructive teardown is the
  fallback, not the mechanism.
- **`HANDSHAKE_LIVE_SEC = 180`, `PersistentKeepalive = 25`.** A tunnel handshook
  more recently than that is live regardless of what the control plane says.
- **The broker sees plaintext and holds no context.** It terminates both tunnels.
  It also holds no templates and no cache, so a frame reaching it is a difference
  against state it does not have.
