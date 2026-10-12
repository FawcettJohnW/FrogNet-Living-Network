# AI_README — the tunnel daemon (region hub links, LAN-only)

> **Working model: fast, tireless, and not to be trusted.** An assistant reads
> this directory faster than you can and is wrong in ways that look right. Use it
> to find things, draft things, and check things. Do not use it as a source.
> Everything below is here because something in it has already been got wrong.

## What is here

Two modes and only two. **Region** (RINA_RAM set, normally by fdf-join in
`/opt/fdf/var/join.env`): one held FD&F region session, one WireGuard link per
hub, FD&F's plan applied per region event. **LAN-only** (no RINA_RAM): FrogNet
discovery and the databasehost election are runMerge's; no WireGuard at all.
There is no broker.

| | |
|---|---|
| `config.py` | the mode, `/etc/fnid`, the node's identity, its WireGuard key (`ensure_node_key`) |
| `poll.py` | `run_region_loop`, `run_lan_only_loop`, the hub-channel bring-up reconcile, handshake liveness, teardown |
| `region.py` | the region: this node's facts, the hub channels, mappings, events, the forward pond |
| `graph_apply.py` | FD&F's plan into the kernel (routes, default, hosts, names) |
| `wg.py` | WireGuard interface handling |

## Traps

- **`tunnel.conf` is optional and only `RINA_RAM` / `RINA_AREA` are read from it.**
  Any other key (an old broker enrolment's) is named once in the log and ignored.
  Identity is read from `/etc/fnid` directly.
- **The daemon makes its own WireGuard key** (`/var/lib/frognet-tunnel/node_private.key`)
  in region mode, before its first announcement, and logs the public half
  (`WG_KEY: made ...`). Nothing else makes it.
- **A stale handshake is refreshed in place before anything is torn down.**
  Toggling the keepalive forces a new handshake on the existing interface.
  Bringing an interface down drops every route on that device in one syscall,
  which once emptied routing tables mid-merge.
- **`HANDSHAKE_LIVE_SEC = 180`, `PersistentKeepalive = 25`.** A tunnel handshook
  more recently than that is live regardless of what the control plane says.
