# FD&F Technical Design Document

**System:** FD&F (Farinacci–Day–Fawcett): RINA naming, LISP mapping and Ribbit shared RAM as one network.
**Build described:** `fdf-2026-10-12-r20`; Appendix S, `fdf-2026-10-16-r21` (`fdf-source/rinab/src/fdf_version.hpp:4`), public repository `FawcettJohnW/FrogNet-Living-Network` at commit `989225c` (2026-10-14).
**Author:** Fawcett Innovations LLC. **Status:** technical design of the code as built, derived from the code. **This is very early code; the security model is designed but not yet fully implemented (§9).**

The main document (§1–§12) is the technical design: the FD&F client, then region mode. The reference appendices (A–O) hold the detail, with every statement traced to the code. The last four appendices (P–S) show additional capabilities: preloading a region with data, extending the RAM API and the semantic message handling, and John Day's DAF model built as a region.

**Contents**

1. Introduction
2. System overview
3. Design goals, constraints and non-goals
4. Architecture
5. Detailed design: the FD&F client
6. Detailed design: region mode
7. Interfaces
8. Data model
9. Security
10. Deployment
11. Verification
12. Open issues

*Reference appendices*
- A. How to read the reference appendices
- B. Overview in detail
- C. Requirements
- D. The memory (generic shared RAM)
- E. The RINA region
- F. Bootstrap and discovery
- G. Network events and failure semantics
- H. Wire protocol
- I. Region: FrogNet on FD&F, in detail
- J. Packaging, build and operations
- K. Clients and applications
- L. Tests and verification
- M. Traceability summary
- N. Known discrepancies, defects and open items
- O. Environment variables

*Additional capabilities*
- P. Preloading custom data
- Q. Examples of extending the RAM API
- R. Examples of extending semantic message handling
- S. John Day's DAF model on Ribbit

---

## 1. Introduction

### 1.1 Purpose

This document is the technical design of FD&F as built: what it is for, how it is put together, the interfaces and data it defines, its security model, how it is deployed, and how it is verified.

### 1.2 Scope

- **In scope:** the FD&F code under `opt/fdf`. That is the RAM manager (`rina-host`), the FD&F client, region mode (FrogNet on FD&F), and the Windows and Android members.
- **Out of scope:** FrogNet's own LAN discovery and routing (`opt/frognet_semantic`), and the Ribbit examples as products in their own right. Appendices P–R draw on those examples only to show what the platform under FD&F can do beyond what FD&F uses. One handler they describe (R.3) survives only as a reference copy.

### 1.3 How to read it

- **Main sections (§1–§12).** The design, in plain terms.
- **Appendices A–O.** The reference detail, with every statement traced to `path:line` in the code. Appendix A explains the conventions and status labels.
- **Appendices P–R.** Additional capabilities beyond what FD&F itself uses.

### 1.4 Terms

| Term | Meaning |
|---|---|
| RAM manager | `rina-host`: the process that holds the shared memory |
| Cell | One addressed item in the shared memory: `(service, variable, instance)` holding a JSON object |
| Session | One program's connection to the RAM manager (three TCP sockets) |
| Held read | A read that waits until something newer is written |
| Area | The name of an FD&F network; by name, it locates its RAM manager |
| Region / pond | A RAM manager and the network its members form; a machine may be in several ponds |
| Forward pond | The one pond that gives a machine its routes, mapping and hub peer |
| Leaf | A member that is not a FrogNet node; the region gives it an address |
| Site key | The region's 64-hex-character key; it signs mappings |
| DIF | Distributed IPC Facility (RINA): a layer of communication with its own members, naming and policy |
| IPCP | IPC process (RINA): one machine's member of a DIF |
| SDU | Service data unit: one message on a RINA flow |
| EID / RLOC | Endpoint identifier / routing locator (LISP): a name or prefix, and where it is reached |
| FNWP / FNW1 | FrogNet's wire protocol and its frame format |
| BLDC-1 | FrogNet's semantic compression: a template once, then only the changed fields |
| SotF | The Communicator's former media handler (Appendix R.3) |
| FNAV | The Communicator's current media bearer |

## 2. System overview

FD&F makes one network out of individual machines by giving them one shared memory.
- **Connections go up only.** Each machine opens one outbound connection to a RAM manager (`rina-host`).
- **No direct traffic.** Machines find each other by reading that memory and talk by writing to it.
  - **Stand-alone FD&F:** nothing goes machine to machine and nothing is routed. The network exists in the RAM.
  - **Region mode (§6):** control still goes only through the RAM. FrogNet's data between sites is routed: over WireGuard through the region host, on routes each node plans from the region's facts.
- **The parts.** The name says where they come from:
  - **RINA** (John Day) names things: DIFs, enrollment, the flow API.
  - **LISP** (Dino Farinacci) locates things: an area's name tells a machine where its RAM manager is.
  - **Ribbit** (John Fawcett) is the shared memory and the API to it.

It is not much code:
- the stand-alone FD&F source is about 3,600 lines;
- the LISP engine compiled into `rina-host` adds about 3,100;
- the C++ platform library and RAM server it runs on add about 4,700 more.

(Code lines only; Appendix B.3.)

### 2.1 Components

| Program | What it does |
|---|---|
| `rina-host` | The RAM manager: the shared memory, the RINA region and the mapping, in one process |
| `fdf-client` | A machine's membership: one session to the RAM manager. Linux and Windows. |
| `fdf-hub` | Region mode only: the region's WireGuard hub |
| `fdf-facts` | Region mode only: publishes a node's addresses, leases and uplinks |
| `rlite-uipcps`, `librina`, `rlite-ctl` | RINA programs (rlite's API) running on the shared memory |
| `fdf-participants`, `fdf-events`, `fdf-where`, `fdf-attention`, `ram`, `rina-lisp` | Small command-line tools over a session |

## 3. Design goals, constraints and non-goals

**Goals:**
- **One network from individual machines.** It must work on a LAN, across the Internet, or both.
- **One bootstrap configuration.** A machine is given only how to reach its RAM manager (and, in region mode, the key that admits it), once, and learns everything else from the shared memory.
- **Automatic recovery.** Joining, leaving and moving the RAM manager need no manual step.

**Constraints**, set by rulings that the code follows:
- **Control and membership only through the shared space.** "Communication between you and those others happens only through the shared space" (2026-10-07). In stand-alone FD&F that covers everything: nothing goes machine to machine. In region mode, FrogNet's data between sites rides WireGuard through the region host (§6), while membership, addresses, keys and mappings still go only through the shared space.
- **Liveness is the session.** There is no heartbeat, no ping, and no pid check.
- **Event-driven.** Nothing polls and nothing runs on a timer; every action follows an event. The few exceptions are listed in Appendix N.3.
- **No fallbacks.** A failed step is logged and retried on the next event. It is never replaced by a second mechanism.
- **Nobody types an address.** Anything that must be assigned, such as a leaf's address, is assigned by the RAM manager.
- **One deliverable per machine type.**

**Non-goals:**
- **Routing in FD&F itself.** Stand-alone FD&F routes nothing. In region mode the routed traffic is FrogNet's, carried over WireGuard; each node plans its own routes from the region's facts (§6, Appendix I.4).
- **Transactions or snapshot isolation** across cells. A region that needs them adds an operation in the RAM manager (Appendix Q).
- **An authorization system inside the RAM manager.** Permissions belong to the host and to the region's owner (§9.1).
- **A VPN.** Windows and Android devices join as full members (§6.5).

## 4. Architecture

### 4.1 Layers

| Layer | What it is |
|---|---|
| Applications | Programs and web pages that talk only through the shared memory: rlite programs, the Communicator, chat, the browser terminal, the kart demo |
| Ribbit | The RAM manager, its API, and the wire (FNWP with BLDC-1 semantic compression) |
| Transport (FD&F) | Locate, connect, announce, participants, capability, change. No definition carries a route, a path or a neighbour. |

### 4.2 The shared memory

Everything in FD&F is reads and writes on one memory. The model is small:
- **Cells.** A cell is addressed by three names, `(service, variable, instance)`, and holds a JSON object.
- **Write.** A write replaces the cell and returns a write-order id.
- **Read.** A read names any part of an address and returns every cell that matches.
- **Held read.** A read with `after=N` waits until something newer than id N is written, then returns it. This is how a program is notified: there is no separate message, and nothing polls.
- **Remove.** Removes a cell by id.
- **Following.** A reader that keeps `after` at the largest id it has received never skips a cell: it receives every cell written since, in id order, with no gaps, whether it follows one variable or a whole service. Each cell holds its latest value only, so a cell written several times before the reader reads it is received once, at its newest value; earlier values are not retained (§8.1).
- **Locking.** Each cell has its own lock. There are no transactions and no snapshot across cells; an application that needs more puts an operation in the RAM manager (Appendix Q).
- **Sessions.** Each program holds one session to the RAM manager. A session is three TCP connections: requests, replies, and a data socket for large bodies. Messages are semantically compressed: after the first, only what changed travels (Appendix H).

Appendix D has the exact semantics; Appendix E has the RINA region built on it.

### 4.3 Bootstrap

One bootstrap configuration, given once. Everything else it learns from the shared memory.

- **Direct and by-name modes:** a single fact, either where the RAM manager is or the area's name.
- **Region mode:** three values, given once to the installer: the region's endpoint, its area name, and the site key that admits the node and signs its mappings.

| Mode | What it is given | Start it with |
|---|---|---|
| **Direct** | where the RAM manager is: `RINA_RAM=host:port` | `fdf-ram-server.sh` on the RAM manager, `fdf-member.sh` on each machine |
| **By name** | the area's name: `RINA_AREA` | `fdf-mapping.sh` (the mapping), `fdf-ram-manager.sh` (the RAM manager), `fdf-member.sh` (each machine) |
| **Region** | the site key, `RAM_HOST:PORT` and the area | `fdf-region-create` on the region host; the node installer on each node (§6) |

**By name**, the area's name is turned into the address of its mapping system by a fixed rule: a hash of the name into 198.18.0.0/15. The mapping says where the RAM manager is now, so the manager can move and machines follow (Appendix F).

## 5. Detailed design: the FD&F client

### 5.1 Behaviour

`fdf-client` (`rinab/src/fdf_client.cpp`, about 400 lines) owns the machine's connection, so applications on the machine just read and write. In order:

1. **Connect.** It opens one session to the RAM manager.
2. **Alive.** It writes the machine's node record on that session. The RAM manager counts the machine alive exactly as long as the session lasts: there is no heartbeat.
3. **Announce.** It publishes the machine's name, IPv4 addresses and capability (cores, memory).
4. **Facts** (Linux). It publishes the machine's own facts from `/opt/fdf/var/facts.json`, and re-announces whenever that file changes. Other programs change the machine's facts by writing the file, never the region.
5. **Mappings.** For each entry in `facts.maps` it sends a LISP Map-Register, signed with the site key. A changed entry is re-registered; a dropped one is withdrawn. Mappings belong to the session, so they go when it goes.
6. **Hold.** It holds a read on its own input space until the session ends, then says why and exits.

**In region mode it also:**
- takes the machine's role in each pond: a node in its forward pond, a participant in the others;
- connects the machine as far into another pond as that pond allows (§6.2).

### 5.2 Failure handling

- **Detection.** A silent loss is detected in about 5 seconds, by TCP keepalive on the session at both ends.
- **Operating envelope.** The 5 s bound suits links where 5 seconds of silence means the member is gone. On lossy links it costs false deaths. Measured on an earlier build (Appendix G.2):

  | Packet loss | False deaths, 5 s bound | False deaths, 20 s bound |
  |---|---|---|
  | 10% | 14 | 5 |
  | 20% | 40 | 19 |

  Because the session is liveness, each false death removes everything bound to the session: the announcement, leaf address, mappings and hub peer. The member puts them back when it reconnects. On links with sustained heavy loss, that churn is the cost of fast detection.
- **The client.** It exits, saying why. systemd restarts it after 2 seconds, and it connects again.
- **The RINA agent** (`rlite-uipcps`) does not exit. It waits on the mapping for the RAM manager to register again, then re-attaches. Measured after a move, on an earlier build (2026-10-07, not re-run on r20): 73–310 ms, with no packets between machines (Appendix G.3).
- **Re-assertion.** After a reconnect each member writes back the records it owns. Nothing is reconstructed by anyone else.
- **Refusal is final.** A refused reach ends the client with exit code 4, which systemd does not restart.

### 5.3 Platforms

- **Linux** (x86-64 and aarch64): static binaries under `/opt/fdf`.
- **Windows:** `fdf-client.exe`, built static with mingw-w64.
- **Windows and Android as full members of a region:** the Windows bridge and the Android client (§6.5).

## 6. Detailed design: region mode

Region mode joins FrogNet networks to each other through one region host.
- **Control in the RAM.** Membership, addresses and keys are tuples in the region's RAM.
- **Data over WireGuard.** Data between sites rides WireGuard tunnels, hub and spoke, through the region host. FrogNet's paths inside a LAN stay direct.
- **No Internet needed.** A region on a LAN address works without the Internet.

### 6.1 Components

- **The region host** runs three services: `rina-host` (TCP 51888), `fdf-hub` (one WireGuard interface, UDP 50889), and its own `fdf-client`. One machine holds one region, and it is not itself a FrogNet node.
- **A node** adds `fdf-client` and `fdf-facts`. Its LAN database-host election is turned off while it is in the region.
- **Membership is the session.** When a node's session ends, everything bound to it goes, and the hub drops its peer on the next event.

### 6.2 Region modes

A region runs in one of three modes. The mode decides how far a machine can reach into the region's network when the region is not that machine's forward pond.

| Mode | Setting | What a participant gets |
|---|---|---|
| **RAM only (the default)** | `reach: none` | The shared memory and nothing else: no address, no tunnel, no route |
| **Restricted** | `reach: listed` | Only the endpoints the region's `where` cells name |
| **Open** | `reach: all` | Every /24 the region serves, and every /32 it has given |

The mode is set in `net.json` on the region host. The RAM manager publishes it as the `(pond, config, net)` tuple at start, and members cannot change it. With no file, the region is RAM only.

### 6.3 Lifecycle

| Step | Command | What happens |
|---|---|---|
| Create | `fdf-region-create AREA PUBLIC_IP` | Makes the site key (printed once), the three services, and the hub |
| Join | `sudo bash fdf-install-<arch>.run <SITE KEY> <RAM_HOST:PORT> <AREA>` | Installs, writes the join state, turns off the LAN election, starts the client and facts |
| More ponds | `fdf-pond-add`, `fdf-pond-remove`, `fdf-forward AREA` | A machine has one forward pond (routes, mapping, hub peer) and any number of participant ponds (memory only) |
| Leave | `fdf-leave` | Removes the FD&F services and state; the node goes back to LAN-only FrogNet and keeps its WireGuard key |

### 6.4 Addressing

- **Nodes keep their own address.** Each node chooses its own 10.X.Y.1 at install.
- **One owner per /24.** The RAM manager refuses an overlapping announcement, the hub keeps the first owner, and the route planner routes a duplicate to nobody.
- **Leaves are given an address.** A machine that is not a FrogNet node joins as a leaf and is given a 10.252.X.Y by the region. It never chooses its own.
- **Reserved ranges.** The hub and its edges use 10.253/16.

### 6.5 The Windows bridge and the Android client

A Windows PC or an Android phone joins a region as a full member of the live network. It is **not a VPN client** of a fixed server.

**What the device holds:**
- one session per pond, carrying its announcement, facts, presence, the shared memory and attention;
- a leaf address in its forward pond;
- reach into other ponds, as far as each pond's mode allows (§6.2).

**Where it sits.** It decides this from the address it already has:
- **On the mesh.** If its address is inside a live member's /24, it is directly on the mesh, with no tunnel.
- **A leaf.** Otherwise it is a leaf through the hub, and the region gives it a 10.252.X.Y.
- **Network changes.** When its default network changes, the session ends and it places itself again.

**How it is built:**
- **The member protocol** is plain Kotlin: the wire, the three-socket session, membership, hubs, placement, leaf, bridge. The same code runs on Android, on Windows, and on a JVM for the network tests.
- **`fdf-tun`** (Go, using wireguard-go) carries the packets: one TUN device, one WireGuard device per scope, and 1:1 address aliasing where ranges collide.
- **On Android**, the system `VpnService` is used only as the packet interface.

**Windows.** The bridge runs as Administrator, with a bundled Java 21 and the Wintun adapter (named "FrogNet"); `wintun.dll` is not included.
- **On John's hardware** (2026-10-11), it joined on the mesh from Seattle5's LAN.
- From off the mesh (192.168.0.2) it was given 10.252.0.1, and ping and ssh reached a node behind the hub.

**Android.** A handset joined and reached through the region on John's hardware, before the October cleanup. No APK is built or shipped yet.

**Measured in network tests** (netns, build r16, before the October cleanup):
- the leaf tunnel joined at +334 ms, with the first handshake at +437 ms;
- a killed leaf's peer was removed from the hub in 17–46 ms.

**Limitation, by design.** Addresses carried inside payloads (FTP, SIP/SDP, RTSP, H.323, embedded URLs) are not rewritten when aliasing. FrogNet applications name machines by identity (`w:<guid>`), not by address, so they are unaffected.

**Membership only.** A plain `fdf-client.exe` (§5.1) is also built for Windows. It gives a PC membership and the shared memory with no packet path.

Appendix K.1 has the sources, versions and test detail.

Appendix I has the details: the hub, the facts, the route planner, ponds and reach.

## 7. Interfaces

### 7.1 RAM API

One endpoint per RAM manager: `/FawcettInnovations.RINA.ram_interface.php` on FD&F hosts, `/ram.php` by default.

| op | What it does |
|---|---|
| `write` | Replaces a cell; returns its id |
| `read` | Every cell matching a partial address; with `after`, a held read (up to 30 s) |
| `remove` | Removes a cell by its current id |
| `version` | The server's version |
| `rina`, `rina_block` | RINA region operations: membership, announce, participants, mappings, flows (Appendix E.4) |
| `lisp` | LISP map-server operations |

Errors are `{"ok":false,"error":"…"}` with an HTTP-style status. Appendix D.2 has the arguments; Appendix E.4 lists the region operations.

### 7.2 Wire

- **Session.** Three TCP sockets per session, bound by a token chosen by the client:
  - **requests:** losing it ends the session;
  - **replies:** tagged by sequence number, sent in any order;
  - **data:** large bodies in 1,368-byte segments, interleaved so a large transfer never holds up a small one.
- **Frames.** Every frame is length-prefixed and starts `FNW1`. Requests are pipelined.
- **Compression.** After the first message of a shape, only changed fields travel. Identical messages travel as a repeat.
- **Keepalive.** On every socket, at both ends: a silent loss is detected in about 5 s.
- **HTTP.** The same port also answers a plain HTTP request.

Appendix H has the frame format and opcodes.

### 7.3 Ports

| Port | Use |
|---|---|
| TCP 51888 | Region RAM manager |
| UDP 50889 | Region hub (WireGuard) |
| TCP 8840 | RAM manager, direct and by-name modes |
| TCP 4342 | Mapping host (by-name mode) |

### 7.4 Command line

| Command | Purpose |
|---|---|
| `fdf-region-create AREA PUBLIC_IP` | Create a region on this host |
| `fdf-install-<arch>.run SITEKEY RAM:PORT AREA` | Install a node and join a region |
| `fdf-pond-add`, `fdf-pond-remove`, `fdf-forward`, `fdf-ponds` | Manage the ponds a machine is in |
| `fdf-leave` | Leave the region and return to LAN-only FrogNet |
| `fdf-participants`, `fdf-events`, `fdf-where`, `fdf-attention`, `ram` | Inspect and use the shared memory from a shell |
| `fdf-ram-server.sh`, `fdf-mapping.sh`, `fdf-ram-manager.sh`, `fdf-member.sh` | Stand-alone FD&F: direct and by-name modes |

## 8. Data model

### 8.1 Cells

A cell is `(service, variable, instance)` holding a JSON object, with:
- a write-order id;
- the time of its last update.

One cell per address holds the latest value only. A service whose name starts with `#` is private and never served over the network.

### 8.2 Region records

The RINA region keeps its own records, keyed `kind|…`.

| Kinds | What they hold | Lifetime |
|---|---|---|
| `ipcp`, `nreg`, `adj`, `app`, `flow` | the RINA objects | re-asserted by their owners |
| `node`, `ann`, `sess`, `proc`, `laddr`, `speer`, watches | machine presence, announcements, leaf addresses | the session: they go when it ends |
| `pol`, `par`, `site`, `map` | DIF policy and parameters, the area's site key, signed mappings | Permanent: kept in the region file across restarts |

Appendix E.3 has every kind, and Appendix E.7 what exists at startup.

### 8.3 Tuples and facts

| Tuple | Holds | Written by |
|---|---|---|
| `(pond, config, net)` | the region's mode (`reach`, `map`), its overlay ranges, and the server's build tag | the RAM manager only, at start, from `net.json` |
| `(pond, attention, <writer>)` | a notice to participants | any member; it goes when the writer's session ends |
| `(pond, where, <ns>)` | the endpoints a restricted region exposes | members |

**A node's facts** are announced by `fdf-client` from `facts.json`:
- **`facts.addrs`:** the node's addresses, each with its kind: `own`, `held`, `given`, `uplink` or `hub`.
- **`facts.maps`:** the prefixes it registers, each with its locators.

## 9. Security

**This is very early code, and the security model is not yet fully implemented.** §9.1 states the intended model. §9.2 records what the current code does, so that the gaps are visible.

### 9.1 The intended security model

**Principle.** FD&F owns no permission system of its own.
- The RAM manager does no authorization. It is an ordinary Linux process and inherits that machine's protection: file ownership, firewall, accounts.
- Finer policy belongs in the region owner's API, because each region is a vendor-owned piece of the address space with its own API (`PLAN-FROGNET-ON-FDF.md:257`).

**Where security lives.** "Security lives in enrollment (RINA), in signed registration (LISP), and in which memory a record lives in (Ribbit)" (`PLAN-FROGNET-ON-FDF.md:257-259`):
- **Enrollment (RINA).** Each DIF is a shared RAM. A machine sees a DIF's records only if it is enrolled in that DIF, so a monitor on a DIF sees only that DIF's machines.
- **Signed registration (LISP).** Identity-to-location mappings are accepted only when signed with the DIF's site key, and only when newer than the last.
- **Which memory (Ribbit).** A record's protection is the protection of the region it lives in. Separate concerns go in separate regions, each with its own RAM manager and key.

**The six contracts, kept distinct** (`PLAN-FROGNET-ON-FDF.md:261-287`):

| # | Contract | Status in r20 |
|---|---|---|
| 1 | **Enrollment bounds visibility.** A *restricted* DIF is its own RAM manager with its own area password. You see its records only if enrolled. This bounds what can be **seen**, not what can be reached. | Partial. Each pond is already a separate RAM manager with its own key. There is no "restricted" designation, and reading is not gated by enrollment. |
| 2 | **Authenticated Map-Register, verified Map-Notify.** HMAC-SHA-256 with the DIF's site key. A `map\|` row exists only if it came through the verifying `map_register` op. This proves "an enrolled member registered this", not ownership. | Implemented (`rina_region.cpp:457-473`; `lisp_area.hpp:84-100`) |
| 3 | **Prefix ownership is the authorization.** One live owner per /24. The hub installs AllowedIPs only from verified mappings, in DIFs it is enrolled in. | Implemented (`fdf-hub:13-19, 206-233`; hub_oracle) |
| 4 | **Selective publication upward.** A restricted DIF registers into a higher DIF only what it chooses: its hub, and the prefixes it accepts traffic for. Nothing else is mirrored. | Not implemented |
| 5 | **Forwarding limited to what was exposed.** The hub that advertises a restricted prefix upward forwards *only* the prefixes it exposed; a sibling region cannot reach more than was published. "Test it." | Not implemented; no test |
| 6 | **Encrypted bootstrap.** The one fact a machine is given (the area name, or where the RAM manager is) travels encrypted (John, 2026-10-07). | Deferred; the ruling stands |

**Exposure by design.**
- **Connections are up only.** Every member opens one outbound session and no inbound port, so a member behind a firewall or NAT exposes nothing.
- **Exposure is the operator's decision.** Whether a region host has a public address is up to whoever runs it. FD&F recommends keeping a region private where that matters, and a region runs on a LAN address with no Internet at all (R-A12).
- **A public region host exposes two ports:**
  - **The hub's WireGuard port (UDP).** WireGuard sends nothing in reply to a packet that lacks a valid handshake from a known key.
  - **The RAM manager's port (TCP).** This is the system's one attack surface. In the planned configuration it is secured by standard means (TLS) or by access controls the operator defines. **r20 has no access control on it** (§9.2).
- **Region modes limit reach.** RAM only, the default, gives a participant the shared memory and no path into the network. Restricted and open widen that deliberately (§I.7).
- **Leaf addresses are assigned by the region**, never chosen by the member.
- **Nesting and multiple membership** (a machine in several non-nested DIFs) are separate relationships. Each is bounded by its own enrollment (`PLAN-FROGNET-ON-FDF.md:289-302`).

### 9.2 As implemented in r20

| Area | Implemented | Source |
|---|---|---|
| Authorization | **None in the RAM manager, by ruling** (R-S1). It inherits the host's protection. Finer policy belongs in the region owner's API. | `PLAN-FROGNET-ON-FDF.md:257-259` |
| Site key | 64 hex characters from `/dev/urandom`, made once per region. Root-only file, `/opt/fdf/var` mode 700, printed once. | `fdf-region-create:48-57, 144-146` |
| Key on nodes | `site.env` mode 600. Pond env files umask 077. The key-free `ponds/<area>.ram` (0644) exists so pages can list ponds. | `fdf-region-join:23-24`; `fdf-pond-add:19-21`; `fdf_client.cpp:274-280` |
| Mapping authenticity | HMAC-SHA-256 over `name\|rloc\|ttl\|ts`. Strictly newer `ts` (anti-replay). EPERM on a bad key. The key never leaves the node. | `rina_region.cpp:457-473` |
| Ownership | One live owner per /24 (EEXIST). A live owner's mapping is not transferred. The hub installs only verified mappings naming it. "An ownership invariant, not a security property." | `rina_region.cpp:474-483, 593-608`; `fdf-hub:13-19`; `PLAN-FROGNET-ON-FDF.md:334-335` |
| Leaf addresses | Given by the region; never self-chosen. | `rina_region.cpp:622-623` |
| Region mode | RAM only by default. Restricted and open are chosen by the operator in `net.json`. The config is written only by `rina-host` at start and is read-only to the network. | `[NET_CONFIG_IS_THE_RAM_MANAGERS_V1]`; §I.7 |
| `map_site` | **No credential.** Anyone who reaches the RAM port can set an area's key; "the firewall is what keeps others out". | OPERATING:165; `rina_region.cpp:448` |
| Encryption in transit | Inter-site data rides WireGuard. **The FNWP sessions and HTTP front are plain TCP:** the bootstrap and all tuple traffic travel unencrypted. OpenSSL is used for SHA-256 and HMAC only. | `rina_region_client.hpp:16-17, 97, 147`; R-D9 |
| Resource exhaustion | Rows, variables and wait patterns are never freed, and a `remove` walks every row ever created. With no access control on the RAM port, anything that reaches it can grow the host's memory without bound. | §D.1 (Growth); `ram_memory.hpp:87-105, 137-148` |
| Session binding | The three sockets are bound by a client-chosen token. The server does not authenticate it, and logs it in the clear. | `ram_server.cpp:631-669` |
| Browser terminal | Through the RAM manager, as `/bin/login` on a PTY. `fdf-browser` on a non-loopback address has no authentication, and keystrokes cross unencrypted. | `fdf-source/fdf/bin/fdf-term-serve:2-37`; `opt/fdf/README.md:183-185` |
| Restricted DIFs | **Design only.** In practice each pond is a separate RAM manager with its own key. | R-S3 |
| Hardening in place | umask 022 on installs; validated sudoers fragment (www-data may run exactly `wg show all latest-handshakes`); reach executed with argv, never a shell; area names checked by pattern: `^[A-Za-z0-9._-]{1,12}$` (`fdf-region-create:35`), `^[A-Za-z0-9._-]+$` (`fdf-pond-add:17`), `^[A-Za-z0-9._-]{1,64}$` (`fdf-reach:56`). | `header.sh:12-14, 28-32`; `fdf_client.cpp:126-127` |

**Exposure in r20.** Besides the hub's WireGuard port (UDP 50889), `fdf-region-create` also opens the RAM manager's TCP port 51888 to members (`fdf-region-create:136-142`). Members reach the RAM manager at that address directly, not through the tunnel (`header.sh:2-6`). This is the planned configuration (§9.1), but r20 has no access control on it yet: anything that can reach the port can open a session.

The gap between §9.1 and this table is the work remaining: contracts 1, 4, 5 and 6; a credential on `map_site`; encryption of the session; the access controls on the RAM manager's port; and bounds on what a session can make the host keep. Security was also intentionally relaxed to get the Windows and Android members working, and a tightening list exists (John, 2026-10-13, *not in repo*).

## 10. Deployment

**Deliverables.** One file per machine type:

| File | For |
|---|---|
| `fdf-install-x86_64.run`, `fdf-install-aarch64.run` | region nodes |
| `fdf-droplet.tgz` | the region host |

Keys, the region file and join state are never shipped.

- **Layout.** Everything FD&F is under `/opt/fdf`, and machine state is in `/opt/fdf/var`.
- **Build.** `rinab/tools/build.sh` builds the RAM host, and `make_deliverables.sh` packages the deliverables. Everything is linked static, so one build runs on any supported Linux.
- **Versions.** Every program logs its build tag at start, so a log says what is deployed.

Appendix J has the build steps, units and logging.

## 11. Verification

| Test | What it shows | Status |
|---|---|---|
| Acceptance gates (netns and containers) | region loop, LAN-only, ponds, reach, install, upgrade, leave | Pass on this build (Appendix L.1) |
| `memory-order-check.sh` | a follower never skips a cell; region answers go to the session that asked | Pass (Appendix L.5) |
| rlite's own 30 integration tests, discovery and move checks | stand-alone FD&F: nothing between machines, name-only bootstrap, follow a moved RAM manager | Pass on the 2026-10-07 build; not yet re-run on this one (Appendix L.2) |
| `map-removal-check.sh` | a removed mapping stays removed across a RAM host restart; a session-bound mapping is not restored | Pass (Appendix L.5) |
| Hardware acceptance (T7) | the ten real-hardware steps | **Not run** (Appendix L.3) |

Never executed on this build: the arm64 installer (it ships), kernel WireGuard (the gates used wireguard-go), real Wi-Fi, a real phone, the Windows bridge on Windows, and a Communicator call. Earlier builds have hardware evidence for some of these (Appendix L.3).

Appendix C lists every requirement with its test, and Appendix N lists what is known to be open.

## 12. Open issues

Appendix N has the full list. The main open items:
- **Mapping lifetime.** The RAM manager's own mapping is registered once per listen with a TTL of 86,400 s and is not renewed. The TTL is checked on every read, so a manager up longer than a day drops out of the by-name mapping, and machines locating it by name can no longer find it.
- **Security** (§9.2):
  - enrollment-gated visibility, selective publication upward, and limited forwarding (contracts 1, 4 and 5);
  - an encrypted bootstrap and encrypted sessions;
  - a credential on `map_site`;
  - access control on the RAM manager's port.
- **Choosing a region mode** is not yet a `fdf-region-create` option, and the default `net.json` path needs a fix (Appendix N.1).
- **Not re-asserted.** `reg|` and `route|` records are not re-asserted after a host change.
- **Stale proof.** The stand-alone FD&F claims were last proven on the 2026-10-07 build (Appendix M).
- **Android.** No APK is built.

---

# Reference appendices

## Appendix A. How to read the reference appendices

**What they are.** Appendices A–O are the engineering reference for the FD&F code under `opt/fdf`: the detail behind §1–§12. They cover:
- requirements, architecture and interfaces;
- the memory semantics and the guarantees they provide;
- bootstrap and failure behaviour, and the wire protocol;
- deployment and security;
- the tests, what they prove, and where each claim stands.

**Every factual statement cites its source**, as `path:line` and, where the code carries one, the bracketed tag it logs or marks (for example `[SESSION_IS_LIVENESS_V1]`). A reader can check any sentence against the code.

**Path conventions.**
- `fdf-source/` = `opt/fdf/src/fdf-source/` in the repository.
- `rinab/` = `fdf-source/rinab/`.
- `docs/...` = the repository root's `docs/` (`OPERATING.md`, `cleanup-2026-10/`).
- `opt/frognet_semantic/...` = FrogNet's own tree, outside FD&F.

**Which source is the binary.** The shipped RAM host `rina-host` is built by `rinab/tools/build.sh`, which:
1. copies the vendored `rinab/platform/ribbit-v0.63`;
2. applies about 37 patch scripts (`rinab/tools/apply_*.py`);
3. splices in the region with `rinab/tools/make_host.sh`.

The checked-in `rinab/platform/ramsrv/` and `rinab/platform/ribbit_cpp/` are that patched result, with one difference. They lack `[NET_CONFIG_IS_THE_RAM_MANAGERS_V1]`, which the build adds: a write to `(pond, config, *)` is refused 403, and network removes skip those rows. This was verified by replaying the patch steps on a copy and diffing the result. Line numbers below are of the `platform/` files.

**Status labels used throughout.**

| Label | Meaning |
|---|---|
| **r20** | Shown on this build, in the October 2026 cleanup gates (netns, containers), 2026-10-13. |
| **HIST** | Shown on an earlier FD&F build (2026-10-05 to 10-09). Not re-run on r20. |
| **HW** | Reported from John's hardware. |
| **NOT RUN** | Specified, never executed. |
| **DESIGN** | Written down, not implemented. |

---

## Appendix B. Overview in detail

### B.1 What FD&F is

FD&F builds one logical network out of individual machines. Each machine holds one outbound session to a RAM manager. The machines find each other only through the shared memory that manager hosts:
- **RINA supplies naming:** DIFs, enrollment, the flow API.
- **LISP supplies identity-to-location mapping:** the area's name tells a machine where its RAM host is.
- **Ribbit supplies the programming surface:** a shared object store, reached by named operations.

The governing rulings:
- **Data through the shared space, never member to member (John, 2026-10-07).** "The list of participants in the shared space can only come from the shared space, and communication between you and those others happens only through the shared space." Recorded at `rinab/experiments/RESULTS.md:1040-1061` and `rinab/HANDOFF.md:20-23`. It superseded member-to-member data, the NAT relay link and the dedicated socket between neighbours (`RESULTS.md:1053-1060`); neighbour propagation was dropped later the same day under the "start over" ruling (`RESULTS.md:1088-1089`).
- **FD&F is a transport (R-A3, below):** "individual machines, each connected up to one RAM manager; discovery by capability announcement; LISP locates, RINA names; no routing, nothing between machines but through the RAM manager." Recorded at `rinab/HANDOFF.md:93-94`.

### B.2 The stack

| Layer | What it is | Where |
|---|---|---|
| Applications | Programs and web pages that talk only through the shared memory: rlite programs through librina, the Communicator, the kart demo, chat, the browser terminal | §I, §K |
| Ribbit | The RAM manager (memory and region), its API, the wire (FNWP, BLDC-1 semantic compression) | §D, §E, §H |
| Transport | FD&F: locate, connect, announce, participants, capability, change. "No definition carries a route, a path or a neighbour" (`rinab/src/transport.hpp:1-48`) | §F |

The transport interface is definitions only (`[TRANSPORT_INTERFACE_V1]`, `rinab/src/transport.hpp:1-48`). Its FD&F implementation is `rinab/src/fdf_transport.hpp`.

### B.3 Components

| Program | Role | Source |
|---|---|---|
| `rina-host` | The RAM manager: generic memory, RINA region and mapping region in one process | `rinab/platform/ramsrv/ram_server.cpp`, `rinab/src/rina_region.cpp`, `rinab/tools/make_host.sh` |
| `rlite-uipcps` | The machine agent: states the machine's devices, survives region loss, re-asserts what the machine owns | `rinab/src/rina_node.cpp` |
| `librina` | rlite's `rina/api.h` implemented on the region: one session per program, one held read | `rinab/src/librina_region.cpp` |
| `rlite-ctl` | rlite's control tool; its command logic runs inside the RAM host (`[CTL_AT_THE_MEMORY_V1]`) | `rinab/src/rlite_ctl.cpp`, `rinab/src/rlite_ctl_client.cpp`, `rinab/src/ctl_host.cpp` |
| `fdf-client` | Region member, Linux and Windows: alive, announce, facts, mappings, reach | `rinab/src/fdf_client.cpp` |
| `fdf-hub` | The region's WireGuard hub, derived from region records | `fdf-source/fdf/bin/fdf-hub` |
| `fdf-facts` | Publishes a node's addresses, leases and uplinks as facts | `fdf-source/fdf/bin/fdf-facts` |
| `fdf-events`, `fdf-participants`, `fdf-where`, `fdf-attention`, `ram`, `rina-lisp` | Small tools over the session | `rinab/src/fdf_*.cpp`, `rinab/src/ram_tool.cpp`, `rinab/src/rina_lisp.cpp` |
| `fdf_graph.py` | Pure route/exit/allowed-ips planner from region facts | `fdf-source/fdf/lib/python/fdf_graph.py` |

**Size.** Code lines only, with blank and comment-only lines excluded:
- stand-alone FD&F source (`rinab/src`): 3,644;
- C++ platform library and RAM server (`rinab/platform/ribbit_cpp` and `ramsrv`, without the LISP engine and generated tables): 4,660;
- the LISP engine port: a further 3,093;
- generated Unicode tables: a further 4,522 lines.

### B.4 Three ways to bootstrap

| Mode | What a machine is given | Programs | §F |
|---|---|---|---|
| Direct | `RINA_RAM=host:port` | `fdf-ram-server.sh`, `fdf-member.sh` | F.2a |
| Mapping (by name) | `RINA_AREA` (the area's name) | `fdf-mapping.sh`, `fdf-ram-manager.sh`, `fdf-member.sh` | F.2b |
| Region (FrogNet on FD&F) | site key, `RAM_HOST:PORT`, area | `fdf-region-create`, the node `.run`, `fdf-region-join` | F.2c, §I |

In every mode the machine is given one bootstrap configuration: one fact in direct and by-name modes, and in region mode the endpoint, area and site key, given once at install. Everything else it learns from the shared memory.

The highest-IP "control host" rule belongs to FrogNet's LAN mode, not to FD&F. It lives at `opt/frognet_semantic/discovery/hosts.py:361-383` and appears nowhere in the FD&F code; within `fdf-source` it is mentioned only in `docs/history`.

---

## Appendix C. Requirements

Each requirement is stated once, with:
- **Source:** the ruling or document it comes from.
- **Implemented:** where the code meets it.
- **Verified:** the test that shows it, if one exists.
- **Status:** the latest status (labels as in §A).

Rulings John stated that are not written in the repository are marked *(ruling, not in repo)*.

### C.1 Architecture

| ID | Requirement | Source | Implemented | Verified / status |
|---|---|---|---|---|
| R-A1 | Data travels through the shared space, never member to member. Connections are up only. The participant list comes only from the shared space. | John 2026-10-07; `rinab/HANDOFF.md:20-23`; `rinab/experiments/RESULTS.md:1041-1051` | `[DATA_THROUGH_THE_RAM_MANAGER_V1]`: each SDU is one posted `send` to the RAM manager (`rinab/src/librina_region.cpp:160, 483-489`). `[AGENT_IS_LOCAL_ONLY_V1]`: the agent carries nothing between machines (`rinab/src/rina_node.cpp:87-92, 131-139`). | `discovery-check.sh` (0 packets between machines) and `move-check.sh` (m2m=0). **HIST** 2026-10-07. Not re-run on r20. |
| R-A2 | One RAM manager reachable by all participants. Its scope may be one machine, a LAN or the Internet. | John 2026-10-07; `rinab/experiments/RESULTS.md:1076-1089` | §F | Gates 2a, G3 (region). **r20** |
| R-A3 | FD&F is a transport: individual machines, each connected up to one RAM manager. Discovery is by capability announcement. LISP locates, RINA names. No routing between machines. | `rinab/HANDOFF.md:93-94` | §F.3 | discovery-check. **HIST** |
| R-A4 | The transport interface holds definitions only; no definition carries a route, path or neighbour. | `rinab/experiments/RESULTS.md:1144-1146`; `rinab/src/transport.hpp:1-48` | `rinab/src/fdf_transport.hpp` | `transport-conformance`. **HIST** 2026-10-07 (binary not in the r20 build tree, `docs/cleanup-2026-10/T5.md:227-229`). |
| R-A5 | Don't copy rlite's messaging architecture. rlite's interface is the contract; the API, RAM server and schema behind it are free, but the schema stays tuple-shaped. | `rinab/HANDOFF.md:37-39` | §E | rlite acceptance suite 30/30. **HIST** 2026-10-07. |
| R-A6 | Region: data rides WireGuard hub-and-spoke through the RAM manager's host. Control is tuples and held reads. Nothing polls or heartbeats. Nothing goes node to node that isn't 10/8 data. | `fdf-source/docs/history/PLAN-FROGNET-ON-FDF.md:8-10, 350-351` | §I | Gates 2a, G3. **r20** |
| R-A7 | FD&F is the authority for membership and topology; FrogNet is the local configuration and forwarding-policy engine. No second discovery system: the crawl does not run in region mode. | `fdf-source/docs/history/PLAN-RAM-DISCOVERY.md:74-88` | `[STORE_UNITS_OFF_IN_REGION_V1]` (`fdf-source/fdf/bin/fdf-region-join:28-41`) | Gate 2a. **r20** |
| R-A8 | Two supported node modes, LAN-only and region. The region switch is one file (`/opt/fdf/var/join.env`). | `docs/cleanup-2026-10/T3a-BRIEF.md:5-6`; `[REGION_SWITCH_ONE_FILE_V1]` | `fdf-source/fdf/bin/fdf-region-join:7, 25-27`; `fdf-leave` | Gates 2a, 2c, G3, G5. **r20** |
| R-A9 | Nesting and multiple membership of DIFs are both allowed; nothing may assume a tree. | `fdf-source/docs/history/PLAN-FROGNET-ON-FDF.md:289-302` | Ponds: one forward pond, any number of participant ponds (§I.6) | Gate 2d (ponds), 2e (reach). **r20** (netns only) |
| R-A10 | One owner per /24 per region, decided by the lowest announce id. The hub never lets WireGuard's last write decide AllowedIPs. Owned beats transit. | `PLAN-FROGNET-ON-FDF.md:74-77, 97-103` | `fdf-source/fdf/bin/fdf-hub:16-19, 206-233`; `[PREFIX_OVERLAP_REFUSED_V1]` (`rinab/src/rina_region.cpp:593-608`) | `hub_oracle.py` cases 1, 2, 4. **r20** (27/27) |
| R-A11 | Deterministic addresses with no allocator: 10.A.B.0/24 maps to edge 10.253.A.B; the hub is 10.253.253.254/16. | `PLAN-FROGNET-ON-FDF.md:69-73` | `fdf-source/fdf/bin/fdf-hub:20, 150-155` | hub_oracle. **r20** |
| R-A12 | A region must work without the Internet; the RAM manager and hub can be a LAN machine. | `docs/cleanup-2026-10/T4a-BRIEF.md:52-53` | `fdf-region-create AREA <LAN-IP>` (`fdf-source/fdf/bin/fdf-region-create:6-8`) | "LAN region" rehearsal on x86 containers, one member. **r20**. Hardware: **NOT RUN** (T7 step 10). |
| R-A13 | Machine identity is a GUID, separate from its locator. | `rinab/STEP2-MACHINE-IDENTITY.md:5-35`; `[MACHINE_IDENTITY_V1]` | `rinab/src/machine_id.hpp:4-40` | rlite 30/30 after step 2. **HIST** |

### C.2 Bootstrap and discovery

| ID | Requirement | Source | Implemented | Verified / status |
|---|---|---|---|---|
| R-D1 | Hands off and completely automatic. The one thing a machine is given is the area's name. | John 2026-10-07; `rinab/experiments/RESULTS.md:1457-1467` | `[ONE_NAME_BOOTSTRAP_V1]` (`rinab/src/rina_region_client.hpp:101-141`) | `rule-check.sh`: joined=3, dns=0. **HIST** 2026-10-07. |
| R-D2 | The Map-Resolver address is derived from the name by a deterministic rule, with no DNS ("option B"). | John 2026-10-07; `rinab/experiments/RESULTS.md:1489-1500`; `rinab/experiments/DESIGN-MR-RULE.md` | `[MR_ADDRESS_FROM_NAME_V1]` (`rinab/src/rina_region_client.hpp:42-62`) | rule-check; C++ and Python agree on 5/5 names (`RESULTS.md:1497-1498`). **HIST** |
| R-D3 | The RAM manager registers its own mapping on every listen; nothing else registers it. | `rinab/experiments/RESULTS.md:1186-1193` | `[RAM_MANAGER_REGISTERS_ITSELF_V1]` (`rinab/src/rina_region.cpp:998-1015, 1056-1060`) | `selfreg-check.sh`. **HIST** |
| R-D4 | Members follow a moved RAM manager by waiting on the mapping, not by timer or neighbour propagation. | `rinab/experiments/RESULTS.md:1200-1237` | `[MAP_WATCH_V1]` (`rinab/src/rina_node.cpp:224-251`) | `mapwait-check.sh`, `move-check.sh`: re-attach 73–182 ms. **HIST** |
| R-D5 | No control messages over UDP. The LISP mapping lives in a region and is read and written over the session. UDP 4342 is used only at the interop edge and as the test oracle. | John 2026-10-06; `rinab/HANDOFF.md:27`; `rinab/src/rina_lisp.cpp:3-10` | `[AREA_MAPPING_IN_THE_REGION_V1]` (`rinab/src/rina_region.cpp:438-514`) | `map-region-check.sh` (0 UDP 4342), `map-oracle-check.sh` (7/7 agree with lispers.net). **HIST** |
| R-D6 | The area password (`RINA_AREA_PASSWORD`, 64 hex characters) is set only on the mapping machine and the RAM-manager candidates; members never read it. | John 2026-10-07; `RESULTS.md:1514-1529` | `fdf-source/fdf/bin/fdf-mapping.sh:7-8`, `fdf-ram-manager.sh:12-19` | Wrong password refused EPERM. **HIST** |
| R-D7 | Region joins: the RAM address and area are required arguments; there is no built-in region. | `docs/cleanup-2026-10/T4a-BRIEF.md:39-41` | `fdf-source/deliverables/header.sh:2-6, 16` | G3. **r20** |
| R-D8 | Discovery is rebuilt from the shared table on every participant (p) or mapping (m) event, never on a timer. A duplicate .1 is refused, naming the holder. A refused join waits for an event. | `PLAN-RAM-DISCOVERY.md:52-69, 95` | `[JOIN_REFUSED_WAITS_V1]` (`rinab/src/fdf_client.cpp:300-325`); `[PREFIX_OVERLAP_REFUSED_V1]` | Gate 2a. **r20**. See §N for the timers that remain. |
| R-D9 | The bootstrap should be encrypted. | John 2026-10-07 *(ruling, not in repo)*; in the repo: "Bootstrap in the clear: deferred, ruling stands" (`PLAN-FROGNET-ON-FDF.md:286-287, 354`) | **Not implemented.** The FNWP session and HTTP front are plain TCP (§9). | **DESIGN** |
| *(superseded)* | 2026-10-06 rulings: Map-Resolver chosen by capability probe; a lone machine is unity; Map-Resolver election by capability; neighbour propagation of a changed host. | `rinab/HANDOFF.md:25-34` (still listed there as "binding") | Removed 2026-10-07 (`RESULTS.md:1239-1268, 1603-1617`). `rina_elect.hpp` survives with no caller (§G.6). | Superseded by R-D2 and R-D4. |

### C.3 Memory semantics

| ID | Requirement | Source | Implemented | Verified / status |
|---|---|---|---|---|
| R-M1 | Tuple lifetime: a record may carry a Permanent flag (stored offline, restored at once on restart). All other records survive by re-assertion. | `rinab/HANDOFF.md:46-47`; `[PERMANENT_ENVELOPE_V1]` `[REGION_FILE_V1]` | `rinab/src/rina_region.cpp:42-91`; `rinab/src/ctl_host.hpp:24-27` | `region-file-check.sh`: policy survived kill -9, the IPCP did not. **HIST** |
| R-M2 | Every host needs `RINA_REGION_FILE`. With the variable unset the host does not start (a named file that does not yet exist is created). A damaged line or a non-durable write is fatal; fdatasync happens before publish. | `rinab/HANDOFF.md:31`; `RESULTS.md:411-416` | `rinab/src/rina_region.cpp:49-81` | region-file-check. **HIST** |
| R-M3 | Liveness is the session. Credit is state, not messages. | `rinab/HANDOFF.md:43`; `rinab/STEP1-DATA-PLANE.md:125-144` | `[SESSION_IS_LIVENESS_V1]`, `[CREDIT_IS_STATE_V1]` | rlite 30/30. **HIST** |
| R-M4 | One row per writer. Mapping rows are written only through `map_register`, HMAC-verified; a raw write is not a mapping. | `PLAN-FROGNET-ON-FDF.md:27, 270-274` | `rinab/src/rina_region.cpp:457-490` | `rinab/tests/map_prefix.cpp` (no dated result). |
| R-M5 | Ownership is checked on every reassert, all records before any write; EPERM names the offender. | `RESULTS.md:464-465, 587` | `rinab/src/rina_region.cpp:398-408, 413-436` | exp5b set. **HIST** (retired experiments) |
| R-M6 | A registration lives by its own TTL, checked when read. No timer, no reaper. Unknown or expired gives a negative answer, never a fallback. | `RESULTS.md:691-695` | `rinab/src/rina_region.cpp:491-493` | `map-expiry-compare.sh`: negative at 5.1 s, against 31.5–58.8 s for lispers.net. **HIST** |
| R-M7 | Notification is a write; waiting is a held read. No new mechanism anywhere. | `PLAN-FROGNET-ON-FDF.md:67-68, 248-249` | §D.5, §E.6 | — |
| R-M8 | The application is notified when its view changes. "The app never elects and never watches a clock." | `PLAN-FROGNET-ON-FDF.md:159-163` | `[LIBRARY_REASSERTS_V1]` type-4 notification (§G) | exp5b-late-program 6/6 after fix. **HIST** |
| R-M9 | A mapping's owner is not transferred while the holder lives. | `[MAP_OWNER_IS_NOT_TRANSFERRED_V1]` | `rinab/src/rina_region.cpp:474-483` | `rinab/tests/mapown_oracle.sh` (no dated result). |
| R-M10 | A reader that follows `after` never skips a cell: it receives every cell written since, at its latest value, in id order, on one variable or a whole service. Values overwritten before it reads are not retained. | §D.3 | `[PUBLISH_IN_ID_ORDER_V1]` `rinab/tools/apply_publish_order.py` | `memory-order-check.sh`: 0 missed (§L.5). **2026-10-15** |
| R-M11 | A region request is answered for the session that asked it. | §D.6 | `[REGION_COALESCE_PER_SESSION_V1]` `rinab/tools/apply_region_session_key.py` | `memory-order-check.sh`: 0 wrong (§L.5). **2026-10-15** |
| R-M12 | A removed mapping stays removed across a RAM host restart, and a session-bound mapping is never restored from the region file. | §E.2 | `[MAP_REMOVAL_IS_PERMANENT_V1]` `[OWNED_MAPPING_IS_NOT_RESTORED_V1]` (`rinab/src/rina_region.cpp:67, 164, 485, 498, 992`) | `map-removal-check.sh` (§L.5). **2026-10-15** |
| R-M13 | Identical requests in flight together give every caller the result of executing each. | §D.6 | `[HOST_COALESCE_V1]` (idempotent memory operations; unique ids on region operations that create) | `identical_writes.cpp` (§L.5). **2026-10-15** |

### C.4 Failure handling

| ID | Requirement | Source | Implemented | Verified / status |
|---|---|---|---|---|
| R-F1 | No fallbacks ("they mask real errors"). No timed re-asks, no heartbeats, no pings, no silent drops. | `rinab/HANDOFF.md:36` | Throughout. Exceptions are listed in §N.3. | `static-no-fallback.sh`: 0 violations. **r20** |
| R-F2 | A failed step is logged and retried on the next event, never replaced by a second mechanism. "Gone is gone." One mechanism per job. | `docs/cleanup-2026-10/T2-BRIEF.md:4-5` and later briefs | — | static-no-fallback. **r20** |
| R-F3 | Failures are logged, not swallowed. | `docs/cleanup-2026-10/T4b-BRIEF.md:24` | — | — |
| R-F4 | Silent loss of a session is detected by kernel TCP keepalive on the FNWP sockets at both ends. This is transport liveness, not an application heartbeat. The bound is **5 s** (`[KEEPALIVE_5S_V1]`, John 2026-10-10), superseding 20 s (`[KEEPALIVE_20S_V1]`, 2026-10-09). | `PLAN-FROGNET-ON-FDF.md:149-158`; `rinab/tools/apply_keepalive_5s.py:2-6` | `rinab/platform/ramsrv/ram_server.cpp:618-628`; `rinab/platform/ribbit_cpp/frogram.cpp:76-85` | Silent death to event on the 5 s build: 3,889 ms (C++ member), 3,860 ms (Kotlin member) (`opt/fdf/src/frognet-android/docs/ANDROID-HANDOFF.md:113-119`). **HIST** |
| R-F5 | On region loss the node recalculates immediately and tears down the region tunnel. No merge takes its place. | `PLAN-FROGNET-ON-FDF.md:142-148`; `[GONE_IS_GONE_V1]` | `opt/frognet_semantic/internet_tunnels_v3/poll.py:1593, 1629-1639` | G3 outage. **r20** |
| R-F6 | "Published ≠ reachable": a row present with its tunnel down gives no route and no name. | `PLAN-FROGNET-ON-FDF.md:189-194` | FrogNet daemon | — |
| R-F7 | A leaving member's peer is deleted at once: no parking, no timer. | `[HUB_HAS_NO_MEMORY_V1]` | `fdf-source/fdf/bin/fdf-hub:238-244` | hub_oracle case 5. **r20** |
| R-F8 | A client crash looks like a leave. A restart that shows as churn is a client bug, never a reason to add a timer. | `PLAN-FROGNET-ON-FDF.md:326-329` | — | — |
| R-F9 | A Map-Notify is not acceptance; its HMAC is verified with our key. | `[MAP_NOTIFY_MUST_BE_SIGNED_WITH_OUR_KEY_V1]`; `rinab/HANDOFF.md:130` | `rinab/src/lisp_area.hpp:84-100` | Against lispers.net. **HIST** |
| R-F10 | Start failures must be seen: `enable` then `start`, never `enable --now`. | `[START_FAILURE_IS_SEEN_V1]` | `fdf-source/fdf/bin/fdf-region-create:98-100`; `fdf-leave:29-34` | G3, G5. **r20** |

### C.5 Security

| ID | Requirement | Source | Implemented | Verified / status |
|---|---|---|---|---|
| R-S1 | The RAM manager does no authorization. It is an ordinary Linux process and inherits the machine's protection (file ownership, firewall, accounts); finer policy belongs to the region owner's API. | `PLAN-FROGNET-ON-FDF.md:257-259`; John 2026-10-08 *(ruling, not in repo)* | No authorization code in `rina-host` (§9) | — |
| R-S2 | Mapping registrations are authenticated (HMAC-SHA-256 with the site key) and replay-protected (strictly newer timestamp). | `PLAN-FROGNET-ON-FDF.md:255-287` | `rinab/src/rina_region.cpp:457-473` | Wrong key EPERM (`docs/cleanup-2026-10/FINAL-GATES.md:124-127`). **r20** |
| R-S3 | Restricted DIFs: a membership-gated area with its own RAM manager and password; mappings signed under the DIF's site key. | John 2026-10-09; `PLAN-FROGNET-ON-FDF.md:255-285` | Each pond is its own RAM manager with its own key (de facto). No "restricted" code exists. | **DESIGN** |
| R-S4 | Forwarding is limited to what was exposed ("Test it."). | `PLAN-FROGNET-ON-FDF.md` contract 5 | — | No test exists. **NOT RUN** |

### C.6 Performance

| ID | Requirement | Source | Implemented | Verified / status |
|---|---|---|---|---|
| R-P1 | Correctness over speed, then as fast as possible. Preallocate: high-water buffers, no allocation where the data's shape is known. | `rinab/HANDOFF.md:40-41`; `rinab/WIRE-REVIEW.md:617` | `[KNOWN_SHAPE_V1]`, `[HOST_ALLOCATOR_V1]`, steps 1–11 of `rinab/WIRE-REVIEW.md` | Host mallocs per transaction 854 → 335. **HIST** |
| R-P2 | Performance claims are given as measured side-by-side numbers. | John (standing) | `rinab/bench.sh` (FD&F against rlite) | No recorded `bench.sh` output (§L.4). |

### C.7 Packaging and operations

| ID | Requirement | Source | Implemented | Verified / status |
|---|---|---|---|---|
| R-O1 | Nothing stray in `/usr/local/bin`; node tarballs are staged in `/tmp` and untarred from `/`. | `PLAN-FROGNET-ON-FDF.md:353` | FD&F programs all live in `/opt/fdf/bin` (`fdf-source/fdf/bin/fdf-join:8`). The node `.run` does carry FrogNet's own existing tools into `/usr/local/bin` (§J.1). | G4, G5. **r20** |
| R-O2 | Exactly one file per machine type. | `docs/cleanup-2026-10/T4a-BRIEF.md:6` | `[ONE_TARBALL_PER_MACHINE_V1]` (`rinab/tools/make_deliverables.sh`) | Gate 5 (dist equals tree). **r20** |
| R-O3 | Every component logs its build; nobody runs a command to learn what is deployed. | `docs/cleanup-2026-10/T2-BRIEF.md:5`; `[FDF_VERSION_V1]` | §J.4 | Build consistency gate in `make_deliverables.sh:78-85`. **r20** |
| R-O4 | Commands never redirect output away from the terminal. | `docs/cleanup-2026-10/T4a-BRIEF.md:4-5` | `fdf-services.sh` writes to the terminal, no log files | `check-docs.sh`. **r20** |
| R-O5 | Docs contain no invented numbers, and every command runs as written. | `docs/cleanup-2026-10/T6a-BRIEF.md:5-6` | — | `check-docs.sh`: 0 problems. **r20** |

---

## Appendix D. The memory (generic shared RAM)

The RAM manager is one process that is both the wire endpoint and the memory (`rinab/platform/ramsrv/ram_server.cpp:8-16`). This section covers the generic memory any application uses. §E covers the RINA region's own records, which live in a separate structure.

### D.1 Data model

| Element | Definition | Source |
|---|---|---|
| Cell | `{id, service, variable, instance, bag, updated}` | `rinab/platform/ribbit_cpp/ram_memory.hpp:31` |
| Address | The triple (service, variable, instance): "a, b and c together identify one storage location (no hierarchy, no root)". All three must be non-empty strings on write. | `ram_memory.hpp:3-4`; `ram_server.cpp:250` |
| Value (bag) | A JSON object, stored as text re-serialised by the server. Integral numbers below 9e15 print as `%.0f`, others as `%.17g`; member order is kept. The stored bag is not byte-identical to what was sent. | `ram_server.cpp:101-111, 251-253` |
| id | One process-wide counter. A write takes `++next_id_`, and the cell's id becomes the new id, so the id names the latest *version*, not the address. Ids rise but are not dense: a failed compare-and-swap uses one up. | `ram_memory.hpp:128, 133, 220` |
| updated | Server wall-clock time when the write was handled, reported as `updated_epoch` (`%.3f`). | `ram_server.cpp:85, 135-137, 253` |
| Remove | Marks the row dead. A later write to the same address revives it with a fresh id. Rows are never unlinked or freed. | `ram_memory.hpp:11-13, 133, 144, 154` |
| Private services | A service named `#…` is the process's own; the network API refuses it with 403. | `ram_server.cpp:236-237, 252, 258` |
| Persistence | **None.** Memory only; "a restart is an empty memory". Ids restart from 1. (The region's Permanent records, §E.2, are a separate mechanism.) | `ram_server.cpp:29-30`; `ram_memory.hpp:220` |
| Growth | Rows, variables, services, wait patterns and triggers are never freed. A blocking read on an address never written creates the row (not live). The hash tables have fixed bucket counts: rows 65,536, variables 16,384, services 1,024, patterns 4,096. | `ram_memory.hpp:87-105, 206, 221-224` |

### D.2 Operations

The API path is `/ram.php` by default, or `--api PATH` (region hosts use `/FawcettInnovations.RINA.ram_interface.php`). The check is a prefix compare, and a mismatch gives 403 (`ram_server.cpp:146, 239`). Errors take the form `{"ok":false,"error":"…"}` with an HTTP-style status (`ram_server.cpp:140`).

| op | Method | Arguments | Reply | Source |
|---|---|---|---|---|
| `version` | any | — | `{"ok":true,"version":"v0.63-bounded"}` | `ram_server.cpp:241`; `version.hpp:5` |
| `write` | POST | body `{service, variable, instance, bag}` | `{"ok":true,"id":N}`. The write replaces the cell. | `ram_server.cpp:248-253` |
| `read` | GET | `service` (required); `variable`, `instance` (optional, empty = any); `after` (default −1); `fresh_s`; `wait_s` (capped at 30 s); `min_rows` | `{"ok":true,"rows":[{id,service,variable,instance,bag,updated_epoch}…]}`, sorted by id ascending. The HTTP front names the list `cells`. | `ram_server.cpp:257-271, 134-138, 611-612` |
| `remove` | DELETE | `id` | `{"ok":true,"removed":0\|1}`. Removes the row only if `id` is its *current* id. Cost is a walk of every row ever created. | `ram_server.cpp:274-276`; `ram_memory.hpp:137-148` |
| `lisp` | POST | `{operation, args}` | `{ok, result_json, written, cells_json}`. Runs a resident LISP engine operation; no rollback on error. | `ram_server.cpp:214-234` |
| `rina`, `rina_block` | POST | region body (§E) | Region answer. Added by `make_host.sh`. | `rinab/tools/make_host.sh:9-38` |

**Blocking reads (held reads).**
- A read with `after ≥ 0` and no `min_rows` gets `min_rows = 1`: "something newer than what I hold" (`ram_server.cpp:262`).
- The read holds only if `wait_s > 0 && min_rows > 0` and fewer than `min_rows` rows match (`ram_server.cpp:266`).
- At the deadline it answers `ok:true` with whatever matches, possibly nothing. There is no timeout flag (`ram_memory.hpp:216`).
- There is no polling interval: the waiter is woken by the write (`ram_server.cpp:25-27`).

### D.3 Ordering and ids

1. **One write is ordered at the one copy.**
   - A write takes its row's exclusive lock.
   - Inside the lock it claims the variable's next window position, takes the next id, and sets id, bag, updated and live together.
   - It releases the lock, then fires the triggers (`ram_memory.hpp:126-134`).
   - The reply carrying the id is sent only after the write is visible (`ram_server.cpp:253`).
2. **Ids are unique and increase in the order they are taken.** Writes to different rows run in parallel, each under its own row's lock, so ids are taken in parallel too.
3. **A read with `after` returns a gap-free prefix in id order, on any scope** (`[PUBLISH_IN_ID_ORDER_V1]`, `rinab/tools/apply_publish_order.py`).
   - The memory keeps a watermark: the largest id at or below which every id is visible.
   - A finished write marks its id done in a ring of 65,536 entries. It then moves the watermark over every consecutive done id it finds, whoever wrote them. No write waits for another.
   - A read with `after` loads the watermark before it scans and returns only ids at or below it. A newer cell is returned by the next read.
   - So a reader that sets `after` to the largest id it received never skips a cell, whether it reads one variable, a whole service, or a variable more than 1,024 writes behind. It receives each cell at its latest value: a value overwritten before the read is not retained (one row per address, §D.4).
   - Reading one variable within its write window (`[WRITE_WINDOW_V1]`, 1,024 slots) walks the window newest to oldest and stops at a slot still being filled (`ram_memory.hpp:161-191`).
4. **Callers that follow a whole service** by `after`: `fdf-monitor` and `fdf-browser` (`term.hosts`).
5. **One session's requests run in arrival order, one at a time** (`[SESSION_ORDER_V1]`, `ram_server.cpp:376-389, 455-475`). There are four exceptions:
   - a request that must park (a held read, or `lisp`) moves to its own thread and the queue continues;
   - a request whose body arrives on the data socket runs when the body lands;
   - a region request whose answer is deferred (`[DEFERRED_ANSWER_V1]`) releases the queue before its answer exists (`ram_server.cpp:530-538, 560-562`);
   - a coalesced request gets its leader's answer (§D.6).

### D.4 Atomicity and isolation: the guarantees

| Property | FD&F memory | Source |
|---|---|---|
| Atomic unit | One cell. A reader never sees a half-written cell. | `ram_memory.hpp:133, 153-158` |
| Locking | Each row has one `shared_mutex`: many readers or one writer. "There is no other lock on the memory." The index structures are lock-free, insert-only lists published by compare-and-swap. | `ram_memory.hpp:1-15, 61` |
| Multi-cell atomicity | **None** in the generic memory. No API takes more than one row lock. | `ram_server.cpp:155-157` |
| Isolation, snapshot | **None.** A multi-row read copies each row under its own lock at a different moment. | `ram_memory.hpp:152-159, 194-197` |
| Single-user illusion, two-phase commit | Not provided, by design. | — |
| Intermediate versions | Not retained: one row per address, latest version only. | `ram_memory.hpp:59-67` |

### D.5 Wakes

- A wait on an exact address hangs a trigger on that row. A wait with free coordinates hangs on a pattern keyed by its fixed coordinates (`ram_memory.hpp:3-7, 110-112, 206`).
- Every write and every remove fires the row's own triggers and the six pattern keys `s.*.*`, `*.v.*`, `*.*.i`, `s.v.*`, `s.*.i`, `*.v.i` (`[FIRE_KEYS_ONCE_V1]`, `ram_memory.hpp:233-235, 264-268`).
- No wake is missed: the trigger is registered before the first evaluation (`ram_memory.hpp:202-208`).
- A write's triggers fire once the watermark (§D.3) has passed its id. The write fires them itself if the watermark is already past; otherwise the write that moves the watermark past it fires them. A woken read can therefore always return the cell that woke it (`[PUBLISH_IN_ID_ORDER_V1]`).
- Any matching write wakes the waiter, which re-evaluates its whole query (`ram_memory.hpp:209-216`).
- Each parked read costs one detached thread for its wait (`ram_server.cpp:564, 594`).

### D.6 Coalescing: identical concurrent requests execute once

- **Host side** (`[HOST_COALESCE_V1]`, `ram_server.cpp:476-523, 550-559`). On the engine path, a request is keyed by a 16-byte hash of its full content (`fnwp_engine.hpp:15, 251`; `fnwp_client.hpp:26-32`). Identical requests in flight from **any** session join the first and receive its answer. After the leader answers, the entry is removed and later identical requests run themselves.
- **Client side** (`[CLIENT_COALESCES_V1]`, `frogram.cpp:592-612`). The client does the same within one session.
- **Consequence.** Two byte-identical writes that overlap in time may execute once, and both callers then receive the same id. The same holds for removes and held reads. Applications that need two identical writes recorded as two must make the requests differ. **Tested** by `tests/identical_writes.cpp` (§L.5): 16 sessions writing the same body 500 times each. 229 of 8,000 calls shared an id, every call was answered, and the memory held one cell with the value written.
- **Why this is safe for FD&F.**
  - The memory's operations are idempotent: a write replaces the cell, so one write of a value has the same result as two. A remove names the cell's current id, so it removes once either way.
  - Every region operation that creates something carries a fresh id, so two calls are never byte-identical:
    - `send` carries a per-SDU sequence number, `"q"` (`[FLAT_SEND_V1]`, `librina_region.cpp:485, 513`);
    - `alloc` carries a new flow id from `uniq()` (`librina_region.cpp:613, 620-622`);
    - `register` carries a new inbox id (`librina_region.cpp:549, 559-560`);
    - `fork` names its child process, and `accept` names the flow it answers (`librina_region.cpp:282, 538`).
  - The remaining region operations (`hello`, `node`, `announce`, the watches, the reads) state a value, so running one of two identical calls has the same result as running both.
  - Region requests coalesce only within one session (below).
  - A region whose operations are not idempotent must make its requests unique. The Redis example puts each connection's id and token in every request (`examples/redis/src/front.cpp:367-368, 472`), and its host does no host-side coalescing: its build applies no `[HOST_COALESCE_V1]` patch, and the tag appears nowhere under `examples/redis/` (§Q.6).
- **Region requests (`op=rina`, `op=rina_block`) are coalesced within one session only** (`[REGION_COALESCE_PER_SESSION_V1]`, `rinab/tools/apply_region_session_key.py`). The region answers from the caller's session, so the key carries the session:
  - `seen` (the address the session arrives from);
  - `register`, `mwatch`, `pwatch`;
  - `map_register` with ownership and `map_remove` (allowed only for the owning session);
  - `send`, whose peer is looked up from the sender.

  Memory requests stay coalesced across sessions ("one execution per request in flight, across sessions", `rinab/tools/apply_host_coalesce.py:1-15`).
- Counters are printed at exit when `RINA_DIAG` is set (`ram_server.cpp:520-523`).

### D.7 Refusals ("NO FALLBACKS", `ram_server.cpp:31`)

| Condition | Result |
|---|---|
| Path not under the API path | 403 |
| Unknown op | 400 |
| Wrong method | 405 |
| `write`: bad JSON, missing coordinate, bag not an object | 400 |
| `write`: private service | 403 |
| `write`: `(pond, config)` (build patch) | 403 |
| `remove`: missing id | 400 |
| `lisp`: any exception, including an attempted blocking read | 400 |

All from `ram_server.cpp:239-278`.

*Not refused (§N):* non-numeric `after`, `fresh_s`, `wait_s` and `min_rows` are read as 0; `wait_s` above 30 is clamped; trailing bytes after a JSON body are ignored (`ram_server.cpp:259-261`; `frogram.cpp:153-183`).

---

## Appendix E. The RINA region

The region is resident in the RAM host: the platform's C++ RAM server (v0.63) with FD&F's build patches and the region compiled in (`rinab/tools/build.sh`; `rinab/src/rina_region.cpp:1-3`).
- Every conversation a program, `rlite-ctl` or an agent has with it is **one call**.
- A call that waits is a held read, answered when its answer exists; "nothing is ever re-asked".
- A participant is alive while its session is up (`[SESSION_IS_LIVENESS_V1]`, `rina_region.cpp:4`).

### E.1 Storage

- **Separate store.** Control records live in the region's own memory (`rinab/src/rina_memory.hpp`), not the generic memory (`[RINA_RECORDS_V1]`, `rina_region.cpp:38-40`). That store has no locks:
  - values are immutable and published by exchange;
  - held reads sleep on futex wait words;
  - memory is reclaimed by epoch (`rina_memory.hpp:1-52`).
- **Record values** are `{"v":"k=v;k=v;…"}`. An empty value means the record is gone (`rina_region.cpp:29-37, 130-138, 162-164`).
- **Input spaces.** Each program has one: events numbered from 1, many producers, one consumer, read as a gap-free prefix (`rina_memory.hpp:59-135`).
- **Flow directions.** Each holds a ring of 4,096 SDU slots with a receiver-published credit (`rina_memory.hpp:137-175`; `rina_region.cpp:231, 333-348`).

### E.2 Permanence `[REGION_FILE_V1]`

- **What is Permanent.** Records whose envelope ends `,"p":1}`.
- **How they are written.** Each is appended to the region file (`RINA_REGION_FILE`) and fdatasync'd **before** it is published (`rina_region.cpp:42-52, 76-81, 91`).
- **At start.** The file is replayed (last value wins, empty value deletes) and compacted. A damaged line is fatal (exit 2). A host with `RINA_REGION_FILE` unset exits 2; a named file that does not exist yet is created (`rina_region.cpp:49-75`).
- **Removals are Permanent too** (`[MAP_REMOVAL_IS_PERMANENT_V1]`). Removing a mapping, by `map_remove` or by its owning session ending, writes an empty value with the Permanent flag. That empty value is appended to the file, so the replay at start deletes the mapping (`rina_region.cpp:164, 498, 992`). Tested by `rinab/tools/map-removal-check.sh` (§L.5).
- **Session-bound mappings are not restored** (`[OWNED_MAPPING_IS_NOT_RESTORED_V1]`). A mapping registered with `own:true` is filed with an `own=1` mark, so it supersedes any older mapping of that name in the file. The replay at start drops it: the session it belonged to did not survive the restart, and its owner registers it again on reconnect. This holds even when the host stops without ending its sessions (`rina_region.cpp:67, 485`; same test, which kills the host with SIGKILL while a session-bound mapping is held).
- **Which kinds.** Only `pol|` and `par|` (a DIF's policy and parameters: "no member re-asserts them", `rinab/src/ctl_host.hpp:24-27`), `site|` and `map|` (`rina_region.cpp:448, 485`).
- **Everything else.** "Every other record lives only in memory and returns by re-assertion" (`rina_region.cpp:42-52`).

### E.3 Record schema

| Key | Value | Written by | Lifetime | Source |
|---|---|---|---|---|
| `ipcp\|<ns>\|<name>` | id, type, dif, mss, address, ttl, csum, sched, netdev, enroller, enrolled… | `rlite-ctl` for the machine | memory; re-asserted by the agent | `rinab/src/rlite_ctl.cpp:43-46, 242-255` |
| `reg\|<ns>\|<dif>\|<appl>` | ipcp=; appl= | `ipcp-register` | memory; **not** in the agent's re-assert set (§N) | `rlite_ctl.cpp:59-60, 332` |
| `nreg\|<lower dif>\|<ipcp>` | ns= | `ipcp-register` | memory; re-asserted | `rlite_ctl.cpp:67-69` |
| `adj\|<dif>\|<a>\|<b>` | lower=; ans=; bns= | `ipcp-enroll` | memory; re-asserted | `rlite_ctl.cpp:70-71, 168, 176` |
| `route\|<ns>\|<ipcp>\|<dest>` | next hops | `ipcp-route-add` | memory; not re-asserted (§N) | `rlite_ctl.cpp:187-201` |
| `pol\|<dif>\|<component>` | policy | `dif-policy-mod` | **Permanent** | `ctl_host.hpp:24-27` |
| `par\|<dif>\|<component>\|<param>` | value | `dif-policy-param-mod` | **Permanent** | same |
| `aclaim:<dif>\|<ipcp>` | {v, ns, ep} | address derivation (§E.6) | memory; counts while the machine's epoch is current | `rlite_ctl.cpp:78-130` |
| `node\|<ns>` | epoch=<session>; devices | `node` op | memory; cleared when that session ends, if the epoch still matches | `rina_region.cpp:706-712, 974-975` |
| `sess\|<sid>` | proc:/pdata:/node: | `hello`, `node` | cleared at session end | `rina_region.cpp:288-292` |
| `speer\|<sid>` | address the session arrives from | server | cleared at session end | `rina_region.cpp:1064-1065` |
| `proc\|<id>` | `1;ns=…;` or `fork` | `hello`, `fork` | cleared at session end | `rina_region.cpp:570, 577, 959` |
| `ibh\|<inbox>\|<proc>`, `held:<proc>\|<inbox>` | 1 | per holder | memory; the newest holder receives | `rina_region.cpp:263-287` |
| `app\|<dif>\|<appl>` | inbox, ipcp, ns, type, ip, pid | `register` | memory; re-asserted by the library; erased lazily when unheld | `rina_region.cpp:726-727, 283-287` |
| `flow\|<dif>\|<fid>`, `fl\|<fid>`, `pfl:<proc>\|<fid>` | flow ends | `accept` | memory; re-asserted by the library | `rina_region.cpp:771-774` |
| `dropped\|<fid>\|<proc>` | reason | `send` | memory ("recorded, never silent") | `rina_region.cpp:318, 796` |
| `ann\|<ns>` | {ns, name, ips, cap, facts} | `announce` | cleared when the node's session ends | `rina_region.cpp:642-643, 980` |
| `laddr\|<10.252.x.y>` | session | `announce` (leaf) | freed at session end | `rina_region.cpp:625-635, 990` |
| `pwatch\|<proc>`, `mwatch\|<name>\|<proc>` | session | watch ops | dropped at session end | `rina_region.cpp:650, 455, 988-989` |
| `site\|<area>` | site key | `map_site` | **Permanent** | `rina_region.cpp:448` |
| `map\|<name or prefix>` | rloc=; exp=; ts=; site= | `map_register` | **Permanent** until its own expiry (checked on read); removed with its owning session if `own:true` | `rina_region.cpp:485, 491-492, 991-992` |
| `mown\|<name>` | owning session | `map_register` | memory | `rina_region.cpp:486` |

Generic-memory tuples the region manages:
- `(pond, attention, ns)` and `(pond, where, ns)` are removed when the node's session ends (`rina_region.cpp:981-984`).
- `(pond, config, net)` is written once at start (§I.7).

**Ownership for re-assertion.**
- A machine owns `ipcp|<ns>|*`, `nreg|*|<its ipcp>`, and `adj` rows where it is `ans` or `bns`.
- A program owns the `app` rows whose inbox it holds, `fl` and `flow` rows where it is an end, and `pfl:<proc>`.
- Every record is checked before any is written (`rina_region.cpp:385-389, 401-405, 419-423, 433-435`).

### E.4 Region operations

Requests are POSTed as `{"op":"…",…}` to `?op=rina` (answered at once) or `?op=rina_block` (may be held) (`rinab/src/rina_region_client.hpp:1-3, 166-171`). An `ok:false` answer is data, not an exception (`[REGION_ANSWER_IS_DATA_V1]`, `rina_region_client.hpp:163-180`).

| op | Purpose | Errors | Source |
|---|---|---|---|
| `node` | The agent's statement of its machine; sets `node\|<ns>`. A new epoch runs `ctl node-rejoin`. Notifies agents and participant watchers. | — | `rina_region.cpp:706-719` |
| `announce` | Discovery: name, IPs, capability, facts. Gives a leaf its /32. | EINVAL, EEXIST (overlapping /24), ENOSPC | `rina_region.cpp:581-647` |
| `participants`, `world`, `resolve`, `version`, `seen` | Read the participant set (alive = node record non-empty); participants and mappings in one read; identity to address in this pond; build tag; the address a session arrives from | ENOENT, ENOADDR (resolve) | `rina_region.cpp:652-705, 515-520` |
| `watch_participants` | Register for `p` events | EINVAL | `rina_region.cpp:648-651` |
| `mine` / `reassert` | The agent's owned records, and writing them back | EINVAL, EPERM | `rina_region.cpp:378-408` |
| `pmine` / `preassert` | The program's owned records, and writing them back | EINVAL, EPERM | `rina_region.cpp:409-436` |
| `hello`, `fork` | Program liveness and inboxes; a forked child keeps the registrations | — | `rina_region.cpp:566-580` |
| `register`, `unregister` | Application registration in a DIF | ENXIO, ENOENT, NEEDIP, EADDRNOTAVAIL | `rina_region.cpp:720-729, 810-814` |
| `alloc`, `accept` | Flow allocation and verdict | NEEDIP, ENOENT, EHOSTUNREACH | `rina_region.cpp:730-779` |
| `send` | SDUs to the peer's input space (bytes or by reference) | peer gone: dropped and recorded | `rina_region.cpp:780-809` |
| `taken`, `taken_now`, `credit` (held) | Receiver's consumed count; sender's credit wait | `gone` | `rina_region.cpp:521-533, 926-942` |
| `wait` (held) | **The** held read: events after N, as text lines `seq\tevent` | — | `rina_region.cpp:307-309, 353-362, 815` |
| `ctl` | Runs an `rlite-ctl` command at the memory | rc, out, err | `rina_region.cpp:757-762` |
| `map_site`, `map_register`, `map_request`, `map_remove`, `map_list`, `map_watch` | LISP mapping in a region (§F.4) | ENOENT, EPERM, EALREADY, EEXIST | `rina_region.cpp:438-514` |
| `rmt_view`, `rmem`, `peek` | Debugging aids | — | `rina_region.cpp:534-565` |

**Events pushed into input spaces:**
- `t`: topology, to agents;
- `p`: participants changed;
- `m`: mapping changed;
- `r`: flow request;
- `v`: verdict;
- `s`: an SDU;
- `x`: gone.

(`rina_region.cpp:238, 243, 487, 751, 776, 806, 968`.) Parked waits are continuations answered by the next push; "no thread waits" (`[DEFERRED_ANSWER_V1]`, `rina_region.cpp:350-366`).

### E.5 Session end

`rina_session_down(sid)` runs once when a session's socket closes: "whoever it was is gone, at once" (`rina_region.cpp:951-996`).
- **A program's session:**
  - its proc record is cleared and its inbox holds released;
  - `x` is pushed and its input space ended, so every held read ends with the session (`[HELD_READS_END_WITH_THE_SESSION_V1]`);
  - its flow directions are ended and senders' credit waits answered.
- **A node's session**, only if the node record's epoch is still this session:
  - `node`, `ann`, and the node's `where` and `attention` tuples are cleared;
  - agents are notified.
- **Every session:** `sess`, `speer`, its watches, its leaf /32 and its owned mappings are cleared, and watchers are notified.
- **Not removed:** generic-memory cells written by a session (`ram_server.cpp`, `ram_memory.hpp`: nothing ties cells to sessions).

### E.6 Flows and SDUs

- **One conversation, one call.** Each program has one permanent control session and one held read (`rinab/src/librina_region.cpp:1-11`).
- **A second session for data.** SDUs and credit statements go on a data session (`[CHANNEL_SETS_V1]`, `librina_region.cpp:250-253, 316-324`).
- **Each SDU is one posted `send`** through the RAM manager, which writes it into the peer's input space (`[ONE_REQUEST_PER_SDU_V1]`, `[DATA_THROUGH_THE_RAM_MANAGER_V1]`, `librina_region.cpp:457-458, 483-489`).
- **SDU bytes** travel as a JSON string, one code point per byte: lossless, not base64 (`[BYTES_AS_TEXT_V1]`, `librina_region.cpp:56-68`).
- **Credit** (`[CREDIT_IS_STATE_V1]`):
  - The sender never has more than one ring (4,096 SDUs) unconsumed.
  - When it reaches that, it makes one held `credit` call; `gone` ends the flow.
  - The receiver publishes the contiguous count it has taken (`rina_region.cpp:333-348, 781-783`; `librina_region.cpp:171-182, 470-477`).
- **Delivery.** SDUs are handed to the program in sequence order, each exactly once (`[FLOW_ORDER_V1]`, `librina_region.cpp:406-440`).
- **Reliable flows** keep each SDU until the receiver states it took it, and re-send after a host change (§G.4). Unreliable flows make no guarantee across a host change.
- **Address derivation** (`[FDF_E2_DERIVED_ADDRESS_V1]`, `rlite_ctl.cpp:78-130`):
  - An IPCP's address is derived, not allocated: FNV-1a of (ipcp, dif, k), claimed by writing a claim cell.
  - The claim with the smallest write id owns the address.
  - Claims count only while the claimant machine's epoch is current (`[FDF_ADDRESS_RELEASE_V1]`).

### E.7 What exists at startup

A RAM host does not have to start empty. Below is everything FD&F's host holds before the first participant connects, in the order it appears; a region can also be preloaded with its owner's own data (§P). Everything else arrives by re-assertion once members connect (§G.4).

1. **The region store and its Permanent records**, before `main()` runs (`rina_region.cpp:126-128`, static initialisation).
   - The host reads `RINA_REGION_FILE`. If it is unset, the host exits 2.
   - It replays the file (the last value of each record wins; an empty value deletes it), rewrites it compacted, and opens it for appending (`rina_region.cpp:53-75`). A damaged line is fatal.
   - Restored: `pol|` and `par|` (each DIF's policy and parameters), `site|` (the area's site key) and `map|` (mappings, each until its own expiry, checked on read).
   - Logged: `[REGION_FILE_V1] N Permanent record(s) restored from <file>`.
2. **The generic memory: one cell.** It is not persisted, so it starts empty apart from the network's configuration. Once the host is listening, it writes `(pond, config, net)` (`[NET_CONFIG_IS_A_TUPLE_V1]`, `rina_region.cpp:1016-1052`):
   - **Source.** It is read from `RINA_NET_CONFIG` (default `/opt/fdf/etc/net.json`).
   - **Fields:**
     - `area`;
     - `reach` (`none`, `listed` or `all`);
     - `map` (`allowed` or `refused`);
     - the fixed overlay ranges: `hub` 10.253.253.254, `edges` 10.253.0.0/16, `leaves` 10.252.0.0/16;
     - `ram`, if the file names one;
     - `server`, the build tag.
   - **No file:** `reach none`, `map refused`, and the log says so. This is the RAM-only default (§I.7).
   - **A file that does not parse, or a value outside those lists:** nothing is published, the log says why, and outsiders' reaches fail.
   - **Protection.** Only the host itself writes this cell. The network API can neither write nor remove it (403, `[NET_CONFIG_IS_THE_RAM_MANAGERS_V1]`).
3. **Its own mapping** (`[RAM_MANAGER_REGISTERS_ITSELF_V1]`, `rina_region.cpp:997-1015`), registered as soon as the host is listening, on its own thread.
   - **When.** If `RINA_MAP_AREA` and `RINA_AREA_PASSWORD` are set, the host registers the area's name, mapped to the address it listens on, with every map-resolver of the area. The TTL is 86,400 s.
   - **No area set** (direct mode): nothing is registered, and nothing is logged.
   - **A password or map-resolver missing:** the log says what, and nothing is registered.
4. **The version line**: `[FDF_VERSION_V1] build <tag> listening on <address>` (`rina_region.cpp:1056-1058`).

**The mapping host** (`fdf-mapping.sh`) differs:
- It runs with `RINA_HOST_ROLE=mapping` and its own region file, `/opt/fdf/var/map.perm`.
- It listens on the address computed from the area's name (bound on `lo`), port 4342.
- It skips steps 2 and 3: it publishes no network configuration and registers nothing for itself.
- Once it accepts connections, the script creates the area's site with the area's password (`map-site`). That is a Permanent `site|` record, so on later starts it comes back from the file in step 1.

**Per session**, not at startup: when a session comes up, the server records the address it arrives from (`speer|<sid>`, `[SESSION_PEER_V1]`). All other session records are written by the session's own requests (§E.3) and cleared when it ends (§E.5).

---

## Appendix F. Bootstrap and discovery

### F.1 Locating the RAM manager

The client's locate order (`[ONE_NAME_BOOTSTRAP_V1]`, `[TRANSPORT_INTERFACE_V1]`, `rinab/src/rina_region_client.hpp:101-141`):

1. `RINA_RAM=host:port`, used as given.
2. Otherwise `RINA_AREA`, plus `:RINA_AREA_PORT` (default **8840**).
3. Otherwise this machine's agent is asked, on the abstract unix socket `\0rina-area`.
4. Otherwise: error, "is rlite-uipcps running?".

**Resolution by name.** When the host part is a name, the Map-Resolver is chosen in this order: an `@mr` suffix, then `RINA_MAP_RESOLVER`, then an address derived from the name. The name is then resolved by `map_request` to `mr:4342` over a TCP session. "LISP is the ONLY way the name is resolved — no hosts file, no DNS" (`rina_region_client.hpp:121-141`).

**Caching and namespace.**
- One lookup per process, forgotten on connect failure and before any re-attach (`[LOCATE_CACHED_V1]`, `rina_region_client.hpp:84-95`).
- The connection is made from `RINA_RAM_NETNS`, else from pid 1's network namespace (`rina_region_client.hpp:152-161`).

### F.2 The three modes

**a) Direct** (`[FDF_DIRECT_V1]`)
- `fdf-ram-server.sh` runs `rina-host --listen 0.0.0.0:8840` with no area and no password (`fdf-source/fdf/bin/fdf-ram-server.sh:2-8`).
- Members set `RINA_RAM` (`fdf-member.sh:9-12`). The host registers nothing (`[QUIET_DIRECT_V1]`, `rina_region.cpp:1004`).
- Members in direct mode do not follow a RAM server restart (`rinab/experiments/RESULTS.md:1553`).

**b) Mapping, by name.**
- **Mapping machine.** `fdf-mapping.sh`:
  - derives the Map-Resolver address from `RINA_AREA` and binds it on `lo` (anycast);
  - runs `map_site AREA PASSWORD`;
  - runs `rina-host` as `RINA_HOST_ROLE=mapping` on `MR:4342` with its own region file (`fdf-source/fdf/bin/fdf-mapping.sh:3-14`).
- **RAM manager.** `fdf-ram-manager.sh`:
  - listens on `IP:8840`, where `IP` is the source address the host uses to reach the Map-Resolver;
  - registers itself in the mapping on every listen (`[RAM_MANAGER_REGISTERS_ITSELF_V1]`, `rina_region.cpp:998-1015, 1056-1060`).
  - Moving the manager means stopping it and starting it elsewhere; "members follow by themselves" (`fdf-ram-manager.sh:5`).
- **Members.** `fdf-member.sh ROUTER` adds a route to 198.18.0.0/15 and runs the agent with only `RINA_AREA` (`fdf-member.sh:2-17`).

**c) Region (FrogNet on FD&F).** §I.
- The member program is `fdf-client`, given `RAM_HOST:PORT` (an IPv4 literal: the static client has no resolver) and the site key (`fdf-source/fdf/bin/fdf-join:7, 13-14`; `fdf-region-join:23-24`).
- The region's RAM port defaults to 51888 (`fdf-region-create:26`).

### F.3 The Map-Resolver address rule `[MR_ADDRESS_FROM_NAME_V1]`

Ruled by John on 2026-10-07 ("option B"): a deterministic rule, no DNS. Source: `rina_region_client.hpp:42-62`; `rinab/experiments/DESIGN-MR-RULE.md`.

- `h = SHA-256(area name)`.
- **IPv4:** 198.18.0.0/15 plus the first 17 bits of `h`. A last octet of .0 becomes .1; .255 becomes .254.
- **IPv6:** fd46:4446::/32 plus the first 96 bits. The prefix is a placeholder awaiting John's choice (`DESIGN-MR-RULE.md:7-16`).
- `RINA_MAP_RESOLVER` overrides the rule. The rule replaced the earlier DNS SRV design.
- Example: `rina-area.test` gives 198.18.82.28. The C++ and Python implementations agree on 5/5 names (`RESULTS.md:1497-1498`; `rinab/tools/mr-address.py`).
- Name length: 1–31 characters on the LISP wire (`rinab/src/lisp_area.hpp:50-53`). Region names are at most 12 characters, because `wg-<AREA>` is an interface name (`fdf-region-create:35`).

### F.4 Mapping semantics `[AREA_MAPPING_IN_THE_REGION_V1]`

LISP's mapping semantics run in a region over the session ("no control messages over UDP"), on port 4342 reused as TCP. Source: `rina_region.cpp:438-514`.

- **`map_site name key`.** Stores the site key, Permanent. **It takes no credential** (§9).
- **`map_register {name, site, rloc, ttl, ts, auth, own}`.**
  - `auth` is hex HMAC-SHA-256 of `name|rloc|ttl|ts` under the site key.
  - It is refused when:
    - the site is unknown: ENOENT;
    - the HMAC is bad: EPERM;
    - `ts` is not strictly newer: EALREADY (no replay);
    - `own:true` and a live mapping is bound to another live session: EEXIST, naming the holder.
  - `name` may be an EID prefix (`10.A.B.0/24`) and `rloc` a locator set `hub:prio:weight,…` (`[MAP_PREFIX_LOCATOR_SET_V1]`).
  - On success it pushes `m` to the watchers of that name and of `*`.
- **`map_request`** returns the rloc, or ENOENT if the name is unknown **or expired**. Expiry is computed when read; there is no reaper.
- **`map_watch name|*`.** Held, with no timer: "the re-registration is the event". It is dropped when the watcher's session ends.
- **The registration's own TTL.** The RAM manager registers with a TTL of 86,400 s, once per listen. Nothing re-registers it on a timer (§N).

**What is not used.**
- No LISP data-plane encapsulation is used anywhere (`rinab/lisp-mapping/lisp.config`: itr, etr and rtr are all `no`).
- lispers.net's UDP wire (Map-Register, Map-Request in ECM, Map-Reply) is implemented in `rinab/src/lisp_area.hpp` "for edge interop and the oracle only" (`rina_lisp.cpp:7-10`).
- The platform LISP engine is compiled into `rina-host` (`ram_server.cpp:34`) but FD&F's mapping path does not call it (`rinab/lisp-mapping/README.md:12-24`).

### F.5 Discovery `[FDF_DISCOVERY_V1]`

- **What the agent announces.** `announce {ns, name, ips, cap}`, where `ips` are the non-loopback IPv4 addresses and `cap` is FrogNet's capability probe output, verbatim (`rinab/src/rina_node.cpp:156-186`).
- **When.** On every attach, and on every netlink link or address change (`rina_node.cpp:312-326`).
- **Capability caching.** The probe runs once per agent process and its output is cached, so the time-varying fields it carries (load, temperatures, ts) stay as first measured. If the probe fails, the machine is attached but not announced (`rina_node.cpp:159-171`).
- **Liveness.** "Alive" means the machine's node record is non-empty, which means its session is up (`rina_region.cpp:200, 974-975`).
- **Participant changes** push `p` to every `pwatch` watcher (`rina_region.cpp:240-244`).
- **Probe fields.** cores, CPU MHz, architecture, model, CPU bench, memory, MySQL state, disk size, class, write rate and fsync, `lan_ip`, load averages, temperatures, ts. The probe refuses to publish if any measurement fails (`fdf-source/fdf/bin/frognet_capability_probe.sh:132-340`).

### F.6 Identity `[MACHINE_IDENTITY_V1]` `[MEMBER_IDENTITY_V1]`

**The agent and library identity.** Source: `rinab/src/machine_id.hpp:4-40`.
- `RINA_MACHINE_ID` if set. Otherwise `m:` plus `/etc/fnid` (FrogNet's GUID), else `/etc/machine-id`.
- On the GUID path, `/` plus the inode of the network namespace is appended, so that in the one-machine suite each namespace stands for a machine.
- The data-plane node number is FNV-1a of the identity (`rinab/src/rmt.hpp:35-38`).

**The region member identity (`fdf-client`).**
- The record key is `w:` plus `facts.guid`, else `w:` plus the name.
- The name is `FDF_NAME`, else `facts.name`, else the hostname (`rinab/src/fdf_client.cpp:282-294`).
- These are two identity schemes for two member programs (§N).

---

## Appendix G. Network events and failure semantics

### G.1 Principles

- Liveness is the session; there is no heartbeat and no pid check (R-M3, `rina_region.cpp:4`).
- A split or merge is an **event the application is told about**, never hidden.
- Each member is the authority for its own records and writes them back after a host change.
- The node agent has no timers; every action follows an event (`rina_node.cpp:1-4`). The exceptions elsewhere are listed in §N.3.

### G.2 Detecting loss: keepalive `[KEEPALIVE_5S_V1]`

Every FNWP socket, at both ends, sets:
- `SO_KEEPALIVE`, `TCP_KEEPIDLE` 2 s, `TCP_KEEPINTVL` 1 s, `TCP_KEEPCNT` 3;
- `TCP_USER_TIMEOUT` 5,000 ms;
- `TCP_NODELAY`.

So a silent loss is detected in about 5 s (`ram_server.cpp:618-628`; `frogram.cpp:76-85`; ruling John 2026-10-10, `rinab/tools/apply_keepalive_5s.py:2-6`). An expired session is reported as ETIMEDOUT "within the 5 s keepalive" (`[RECV_SAYS_WHY_V1]`, `frogram.cpp:105`).

Measured silent death to event, C++ and Kotlin members with the same options (`opt/fdf/src/frognet-android/docs/ANDROID-HANDOFF.md:113-119`, **HIST**):

| Build | C++ | Kotlin |
|---|---|---|
| 20 s keepalive | 19,306 ms | 19,162 ms |
| 5 s keepalive | 3,889 ms | 3,860 ms |

The same document records the cost of the shorter bound. False deaths at 10% packet loss were 5 on the 20 s build and 14 on the 5 s build. At 20% loss they were 19 and 40.

### G.3 The agent when the region goes `[AGENT_SURVIVES_THE_REGION_V1]` `[MAP_WATCH_V1]`

Source: `rinab/src/rina_node.cpp:224-267`.

1. The agent's one held read (`wait`, effectively unbounded) throws.
2. The agent clears its session, logs "the region is gone; waiting on the mapping for the RAM manager", and **does not exit**.
3. It opens a session to the Map-Resolver and sets `map_watch` on its area **before** each attach attempt, so a registration in between is not missed.
4. It forgets the cached location and attempts `attach()`. On failure it holds on the mapping until a registration event, then tries again.
5. If the mapping region itself is unreachable, the machine "stays detached" and logs it.

**Measured re-attach after a RAM manager move:** 73, 135, 128, 182 and 124 ms (move-check); 269–310 ms (rule-check). Zero packets between machines. **HIST** 2026-10-07 (`RESULTS.md:1228-1290, 1441, 1506, 1524, 1614`).

### G.4 Re-attaching and re-asserting

**The machine** (`[AGENT_REASSERTS_V1]`, `rina_node.cpp:151-155, 190-204`). `attach()` runs, in order:
1. a new session;
2. `reassert` of the machine's saved records;
3. `node`;
4. `announce`;
5. refresh of its saved copy (`mine`);
6. start of its held read;
7. a wake to every subscribed program.

**Programs** (`[LIBRARY_REASSERTS_V1]`, `librina_region.cpp:325-375`).
- Each program keeps a copy of what it owns (`pmine`), refreshed after every control call, never per SDU.
- When its held read throws, it subscribes to the agent with a type-5 PDU on the local socket.
- The agent answers with a type-4 PDU when the host is back; if the agent already holds a live session, it answers at once (`rina_node.cpp:205-223`).
- The program then reattaches: new session, `hello`, `preassert`, its data session dropped, its held read restarted.
- If re-attaching fails, it subscribes again.

**Reliable flows** (`[RESEND_ON_HOST_CHANGE_V1]`, `librina_region.cpp:494-519`). After the host changes, the program reads the receiver's `taken_now` count and re-sends kept SDUs from there; the receiver drops duplicates.

**What is lost on a host change** (`librina_region.cpp:494-496`; `rina_region.cpp:974-995`):
- route events pushed while the program had no session;
- the old host's record of what each receiver took;
- every session-bound row: node record, announcement, where and attention tuples, leaf /32, owned mappings, watches.

These come back by re-assertion and re-announcement, not by copying.

### G.5 Region mode: a node when the region goes

FrogNet's daemon, outside FD&F, cited for completeness (`opt/frognet_semantic/internet_tunnels_v3/poll.py`):
- Region loss is one transition (`[REGION_LOSS_IS_A_TRANSITION_V1]`): the phase is recalculated, every region route and name is withdrawn, and every kernel WireGuard interface is torn down. No merge takes their place (`[GONE_IS_GONE_V1]`) (`poll.py:1593, 1629-1639`).
- Rejoin is tried every 5 s, one call per try (`[REJOIN_EVERY_5S_V1]`, `poll.py:1643-1653`). On return the daemon sets its watch, then reads (`[WATCH_BEFORE_READ_V1]`, `poll.py:1599-1610`).
- `fdf-client` exits when its session ends, and systemd restarts it 2 s later. The fresh session re-announces and re-registers (`fdf-source/fdf/bin/fdf-join:38-40`; `fdf_client.cpp:403-408`).
- **Measured, r20 containers.** With the region host off the network for 49–52 s, traffic returned 15–40 s after reconnect with no manual step. With the RAM manager stopped for 40 s, `fdf-client` waited and re-joined (`docs/cleanup-2026-10/FINAL-GATES.md:78`; `T4a.md:181-182`; `T4b.md:231`).

### G.6 Elections

- **`rina_elect`** (`[MR_RANKED_BY_CAPABILITY_V1]`, `rinab/src/rina_elect.hpp`): the most cores wins, then the most memory, then the lowest identity. It is **dormant**: included by the agent but called by nothing except its unit test (`rinab/tests/elect_test.cpp`). The Map-Resolver election it served was replaced by the name rule (`RESULTS.md:1603-1612`), and the relay-router election went with the NAT relay (`RESULTS.md:1282-1286`).
- **The RAM manager is not elected in FD&F.** It is wherever it registers in the mapping (mode b) or wherever `fdf-region-create` was run (mode c).
- **Region-mode database host.** FrogNet's daemon elects it from the members' facts (`docs/OPERATING.md:48-49`).

### G.7 NAT

Every member holds one outbound TCP session to the RAM manager, and all data goes through it, so no relay is needed (`RESULTS.md:1057-1058`). The relay-router design (`rinab/experiments/DESIGN-NAT.md`) was removed. `seen` still reports the address a session arrives from (`rina_region.cpp:515-520`).

---

## Appendix H. Wire protocol

### H.1 Session setup

Every accepted socket first gets the keepalive options (§G.2). A socket whose first 4 bytes are `GET ` or `POST` is one plain HTTP request (`[HTTP_FRONT_V1]`, `ram_server.cpp:597-616, 629`). Otherwise it speaks FNW1, and the first frame must be HELLO (0x50) or the socket is closed (`ram_server.cpp:630`).

| Socket | HELLO text | Role | Source |
|---|---|---|---|
| Request | `<token>` | Carries requests. **Its loss ends the session.** | `ram_server.cpp:663-668` |
| Return | `RETURN:<token>` | Carries replies, sequence-tagged. The server sends HELLO `ram`. | `ram_server.cpp:632-637` |
| Data | `DATA:<token>` | Carries large payloads in segments. Opening it switches the session to the engine path. | `ram_server.cpp:638-661` |

- Pairing waits up to 10 s; a socket left unpaired is closed (`ram_server.cpp:633-641, 664`).
- The token is client-chosen and is the only binding between the three sockets (`frogram.cpp:342-343`).
- The C++ client always opens all three. A far end that does not answer DATA is refused: "it does not speak the full FNWP contract" (`frogram.cpp:341-360`).

### H.2 Framing and opcodes

- Every frame is `[len:4 BE]` followed by the frame, which begins `FNW1`. Lengths must be between 5 bytes and 1 MiB (`ram_server.cpp:82, 95`).
- **Requests carry no sequence number.** The server numbers them 0, 1, 2… as read (`ram_server.cpp:665, 668`).
- Replies are `[len][seq:4][frame]` and may leave in any order (`ram_server.cpp:413`).
- **Pipelining:** the client may send any number of requests without waiting.

Opcodes (`rinab/platform/ribbit_cpp/semwire.hpp:7-16, 42-46`):

| Opcode | Name |
|---|---|
| 0x01 | REQ_FULL |
| 0x02 | REQ_REPEAT |
| 0x03 | REQ_RAW |
| 0x04 | REQ_DIFF |
| 0x11 | RESP_DIFF |
| 0x13 | RESP_SAME |
| 0x14 | RESP_RAW |
| 0x21 | REQ_MISS |
| 0x22 | REQ_SAME_MISS |
| 0x30 | ERROR `[status:2][len:2][msg]` |
| 0x40 | SEQ_RESET |
| 0x50 | HELLO |
| 0x60–0x62 | RTT ping, pong, loop (answered "op not spoken here") |

### H.3 Semantic compression (engine path)

- **Templates.** Learned from RAW exchanges by both ends, per instance: (opcode, service, variable, instance) (`[INSTANCE_REFERENCES_V1]`, `fnwp_engine.hpp:8-24, 81-94`). They can also be primed from `RIBBIT_TEMPLATES` (`/opt/fdf/templates/fdf.prime`, "BLDC-1's FD&F dictionary") (`[PRIMED_TEMPLATES_V1]`, `fnwp_engine.hpp:101-137`).
- **Answers** go out as one of:
  - RESP_SAME, when the bytes equal the instance's last answer (`[SAME_AS_LAST_ANSWER_V1]`);
  - RESP_DIFF against the template;
  - RESP_RAW.

  Extraction is proven by rebuilding before it is trusted (`[REPLY_EXTRACTION_PROVEN_V1]`, `fnwp_engine.hpp:564-631`).
- **same_id** is minted from the session secret and a counter; it is not a hash of the answer (`[NO_ANSWER_HASH_V1]`, `fnwp_engine.hpp:500-503`).
- **The REPEAT cache** holds 4,096 entries per session, and bodies over 64 KiB are not cached. A client that missed a SAME asks with REQ_SAME_MISS rather than resend, because "a second send runs it a second time". An evicted answer gives ERROR 410, and the request is **not** run again (`fnwp_engine.hpp:366-372, 461-464, 495-511`).
- **Measured, HIST 2026-10-07** (`RESULTS.md:1342-1346, 1427-1431`):
  - data-path bytes reduced by 23%;
  - the C++ and Python encoders byte-identical on 605/605 frames;
  - a flat send is 87–88 B per frame.

### H.4 The data socket `[HIGH_SPEED_DATA_SOCKET_V1]` `[THE_CEILING_IS_A_TIME_NOT_A_SIZE_V1]`

Source: `rinab/platform/ribbit_cpp/dataplane.hpp:9-99`; `ram_server.cpp:430-432, 567-575, 642-661`.

- **Record format:** `[len:4][seq:4][total:4][offset:4][bytes]`. A segment is 1,368 bytes, "what fits one MTU after framing".
- **The ceiling.** A RAW payload goes whole on the return socket only if it drains within 20 ms at the socket's achieved rate. The ceiling is `clamp(rate × 20 ms, 1368, SO_SNDBUF(return)/2)`, and the rate is an exponential average with a 2 s time constant.
- **Above the ceiling.** The payload goes to the data socket. The return socket carries a **marker** in wire order, and the body follows under the same sequence number.
- **Interleaving.** One writer thread per session sends one segment per turn, round-robin, so N transfers in flight each get every Nth segment. A large transfer never holds up a small one.
- **Upload direction.** It works the same way: a REQ_RAW marker carries `data_bytes`, and the request runs when its body lands.
- **Failure scope.** A segment that does not fit its transfer is a protocol error that stops the data-socket reader. The data socket's loss does **not** end the session.

### H.5 Limits

| Limit | Value | Source |
|---|---|---|
| Frame size | 5 B – 1 MiB | `ram_server.cpp:82, 95` |
| Held read (generic memory) | 30 s maximum | `ram_server.cpp:83, 261` |
| Write window | 1,024 per variable | `ram_memory.hpp:76` |
| Host coalesce table | 4,096 slots, probe 32 | `ram_server.cpp:481, 491` |
| Publish ring | 65,536 ids. A write more than that far ahead of the watermark waits for it before marking. | `[PUBLISH_IN_ID_ORDER_V1]` |
| Work ring / pool | 65,536 cells; 4 × cores threads | `ram_server.cpp:291, 331` |
| Socket buffers | 32 KiB (listener RCVBUF, data SNDBUF, client SNDBUF) | `ram_server.cpp:695-696, 646`; `frogram.cpp:348, 356` |
| Listen backlog | 1,024 | `ram_server.cpp:698` |
| Client call timeout | 15 s (`read`: 15 s + `wait_s`) | `frogram.hpp:63-67`; `frogram.cpp:675` |
| Region flow ring | 4,096 SDUs; credit by quarters | `rina_memory.hpp:141, 159` |
| Region input-space segment | 1,024 events | `rina_memory.hpp:62` |
| Region tables | 65,536 slots | `rina_memory.hpp:192` |
| HELLO token | ≤ 255 B | `ram_server.cpp:631` |
| Ports | 8840 (direct and mapping RAM manager), 4342/TCP (mapping region), 51888/TCP (region RAM), 50889/UDP (region hub), 8799 (kart), 8800/8994 (Communicator) | §F, §I, §K |

---

## Appendix I. Region: FrogNet on FD&F, in detail

### I.1 Shape

- **A region host runs three units** (`docs/OPERATING.md:40-42`):
  - `fdf-ram-<AREA>`: `rina-host` on TCP 51888, region file `/opt/fdf/var/area-<AREA>.perm` (`fdf-region-create:68`);
  - `fdf-hub`: one WireGuard interface `wg-<AREA>` on UDP 50889;
  - `fdf-client-<AREA>`: the hub's own membership.
- **A node in a region adds** `fdf-client` and `fdf-facts`, and its database-host election timer is turned off.
- **The switch is one file**, `/opt/fdf/var/join.env` (`[REGION_SWITCH_ONE_FILE_V1]`, OPERATING:25-26, 37).
- **Membership is the session.** Data between sites rides WireGuard, hub-and-spoke, through the region host. FrogNet's LAN paths stay direct (OPERATING:17-23).
- **Three region modes:** RAM only (the default), restricted, and open. They set how far a participant can reach into the region's network (§I.7).
- **The region host is not a FrogNet node.** One machine holds one region. A LAN address works, so a region needs no Internet (OPERATING:51-52; `fdf-region-create:6-8, 44-47`).

### I.2 Creating a region `[FDF_REGION_CREATE_V1]`

`fdf-region-create AREA PUBLIC_IP [--ram-port 51888] [--hub-port 50889] [--ufw]` (`fdf-source/fdf/bin/fdf-region-create`) does the following:

1. Forces umask 022 (`[UMASK_IS_OURS_V1]`) and requires root (`:19-22`).
2. Records the WireGuard implementation (`[WG_IMPL_RECORDED_V1]`, `:42-43`).
3. Refuses if this machine already holds a region (`:44-47`).
4. Makes the site key: 32 bytes from `/dev/urandom` as 64 hex characters, written umask 077 to `/opt/fdf/var/site-key-AREA`, made once (`[ONE_KEY_PER_REGION_V1]`, `:48-57`).
5. Writes the `fdf-ram-AREA` and `fdf-client-AREA` units (`:60-95`).
6. Waits for `version`, then runs `map_site`, which must answer `ok:true` (`:102-113`).
7. Writes `/opt/fdf/hub/hub.env`, the only place it is made, and starts the hub (`:114-133`).
8. Prints the firewall lines, or applies them with `--ufw`, and prints the site key **once** (`:136-150`).

**Updating a region host:** untar the new droplet tarball, `daemon-reload`, then restart the three units. The settings survive because they are not in the package (OPERATING:161-163).

### I.3 Joining

- **The node `.run`** is invoked as `sudo bash fdf-install-<arch>.run <SITE KEY> <RAM_HOST:PORT> <AREA>`. All three arguments are required (`[FDF_INSTALL_V1]`, `fdf-source/deliverables/header.sh:2-43`). It:
  1. checks the architecture and the tools it needs, and that FrogNet is installed;
  2. unpacks, then validates and installs the sudoers fragment;
  3. checks that `/fdf/` answers 200 under Apache;
  4. records the WireGuard implementation;
  5. execs `fdf-region-join`.
- **`fdf-region-join`** (`[FDF_REGION_JOIN_V1]`):
  1. checks the binary's architecture byte;
  2. writes `site.env` (mode 600; it prints only the key's length and last 6 characters);
  3. writes `join.env` **first**;
  4. stops and disables the LAN election timer (`frognet-dbhost-elect.timer`);
  5. restarts the tunnel daemon, which writes the node's facts;
  6. runs `fdf-join`.

  (`fdf-source/fdf/bin/fdf-region-join:15-46`; the timer at `:33-36`, the restart at `:43`.)
- **`fdf-join`** installs `fdf-client.service` (Restart=always, 2 s) and `fdf-facts.service` (`fdf-join:25-61`). "Nothing goes into /usr/local/bin" (`:8`).

### I.4 Facts and the graph

- **`fdf-facts`** publishes `facts.addrs` rows with their interface, IP, value, kind and MAC (`fdf-source/fdf/bin/fdf-facts:4-13`). The kinds are:
  - `own`: the served 10.x.y.1;
  - `held`: a lease on another node's LAN;
  - `given`: a lease handed out;
  - `uplink`: an uplink, with its type and rate;
  - `hub`: the region edge.

  It wakes on `ip monitor` and inotify of the lease directory, with a 50 ms debounce: "nothing polls" (`[FACTS_WAKE_ON_EVENT_V1]`, `:315-375`). An uplink is dead after 3 consecutive missed pings, sent back to back with a 2 s timeout each, in a probe that runs every 3 s; one answer brings it back (`[UPLINK_DEAD_IS_THREE_MISSES_V1]`, `:159-207`).
- **`fdf_graph.py`** is a pure function from the region's participants to one host's plan: routes, default route, hosts file, maps, hub routes, conflicts and exit (`[GRAPH_V1]`, `fdf-source/fdf/lib/python/fdf_graph.py:1-20, 274-277`). Its rules:
  - **Links:** a link is a held lease matched by a given lease (`[ADJACENCY_IS_A_LEASE_PAIR_V1]`).
  - **No cycles:** each host has one upward link (`[FOREST_V1]`).
  - **Exit ranking:** by link speed, then the lower .1, "not by hops" (`[EXIT_RANK_V1]`, `:192-194`). The module's docstring at `:17` still says "nearest" (§N).
  - **Duplicates:** a .1 claimed by two live hosts is a conflict, routed by nobody (`:128-131`).

### I.5 The hub `[FDF_HUB_V1]`

Source: `fdf-source/fdf/bin/fdf-hub`.

- **One interface per region, one peer per live member,** derived entirely from region records. Nothing is configured per member and nothing polls (`:8-12`).
- **Prefixes come only from verified mappings** (signed `map_register`) that name this hub **and** that the member's facts claim: "Facts say who; mappings say what." (`:13-15, 170-175, 217-218`).
- **One owner per /24:** the alive claimant with the lowest announce id. Later claimants are refused and logged with both GUIDs. Owned beats transit (`:16-18, 206-233`).
- **AllowedIPs.** WireGuard silently moves an overlapping AllowedIP to the last peer set, so the hub keeps its own prefix-to-peer table and applies it (`:18-19`).
- **Addressing:** a member's 10.A.B.0/24 gets edge 10.253.A.B/32; the hub is 10.253.253.254/16 (`:20, 150-155`).
- **Events.**
  - Each `p` or `m` line from `fdf-events` is one reconcile, and the first line must be `ready` (`[WATCH_BEFORE_READ_V1]`, `:287-292, 319-327`).
  - On start the hub adopts the kernel's state and never tears down first (`:236-254`).
  - It has no memory of former members: a non-member's peer is deleted on the next event (`[HUB_HAS_NO_MEMORY_V1]`, `:238-244`).
- **Advertising peers.** The hub writes its peer list into its own facts, so a member raises its tunnel only once its key is present. This fixed a first-handshake race of 5,129 ms that occurred one run in three (`[HUB_SAYS_ITS_PEERS_V1]`, `:302-310`).
- **Leaves and reachers** are peered with their /32 only (`[REACH_IS_A_LEAF_HERE_V1]`, `:163-166`).
- **Per event** it logs: `<why>: members=N mappings=N peers=N prefixes=N changes=N build=<tag>` (`:314`).

### I.6 Addresses

- **Nodes choose their own 10.X.Y.1 at install.** The RAM manager does not assign /24s (OPERATING:70-71, 325).
- **A /24 has one owner per region, enforced three times:**
  1. the RAM manager refuses an announcement that overlaps a live member (`[PREFIX_OVERLAP_REFUSED_V1]`, `rina_region.cpp:593-608`);
  2. the hub keeps the lowest id;
  3. the graph routes a duplicated .1 by nobody.
- **Leaves get their address from the region,** following John's 2026-10-10 ruling "Nobody types shit. That's what the ram manager is for" (`[LEAF_ADDR_FROM_THE_REGION_V1]`, `rina_region.cpp:609-646`):
  - the lowest free 10.252.X.Y, bound to the session;
  - the same address on re-announce;
  - freed at session end.

  A leaf that states its own address is refused (EINVAL), and when the range is exhausted the answer is ENOSPC.
- **Reserved ranges:** 10.253/16 holds the hub and its edges, and 10.254/16 is FrogNet's pond range. Neither is a node /24 (`fdf_graph.py:28-33`; OPERATING:325-328).

### I.7 Ponds and reach

- **A pond** is one region. A machine has one **forward** pond, which gives it routes, a mapping, a hub peer and the election, and any number of **participant** ponds, which give it a session and the shared memory only (`fdf-source/docs/PLAN-PONDS.md:7-17`).
- **Managing ponds:**
  - `fdf-pond-add` adds a pond;
  - `fdf-pond-remove` removes one, but refuses the forward pond;
  - `fdf-forward AREA` switches the forward pond in place, without a restart (`[FORWARD_SWITCH_IN_PLACE_V1]`).

  Each `fdf-client@<area>` re-announces as a node or as a participant when the forward pond changes (`[FORWARD_IS_A_FACT_V1]`, `fdf_client.cpp:95-115`).
- **Attention** is a tuple, `(pond, attention, <writer>)`, written with `fdf-attention`. Participants hold a read on it and log `ATTENTION`. The region removes the notice when its writer's session ends (`[ATTENTION_IS_A_TUPLE_V1]`; `rina_region.cpp:981-984`).
- **A region runs in one of three modes,** set by its `(pond, config, net)` tuple (`[NET_CONFIG_IS_A_TUPLE_V1]`, `rina_region.cpp:1016-1050`). The mode decides how far a machine that participates in the pond, without it being its forward pond, can reach into it:

  | Mode | `reach` | What a participant gets |
  |---|---|---|
  | **RAM only (the default)** | `none` | The shared memory and nothing else: no address, no tunnel, no route into the pond's network. |
  | **Restricted** | `listed` | The /32s of the endpoints the pond's `where` cells name, resolved by identity. |
  | **Open** | `all` | Every /24 the pond serves and every /32 it has given. |

  - **Collisions.** A second setting, `map` (`allowed` or `refused`), decides whether colliding addresses may be aliased when reaching.
  - **Who sets it.** The tuple is written only by `rina-host` at start, from `RINA_NET_CONFIG` (default `/opt/fdf/etc/net.json`); the network API cannot change it (`[NET_CONFIG_IS_THE_RAM_MANAGERS_V1]`).
  - **Default.** With no file, the region is RAM only: `reach none, map refused`.
- **Reach** (`[REACH_V1]`, `fdf-source/fdf/bin/fdf-reach`) is the mechanism behind the restricted and open modes. It connects a participant into the pond as far as the mode allows.
- **Address collisions when reaching:**
  - A colliding destination is aliased 1:1 from 100.64.0.0/10, but only if `map` is `allowed`. Otherwise it is REFUSED, naming the prefix.
  - Released aliases are quarantined for 600 s (`[ALIAS_QUARANTINE_V1]`).
  - Reach creates only its own interfaces, tables and chains (`rch-*`, `FDFR*`) and never writes the main routing table (`fdf-reach:17-36, 65-70, 133-164`).
- **Resolving identities.** Tuples name machines by identity (`w:<guid>`), never by address; `resolve` gives the address in *this* pond (`[IDENTITY_IN_TUPLES_V1]`, `rina_region.cpp:672-696`). If two ponds answer, the lookup is refused (`[RESOLVE_AMBIGUOUS_REFUSED_V1]`).
- **Refusal is final.** `fdf-client` takes the reach down and exits with code 4, which systemd does not restart ("No fallbacks", `fdf_client.cpp:116-191`).

### I.8 Leaving

`fdf-leave` (`fdf-source/fdf/bin/fdf-leave`):
1. stops and removes the FD&F units, including every pond;
2. removes the region state files;
3. re-enables the LAN election timer;
4. restarts the daemon in LAN-only mode.

The node keeps its WireGuard key, so a later join announces the same key (`:13-35`; OPERATING:192-194). On the region side, the session's end removes everything bound to the member (§E.5), and the hub deletes its peer on the next event.

---

## Appendix J. Packaging, build and operations

### J.1 Deliverables `[ONE_TARBALL_PER_MACHINE_V1]`

| File | For | Contents | Size (r20) |
|---|---|---|---|
| `fdf-install-x86_64.run` | x86 nodes | `/opt/fdf` (static binaries), `/opt/fdf/comms`, FrogNet region patch | 31,066,176 B |
| `fdf-install-aarch64.run` | Pi nodes | the same, aarch64 | 28,148,395 B |
| `fdf-droplet.tgz` | region host | `/opt/fdf` (static, x86) and `fdf-hub.service`; no FrogNet patch, no comms | 23,216,582 B |

Sources: `rinab/tools/make_deliverables.sh:3-114`; sizes `docs/cleanup-2026-10/FINAL-GATES.md:84`.

**Contents and rules.**
- **Never shipped:** `/opt/fdf/var` (keys, region file, join state) and `hub.env` (`make_deliverables.sh:9-12`).
- **Files only:** no directory entries, owner 0, so extracting at `/` never resets the mode of `/etc`, `/usr` or `/var/www/html` (`[FILES_ONLY_TAR_V1]`, `:97-99`).
- **FrogNet files:** the node bundle carries 52 FrogNet files taken from the repository tree itself (`[MIRROR_IS_THE_TREE_V1]`). These include FrogNet's own existing tools in `/usr/local/bin`, an Apache vhost and a NetworkManager hook. FD&F's own programs are all in `/opt/fdf/bin` (`:13-77`).
- **Build gates.** The build refuses if:
  - the tag in any component differs from `FDF_BUILD_TAG`;
  - the two copies of `fdf-hub` differ;
  - any binary lacks the tag;
  - the comms binaries are missing (`:78-88`).

### J.2 Build from source

- **Toolchain:** g++ with C++20, lz4, OpenSSL, jemalloc, and `aarch64-linux-gnu-g++` (`opt/fdf/README.md:46-58`).
- **Steps:** `tools/build.sh`, `build_static_host.sh`, `build_static_clients.sh`, `build_arm64.sh`, then `make_deliverables.sh <outdir>`. This took about 5 minutes in T4b.
- **Static linking.** Everything is static, because a dynamic `rina-host` built on Ubuntu 24.04 needed glibc 2.38 and failed on 22.04 (`[STATIC_HOST_V1]`).
- **Allocator.** jemalloc is linked when present: host CPU −15% and request-response throughput +16–27% compared with glibc malloc (`[HOST_ALLOCATOR_V1]`, `rinab/tools/build.sh:51-55`).
- **Windows:** `fdf-client.exe` is built with mingw-w64, static (`build_windows_client.sh`).

### J.3 Installed layout and units

- **Everything FD&F is under `/opt/fdf`,** and machine state is in `/opt/fdf/var`.
- **`fdf.env`** sources `var/area.env` and `var/join.env`. There is no built-in area (`[NO_BUILT_IN_AREA_V1]`, `fdf-source/fdf/fdf.env:1-24`).
- **Units:**
  - region host: `fdf-ram-<AREA>`, `fdf-client-<AREA>`, `fdf-hub`;
  - node: `fdf-client`, `fdf-facts`, `fdf-client@<area>` per pond, and `fdf-term-serve` when terminals are enabled.

  (§I; `fdf-join:25-61`; `fdf-pond-add:22-46`.)
- **WireGuard implementation** is chosen once (`kernel` or `wireguard-go`) and recorded in `/etc/frognet/wg.impl`; nothing tries the other (`fdf-wg-impl:2-18, 28-46`).

### J.4 Versions in the logs `[FDF_VERSION_V1]`

- **One source:** `FDF_BUILD_TAG` in `rinab/src/fdf_version.hpp:4`. The Python copies must match, and the build enforces it.
- **Where it appears:**
  - the RAM manager's `version` op;
  - every `world`, `participants` and `resolve` answer;
  - the `(pond, config, net)` cell;
  - `fdf-client`'s start line (`build <tag>; region at <ram> is build <tag>`);
  - every hub event line;
  - `fdf-facts`;
  - `fdf-reach`;
  - the `.run`, `fdf-region-create`, `fdf-region-join` and `fdf-leave` banners.

  (`rina_region.cpp:652-698, 1042-1057`; `fdf-hub:286, 314`; `fdf_client.cpp:326-327`; OPERATING:295-310.)
- **Generic memory version:** `v0.63-bounded` (`version.hpp:5`).

### J.5 Logging and diagnostics

- **RAM server log lines:** `[RAM-SERVER] <time> session up|data socket up|session down … after N requests` (`ram_server.cpp:86-87, 651-670`).
- **Diagnostic switches:**

  | Variable | Effect |
  |---|---|
  | `FROGRAM_TRACE` | Per-request trace |
  | `RINA_DIAG` | Coalescing and known-shape counters at exit |
  | `RINA_WIRE_STATS=1` | Per-session wire counters at program exit |
  | `RINA_SEND_TRACE` | Trace of SDU sends |
  | `RINA_EXIT_TRACE` | Exit reason trace |
  | `RINA_RMT_STATS` | RMT counters at exit (now always zero, §N) |
  | `RINA_AGENT_LOG` | Agent log location |

  (`ram_server.cpp:428-429, 520-523`; `rina_region.cpp:924`; `librina_region.cpp:262, 294, 486-488`; `rina_node.cpp:93-97, 282-287`.)
- **Not logged:** individual reads and writes, refused requests, connections dropped before a session forms, and send failures (`ram_server.cpp:239-278, 630-664, 413`).

---

## Appendix K. Clients and applications

### K.1 Windows bridge and Android

Sources: `fdf-source/docs/WINDOWS-BRIDGE.md`; `fdf-source/docs/PLAN-BRIDGE.md`; `opt/fdf/src/frognet-android/docs/ANDROID-HANDOFF.md`.

- **The device is a region member,** not a VPN client of a fixed server. It holds:
  - one FNWP session per pond (announce, facts, presence, shared memory, attention);
  - a leaf of the forward pond;
  - reach into other ponds, as their config allows.

  The Android `VpnService` is only the packet interface (`ANDROID-HANDOFF.md:9-23`; `WINDOWS-BRIDGE.md:12-14`).
- **The member protocol is plain Kotlin** (`member/`: Wire, Session with three sockets, Member, Hubs, Placement, Leaf, Bridge). It runs on Android, on a JVM for the netns tests, and on Windows (`ANDROID-HANDOFF.md:123-141`).
- **Placement** (`[ADDRESS_IS_THE_ONE_IT_HAS_V1]`): a device with an address inside a live member's /24 is **on** the mesh, with no tunnel. Otherwise it is a leaf through the hub. A change of its default network ends the session (`ANDROID-HANDOFF.md:101-107`).
- **`fdf-tun`** (Go, vendoring wireguard-go): one TUN device, one WireGuard device per scope, and 1:1 alias rewriting with incremental checksums (`PLAN-BRIDGE.md:66-76`).
  - **Limitation, by design:** addresses inside payloads (FTP, SIP/SDP, RTSP, H.323, embedded URLs) are not rewritten. FrogNet apps use `w:` identities instead (`PLAN-BRIDGE.md:141-149`).
- **Windows** (`fdf-kt-…-a15` with `fdf-tun-…-b11`, bundled Java 21) uses the Wintun adapter "FrogNet" and runs as Administrator. `wintun.dll` is not included (`WINDOWS-BRIDGE.md:16-35`).
  - **HW 2026-10-11 (a12/b8):** on the mesh on Seattle5's LAN; off the mesh at 192.168.0.2, given 10.252.0.1, ping and ssh to a node behind the hub.
  - a15/b11 not run on Windows (`WINDOWS-BRIDGE.md:3-5, 60-76`).
- **Android:** a15, which a handset joined and reached through (**HW**, before the cleanup). **No APK is built or shipped** (`ANDROID-HANDOFF.md:3-7`; OPERATING:198-201).
  - **Measured (netns, r16):** leaf tunnel joined at +334 ms and first handshake at +437 ms; a killed leaf's hub peer deleted in 17–46 ms (`ANDROID-HANDOFF.md:201-212`).

### K.2 Applications on FD&F

- **Web views.**
  - `/fdf/` on the node's Apache: participants first, then a held read until the set changes, "no timer" (`[FDF_DASHBOARD_V1]`, `fdf-source/fdf/www/fdf/fdf_dashboard.html:6-13`).
  - Chat: page input as `<App>.<Queue>.<submitter>`, with a held read (`[FDF_CHAT_V1]`).
  - CLI views: `fdf-ls` and `fdf-monitor` ("no timer").
- **Browser terminal** (`[FDF_TERM_V1]`): through the RAM manager only. No port is opened on the machines, and no machine connects to another (`opt/fdf/README.md:165-166`).
- **Communicator** (`[COMMS_ON_A_NODE_V1]`):
  - `comms-ram` (8800) and `comms-media` (8994) ship static on every node.
  - `fdf-comms.sh` publishes their location as the tuple `(pond, where, w:<guid>)`, by identity, and the region removes the tuple with the session.
  - **Not exercised by the gates.** **HW 2026-10-07:** video 850×480 at 24 fps in 400–800 kb/s, total under 2 Mbps, Windows laptop behind NAT through the droplet (`RESULTS.md:1735-1749`).
- **Kart demo:**
  - its own region on 8799 (`fdf-kart-ram.sh`);
  - players' poses and tiles in the shared memory, "no game server" (`opt/fdf/kart/README.md:1-45`).
  - **HW 2026-10-07:** across Windows and a Raspberry Pi (`RESULTS.md:1620-1632`).

---

## Appendix L. Tests and verification

### L.1 Acceptance gates of the October 2026 cleanup (the current acceptance set)

**Environment.**
- The sandbox, with no WireGuard kernel module, so wireguard-go.
- Docker containers from `frognet-clean:noble` (Ubuntu 24.04 x86-64 with systemd).
- No arm64 execution, no real Wi-Fi, no kernel WireGuard.

**Last run:** 2026-10-13, on r20 (`docs/cleanup-2026-10/FINAL-GATES.md:1, 53-84`; logs in `docs/cleanup-2026-10/final-logs/`).

| Gate | Exercises | How to run | Pass | Result |
|---|---|---|---|---|
| 1g hub oracle | `fdf-hub` decisions against a fake kernel: ownership, the AllowedIPs trap, verified mappings, transit, leave, adopt, leaf, graph prefixes, reach as leaf | `FDF_PY=opt/fdf/lib/python python3 opt/fdf/hub/hub_oracle.py` | `RESULT PASS` | **PASS 27/27** |
| 2a region loop | real tunnel daemon, RAM manager, `fdf-hub`, wireguard-go, netns; 6 changes | `rinab/tools/netns-region-loop-test.sh` | `RESULT PASS` | **PASS 13 + 1 skip** (Communicator stage) |
| 2b region loop, NOKEY | the daemon makes its own WireGuard key | the same, `NOKEY=a` | `RESULT PASS` | **PASS 17 + 1 skip** |
| 2c LAN-only | two nodes, no RAM manager, no WireGuard | `rinab/tools/netns-lan-only-test.sh` | 19 checks | **PASS 19/19** |
| 2d ponds | one node in two ponds, forward switched 6× | `rinab/tools/netns-ponds-daemon-test.sh` | `RESULT PASS` | **PASS 21** (one timing flake recorded at T3a) |
| 2e reach | ponds A–F, collisions, aliases, refused maps, phone bridge stage | `rinab/tools/netns-reach-test.sh` (with `BRIDGE_KT`, `FDF_TUN`) | `RESULT PASS` | **PASS 70** |
| 2f member selftest | the Kotlin member | `fdf-member-kt selftest` | `RESULT PASS` | **PASS** |
| 3a / 3b static gates | grep for broker remnants and for fallback patterns | `docs/cleanup-2026-10/static-no-broker.sh`, `static-no-fallback.sh` | 0 violations | **0 / 0** (the T0 tree had 3,020 / 237) |
| 3c docs | every command in `OPERATING.md` runs; no forbidden words | `docs/cleanup-2026-10/check-docs.sh` | 0 problems | **0** (34 docs, 32 commands) |
| G1 | clean LAN-only install and reboot | container | no failed unit | **PASS**, 6 min 25 s |
| G3 | `fdf-region-create` plus two nodes via OPERATING §D, plus an outage | containers d2, n1, n2 | pings through the hub; recovery with no manual step | **PASS**: 3/3 each way at 1.1–1.5 ms; back 15 s after a 52 s outage |
| G4 / G5 | upgrade with `--preserve`; `fdf-leave` and uninstall | containers | identity kept; nothing left | **PASS** |
| LAN region | region with no Internet (T7 steps 1 and 10, rehearsed) | containers p1, p2 | no Internet; key rotation | **PASS** (one member) |
| net config | second pond reachable through a `RINA_NET_CONFIG` drop-in | container d4 | `reach all, map allowed` | **PASS with the drop-in.** The default path `/opt/fdf/etc/net.json` does not work (§N). |
| 5 dist | deliverable payloads equal the tree | `rinab/tools/make_deliverables.sh`, then `cmp` | every file but BUILD equal | **PASS** |

**Also run, not all green** (`FINAL-GATES.md:55-62, 74-75, 77`): gates 1a–1b, 1e–1f, 3d and G2 pass. Gate 1c (simulator) has 2 red tiers (collectives/DDP and the aiconnect ramp, which need node-local code) and 1d has 4 red oracles that test code not in this tree.

**Not re-run.** The netns gates were not re-run after the final fixes, since no netns script runs the fixed `fdf-region-create`, `fdf-region-join` or `fdf-leave` (`FINAL-GATES.md:70`). G3 was run fresh in the final gates (`FINAL-GATES.md:78`).

### L.2 FD&F's own tests: last results on earlier builds

None of the following is in the cleanup gates. The r20 `build/bin` lacks the binaries they call (`docs/cleanup-2026-10/T5.md:227-229`), and their evidence files are not in the package (`rinab/README.md:7-9`). Label: **HIST**.

| Test | Exercises | Pass criterion | Last result |
|---|---|---|---|
| rlite acceptance suite, `sudo ./run.sh RLITE_ROOT` | rlite's own 30 integration tests, unchanged, against the region | exit 0 per test | **30/30** after every change, 2026-10-05 to 10-07 |
| `transport-conformance` (`rinab/tests/transport_conformance.cpp`) | the transport interface; `change` returns within 5 s of a re-announce | `RESULT PASS` | PASS 2026-10-07 |
| `discovery-check.sh` | 3 machines announce, one leaves; tcpdump | 0 packets between machines | PASS: 110 packets, 0 between machines |
| `selfreg-check.sh` | the RAM manager registers itself; kill -9; move | the mapping answers the new address | PASS, 3.7 / 4.3 ms |
| `mapwait-check.sh` | a held watch returns on re-registration | returns by itself | PASS, 60 ms |
| `move-check.sh` | machines follow a moved RAM manager | 3 alive, 3 re-attached, m2m=0 | PASS, 73–182 ms |
| `rule-check.sh` | only `RINA_AREA` given; Map-Resolver derived from the name | joined=3, dns=0 | PASS, 269–310 ms |
| `map-region-check.sh` | LISP mapping semantics in a region | 0 UDP 4342 | PASS |
| `map-oracle-check.sh` | against unmodified lispers.net 0246ef3 | 7/7 agree | PASS twice |
| `map-expiry-compare.sh` | expiry timing | measurement | region 5.1 s; lispers.net 31.5–58.8 s |
| `region-file-check.sh` | Permanent survives kill -9; an IPCP does not | as stated | PASS |
| `dashboard-check.sh`, `chat-check.sh` | PHP pages, held reads | 200, held | PASS 2026-10-07; the T5b version was not run |
| `elect_test.cpp` | the dormant election rule | `RESULT PASS` | 9/9 |
| `map_prefix.cpp`, `mapown_oracle.sh`, `net_config_test.cpp`, `reach_alias_test.py` | prefix mappings; owner not transferred; config read-only; alias quarantine | `RESULT PASS` | No dated result recorded. Gate 2e checks the same property as `net_config_test` in its own stage 6 (`netns-reach-test.sh:284-285`). |

All from `rinab/experiments/RESULTS.md` and `rinab/HANDOFF.md:96-108`.

### L.3 Hardware acceptance (T7)

Ten steps, each with "look for" lines and a pass criterion (`docs/cleanup-2026-10/T7-HARDWARE-ACCEPTANCE.md`):
1. rotate the site key;
2. update the droplet to r20;
3. upgrade a Pi with `--preserve`;
4. fresh Pi, LAN-only;
5. a Pi joins the region;
6. phone and Windows PC join, with a Communicator call;
7. a node leaves;
8. reboot the droplet; everyone comes back with nothing typed;
9. two regions on one PC;
10. a region with no Internet.

**Status: NOT RUN.** The document lists what has never been executed (T7:340-352):
- the arm64 `.run`;
- kernel WireGuard;
- real Wi-Fi;
- a real phone;
- the Windows bridge on Windows;
- a Communicator call;
- a Pi region host carrying traffic;
- `--preserve` on a region member.

Hardware evidence that does exist:
- Seattle7 (Raspberry Pi), r21–r28, 2026-10-14 (`docs/cleanup-2026-10/FIX-2026-10-14-IDENTITY.md`);
- the first FD&F hardware runs on 2026-10-07: the kart demo and the Communicator (`RESULTS.md:1620-1749`).

### L.4 Measured numbers

| Number | Build | Source |
|---|---|---|
| Region loop: change to applied, median 87 / 89 ms. Daemon total 24 / 27 ms (world 1–2, bringup 15–16, apply 5–6). | **r20**, netns | `docs/cleanup-2026-10/final-logs/region.txt` |
| Pond switch to the new pond's member answering: 398–645 ms (6 switches) | **r20**, netns | `final-logs/ponds.txt` |
| Ping between region nodes through the hub: 1.1–1.5 ms (earlier builds up to 2.3 ms, T4a–T5b) | **r20**, containers | `FINAL-GATES.md:78` |
| Region host off the network 52 s: traffic back 15 s after reconnect (earlier builds: 49 s off, back in 20–40 s) | **r20**, containers | `FINAL-GATES.md:78`; T4a; T4b |
| Flow data through the RAM manager: 1.03 requests per transaction; 520 µs per transaction (member to member had been 22 µs) | HIST 2026-10-07 | `RESULTS.md:1096-1102` |
| Re-attach after a move: 73–310 ms; 0 packets between machines | HIST | `RESULTS.md:1228-1614` |
| Join cost: 29 packets, about 1,085 bytes, 42–66 ms, flat from N=2 to 1,000 | HIST 2026-10-08; harness lost, **not re-checkable** | `fdf-source/docs/history/HANDOFF-FDF-2026-10-08.md:99-101` |
| Clean leave: prefixes gone in 131–139 ms. Silent loss detected at 19,438 ms (then a 20 s bound) | HIST 2026-10-09 | `HANDOFF-REGION-2026-10-09.md:79-98` |
| Mode B against rlite (different machines): ping 0.020 against 0.012 ms; request-response 51,459 against 85,598 tps | HIST 2026-10-05, design since superseded | `rinab/STEP1-DATA-PLANE.md:36-45` |
| Lines of code against rlite | **not measured**: `bench.sh` computes it, but no output is recorded | `rinab/bench.sh` |

### L.5 Memory, session and mapping tests

`rinab/tools/memory-order-check.sh [WRITERS] [WRITES]` builds the tests below, starts a fresh `build/rina-host`, runs them, and prints `RESULT PASS` or `RESULT FAIL`. Last run 2026-10-15 on a 2-core x86-64 sandbox, loopback.

| Test | Exercises | Pass criterion | Result |
|---|---|---|---|
| `tests/order_race.cpp`, mode `variable` | 8 writer sessions × 2,000 new cells on one variable; a follower doing held reads with `after` = the largest id received (R-M10) | the follower received every id the final full read lists | 16,000 / 16,000, three runs |
| `tests/order_race.cpp`, mode `service` | the same, one variable per writer, the follower reading the whole service | as above | 16,000 / 16,000, three runs; 64,000 / 64,000 with 16 × 4,000, two runs |
| `tests/seen_race.cpp` | two processes at once, from 127.0.0.2 and 127.0.0.3 (`tests/srcbind.c`), each 16 sessions × 2,000 `seen` (R-M11) | every answer is the process's own address | 64,000 / 64,000 right, three runs |
| `tests/write_rate.cpp` | 16 writer sessions × 4,000 new cells, one variable each, no reader | measurement | 9,900–11,100 writes/s |
| `tests/map_removal_persists.cpp` (`tools/map-removal-check.sh`) | a mapping removed by `map_remove`, and one removed when its session ends, both stay removed after the RAM host restarts on the same region file; a mapping not removed survives; a session-bound mapping held when the host is killed with SIGKILL is not restored (R-M12) | all four as stated, before and after the restart | Pass, 2026-10-15, three runs; fails with either fix removed |
| `tests/identical_writes.cpp` (in `memory-order-check.sh`) | 16 sessions × 500 byte-identical writes to one cell, in flight together (R-M13) | every call answered; one cell, holding the value written | Pass, 2026-10-15: 229 of 8,000 calls coalesced |

---

## Appendix M. Traceability summary

| Requirement area | Tested on r20 | Tested only on earlier builds | No test |
|---|---|---|---|
| Architecture | R-A2, R-A6, R-A7, R-A8, R-A9 (netns), R-A10, R-A11, R-A12 (containers) | R-A1, R-A3, R-A4, R-A5, R-A13 | — |
| Bootstrap | R-D7, R-D8 | R-D1, R-D2, R-D3, R-D4, R-D5, R-D6 | R-D9 (not implemented) |
| Memory | R-M10, R-M11, R-M12, R-M13 (current source, 2026-10-15) | R-M1, R-M2, R-M3, R-M5, R-M6, R-M8 | R-M4, R-M9 (tests exist with no dated result); R-M7 |
| Failure | R-F1, R-F2, R-F5, R-F7, R-F10 | R-F4, R-F9 | R-F3, R-F6, R-F8 |
| Security | R-S2 | — | R-S1, R-S3 (design), R-S4 |
| Performance | — | R-P1 | R-P2 |
| Packaging | R-O1, R-O2, R-O3, R-O4, R-O5 | — | — |

**The largest gap:** the stand-alone FD&F claims are proven only on the 2026-10-07 build. These are R-A1 (nothing between machines), R-D1/R-D2 (only the area's name) and rlite 30/30. Re-running `discovery-check`, `rule-check`, `move-check`, `transport-conformance` and the rlite suite on r20 would bring them current.

---

## Appendix N. Known discrepancies, defects and open items

Found by reading the code against its comments and documents. Each was checked against the code.

### N.1 Behaviour that differs from what the comments or documents say

1. **Numeric query arguments are coerced, not refused.** Non-numeric values become 0, contradicting "NO FALLBACKS" (`ram_server.cpp:31, 259-261`).
2. **`op=remove` is unreachable through the plain HTTP front,** which recognises only `GET` and `POST` (`ram_server.cpp:629`).
3. **Legacy-path ERROR frames omit the message-length field** that semwire defines (`ram_server.cpp:581-588` against `semwire.hpp:12`).
4. **Choosing a region mode is undocumented.** A fresh region is RAM only, by design (§I.7). But `fdf-region-create` has no option to choose restricted or open, nothing ships `/opt/fdf/etc/net.json`, and `OPERATING.md` does not mention the file. The final gates also found that the default path does not work, and only a `RINA_NET_CONFIG` drop-in does (`rina_region.cpp:1016-1050`; FINAL-GATES: "the default path does not work").
5. **The RAM manager's mapping TTL is 86,400 s with no re-registration.** The TTL is checked on every read, so a manager up for more than a day drops out of the mapping in mode b (`rina_region.cpp:1012`).
6. **Records outside the reassert sets:** `reg|` and `route|` are in neither the agent's nor the library's set. It is not established how they return after a host change (`rina_region.cpp:384-392`).
7. **The SDU-by-reference form is accepted by the region but never emitted by librina,** which always sends bytes (`rina_region.cpp:798-803` against `librina_region.cpp:485`). The `"k":"c"` credit event librina handles is never sent.
8. **Two identity schemes:** `fdf-client` uses `w:<guid>`; librina and `rlite-ctl` use `m:<guid>/<netns inode>`. And `machine_id.hpp:7` says the inode is "always" appended, but it is not when `RINA_MACHINE_ID` is set (`:30`).
9. **The capability probe runs once per agent,** so its load and temperature fields stay as first measured (`rina_node.cpp:159-171`).
10. **`fdf_graph.py`'s docstring says the exit is the "nearest" host;** the code ranks by speed, then the lower .1, which matches the 2026-10-09 ruling (`:17` against `:192-194`).
11. **The node `.run` writes outside `/opt/fdf`:** 52 FrogNet files into `/opt/frognet_semantic` (28), `/usr/local/bin` (15), `/var/www/html` (6) and `/etc` (3) (`make_deliverables.sh:23-77`). The "nothing in /usr/local/bin" statement holds for FD&F's own programs only.
12. **The build source.** `platform/ramsrv` and `platform/ribbit_cpp` lack the `[NET_CONFIG_IS_THE_RAM_MANAGERS_V1]` patch the build applies, and `build.sh:2` calls the patched files "vendored untouched" (§A).

### N.2 Stale comments and dead code

- These still describe retired member-to-member flows and neighbour propagation: `rina_node.cpp:142, 308-310`; `librina_region.cpp:396` ("flows keep moving member to member"); `rmt.hpp:1-21` (hop-by-hop Ethernet relay).
- `rina_region.cpp:1008` still mentions an SRV record. `rlite_ctl.cpp:387` shows a `pol|<netns>|…` key that the code does not use.
- Dead code: `rina_elect.hpp` is included but unused; `g_answer`, `loopback_locator`, `RMT_TCP_PORT`, `LISTEN_HOST` and `LISTEN_PORT` are set but never read; the RMT counters are never incremented.
- `fdf-hub`'s argparse default port is 50820, against 50889 everywhere else (the unit passes `--listen` from `hub.env`, which `fdf-region-create` writes as 50889 unless `--hub-port` is given). The kart README names a `kart-ram` binary that does not ship.

### N.3 Timers and second paths

These are in tension with R-F1 and R-F2. They are recorded here, not ruled on.

| Where | What |
|---|---|
| `fdf_client.cpp:384-389` | `[MAPS_ASSERTED_V1]`: mappings re-asserted every `FDF_ASSERT_SEC` (default 30 s). `mapown_oracle` depends on it. |
| `rlite_ctl.cpp:98-104` | The address claim spins with `yield` until its own claim is visible. The comment says "no wait". |
| FrogNet daemon | Region rejoin every 5 s; capability re-probed every 600 s; graph hold-down of 60 s before removing a vanished row (`opt/frognet_semantic/internet_tunnels_v3/poll.py:1652, 1355`; `graph_apply.py:42`). |
| `rina_region.cpp:235-239` | `notify_agents()` pushes a topology event to every agent on every node change. The 2026-10-08 fix `[NO_TOPOLOGY_BROADCAST_V1]` was lost with that workspace. |
| `fdf-ram-manager.sh:13-15` | When the mapping is local, the manager's address is the default route's source, else the first global address: two steps. |
| `fdfram.where()`, `fdf-reach ram_of` | `fdfram.where()`: the environment, else `join.env`. `fdf-reach ram_of`: the pond's `ponds/<area>.env`, else the environment when `RINA_AREA` is that pond. Both allowed in `static-no-fallback.sh:101`, with a stated reason. |

### N.4 Open items

- **Security:**
  - `map_site` needs a credential;
  - the bootstrap is not encrypted (R-D9);
  - restricted DIFs exist only as design (R-S3);
  - contract 5, forwarding limited to what was exposed, has no test.
- **Verification:**
  - re-run the stand-alone FD&F tests and the rlite suite on r20 (§M);
  - T7 on hardware;
  - kernel WireGuard and arm64 have never been executed;
  - record `bench.sh` against rlite.
- **Design questions recorded:**
  - in LAN-only mode a neighbour leaving is not an event; the role stays 5 min 15 s (T6a);
  - receivers should re-assert their taken counts, so that a re-send after a restart is not the whole window (`RESULTS.md:673-675`);
  - the IPv6 prefix of the Map-Resolver rule is a placeholder;
  - the mapping region does not answer real-Internet LISP (`RESULTS.md:735-736`).
- **Tests needing repair:**
  - `eexist_oracle.sh` has no pass criterion and a wrong binary path;
  - the copies of `netns-hub-test.sh` and `netns-graph-test.sh` under `fdf/hub/` are stale.

---

## Appendix O. Environment variables

| Variable | Meaning | Source |
|---|---|---|
| `RINA_RAM` | `host:port` of the RAM manager (direct, region) | `rina_region_client.hpp:105` |
| `RINA_AREA`, `RINA_AREA_PORT` | Area name; RAM port (default 8840) | `:106-107` |
| `RINA_MAP_RESOLVER` | Overrides the derived Map-Resolver address | `:56-62` |
| `RINA_AREA_PASSWORD` | Area password, on the mapping machine and RAM-manager candidates only | `fdf-mapping.sh:7-8` |
| `RINA_MAP_AREA` | Area the RAM manager registers itself under | `rina_region.cpp:998-1015` |
| `RINA_HOST_ROLE=mapping` | Run `rina-host` as the mapping region | `rina_region.cpp:1056-1060` |
| `RINA_REGION_FILE` | Permanent-record file; required | `rina_region.cpp:42-52` |
| `RINA_NET_CONFIG` | Pond config file (default `/opt/fdf/etc/net.json`) | `rina_region.cpp:1016-1050` |
| `RINA_SITE_KEY` | Site key for signing mappings (region) | `fdf_client.cpp:207-260` |
| `RINA_MACHINE_ID` | Overrides machine identity | `machine_id.hpp:28-33` |
| `RINA_RAM_NETNS` | Network namespace the connection is made from | `rina_region_client.hpp:152-161` |
| `RINA_CAPABILITY_PROBE` | Capability probe script | `fdf.env:20` |
| `RIBBIT_TEMPLATES` | Primed BLDC-1 dictionary | `fnwp_engine.hpp:101-137` |
| `FDF_FACTS`, `FDF_NAME`, `FDF_FORWARD_FILE`, `FDF_REACH`, `FDF_ASSERT_SEC` | `fdf-client` facts file, name, forward pond, reach, assert interval | `fdf_client.cpp` |
| `FDF_WG_IMPL` | WireGuard implementation override (test harnesses) | `fdf-hub:66-70`; `opt/frognet_semantic/internet_tunnels_v3/config.py:80` |
| `FROGRAM_TRACE`, `RINA_DIAG`, `RINA_WIRE_STATS`, `RINA_SEND_TRACE`, `RINA_EXIT_TRACE`, `RINA_RMT_STATS`, `RINA_AGENT_LOG` | Diagnostics (§J.5) | — |

---

# Additional capabilities

## Appendix P. Preloading custom data

A region can start with any data its owner chooses, not just FD&F's own records. "I can pre-fill it before anybody connects" (`opt/frognet_semantic/ribbit/RIBBIT.md:179-190`).
- **Example.** A game server loads the players, their inventory, their attributes and their stats before the game starts. When everybody connects, the world is already there, and no client has to exchange anything to reconstruct it.
- **Where the data comes from** is the region owner's choice: a file, a database, an archive of an earlier session, or nothing at all. "The backing store is this application's choice" (`ribbit/cpp/src/ram_host.cpp:29-30`).
- **How it gets in.** Two patterns are in the tree:
  - **The host writes it as it starts.** FD&F does this twice: the Permanent records replayed from the region file and the network configuration written from `net.json` (§E.7, steps 1 and 2). The data is present before the host accepts its first session.
  - **A loader writes it.** A participant connects, writes the data through the ordinary API, and only then are the players given the address. This works with any host, including one with no operations of its own (§Q.2, §Q.3).
- **Protection.** Data the participants must not change can be kept the way `(pond, config, net)` is: written by the host, refused to the network API. Data they must not see can go in a private `#` service (§Q.1).

---

## Appendix Q. Examples of extending the RAM API

Every RAM host serves the same base at one endpoint path, over FNW1: the memory's own operations (read, write, remove, held reads). A region's owner (Ribbit's word is *vendor*) extends it to fit what the region means. The examples below each go a different distance. Paths are relative to `opt/frognet_semantic/ribbit/` unless they start with `rinab/`.

| Extension | What the owner adds | Examples |
|---|---|---|
| Schema only | What service, variable and instance mean. No new operation. | Chat, Communicator |
| Region operations | `op=<name>` requests that run in the host, against the memory | LISP (`op=lisp`); FD&F's RINA region (`op=rina`, `op=rina_block`); Redis (`op=redis` and others) |
| Memory operations | New primitives in the memory, called by the region's operations | Redis |

Why an operation goes in the host at all: some decisions need the memory's atomicity. A queue claim looks at a value and changes it. A multi-record Map-Register must validate everything before it changes anything. "Those operations belong beside the memory because that's where the required atomicity can actually be provided" (`RIBBIT.md:904-910`). The rest of the application stays with the participants.

### Q.1 Adding an operation

There are two ways, with the same result: the host's endpoint answers the base operations and the region's.

- **Derive from `ribbit::RamHost`** (`cpp/include/ram_host.hpp:1-72`).
  - Override `vendor_id()` and `vendor_version()`. For operations, also override `handles(op)` and `operation(op, body)`.
  - `resident_operation()` runs an operation against the memory itself. It reads the rows it names, each under its row's shared lock, and writes its own rows under their exclusive locks, with no session, view or copy.
  - The reply carries the result, the id of every cell written, and the region service's cells whole, so the caller holds its own writes at once (`[OWN_WRITES_HELD_V1]`).
  - The endpoint defaults to `/<vendor_id>-api`. `main()` is one line, and the result is one executable.
- **Patch the platform's host at build** (`rinab/tools/make_host.sh`; `examples/redis/tools/make_host.sh`).
  - The patch takes the platform's unmodified `ram_server.cpp` and adds one include and one dispatch block, at anchors that must each be found exactly once, or the build refuses.
  - FD&F and Redis take this route because their hosts also carry the platform patches applied in `rinab/tools/build.sh`.

The examples follow the same rules:
- **An inline operation never waits.** Anything that may wait is a separate operation: `rina_block` answers now or later through a continuation (`[DEFERRED_ANSWER_V1]`), and `redis_block` parks on its own request thread.
- **Errors.** A malformed body is a 400 naming the operation; the wrong method is a 405.
- **The caller's session.** An answer that depends on the caller's session reads it from the host (`rina_session_id()`), and such operations are coalesced within one session only (§D.6).
- **Private services.** A service whose name starts with `#` holds the region's secrets and is never served over the network (`private_service`).

### Q.2 Chat: schema only

`examples/chat/` (`README.md`). `chat-ram` is a `RamHost` with no operations of its own.
- **Buffers.** Bob's buffer is `(ChatServer, Bob, *)`. Each sender owns a ring of 64 cells in it (instance `Dave#<slot>`), so two people writing to Bob at once never overwrite each other. Bob holds a read on his buffer, which wakes when anyone writes there.
- **Presence.** One cell per person, `(ChatPresence, here, Dave)`, refreshed by its owner and believed for 60 seconds.
- **Commands.** `/list`, `/Name text` and the rest are reads and writes in the client.

### Q.3 Communicator: schema only

`examples/communicator/src/ram/comms_ram.cpp:1-21`.
- **Tuples.** Presence, chat, the five call-state tuples and each media host's connection information are all tuples on the default API. Each is written by the party with standing to know it and read by everyone else.
- **Media.** The streams go to a separate media host. It publishes where it listens as a tuple, and callers read the tuple and dial it.
- `comms-ram` has no operations.

### Q.4 LISP: one operation with an engine behind it

`examples/lispers.net/ribbit_cpp/lisper_ram.cpp:1-35`.
- **The operation.** `handles("lisp")`, with body `{"operation": NAME, "args": {...}}`.
- **What runs.** Each call runs a resident `Engine` (`lisp_engine.hpp`) in the memory. A whole Map-Register, with its authentication, site policy, registration and Map-Notify, is one call. A site, a key and a policy are one call each.
- **Its cells** are under the service `lisp`.
- **Verification.** It was tested against lispers.net 0.643 (`examples/lispers.net/README.md`).
- **In FD&F.** The FD&F host keeps this operation from the platform (§D.7).

### Q.5 FD&F's RINA region: two operations and its own store

`rinab/tools/make_host.sh`; `rinab/src/rina_region.cpp`.
- **Operations.** `op=rina` answers at once. `op=rina_block` answers now or later through a continuation and never holds a thread. Both are recognized by the exact tail of the path before the query string is parsed (`[REGION_FAST_PATH_V1]`). §E.4 lists the operations carried in the body.
- **Its own store.** The region keeps its records in its own lock-free store, not in the generic memory. Permanent records go to the region file (§E.1–§E.2).
- **The caller's session.** Several operations answer from the caller's session: `seen`, registration, mapping ownership and `send` (§D.6).

### Q.6 Redis: region operations and memory primitives

`examples/redis/` (`README.md`; `src/door/redis_region.h`).
- **Shape.** Stock Redis clients speak RESP to a front. Fronts hold no data and connect up over FNW1 to one RAM host, which holds the keyspace as the Redis region.
- **Endpoint.** `/Fawcett.Redis.ram_interface.php`. It inherits the platform's API whole.
- **Region operations:**
  - `op=redis`: a pipeline of commands, run inline;
  - `op=redis_block`: one blocking command (BLPOP, XREAD BLOCK and the like), parked on its own request thread;
  - batch forms carrying many connections' pipelines in one call. The raw form is answered asynchronously by whichever thread finishes the batch's last item (`[NOBODY_WAITS_V1]`).

The memory is extended with operations that decide in the server, so no command reads, decides and writes back (`[THE_MEMORY_DECIDES_V1]`, `src/memory.hpp`, `src/memory.cpp`):

| Memory operation | What the memory decides | Commands built on it |
|---|---|---|
| `add(cell, delta)` | the total, by one fetch-add; each adder gets its own total | INCR, DECR, INCRBY, DECRBY, HINCRBY |
| `replace_if(cell, expected, new)` | whether the write lands: only if the cell still holds what was read | SETNX, SET NX/XX, HSETNX, MSETNX, EXPIRE NX/XX/GT/LT, XREADGROUP delivery |
| `exchange(value, new)` | which value was replaced, handed back | GETSET, SET … GET |
| `take_variable(key)` | who removed the key, with its value, handed to that one caller | GETDEL |
| `remove_if(value, expected)` | taking back only what this caller wrote | MSETNX losing a later key |
| `update` | a value decided from the live cell; if another writer replaced the cell first, decided again on the new one | ZADD INCR/NX/XX/GT/LT, ZINCRBY |
| pieces, folded on read in ticket order | nothing: each edit is its own cell, so no edit can be lost | SETRANGE, SETBIT, BITFIELD, PFADD, PFMERGE |

- **Claims.** A command takes the memory's answer as final. Where a claim is lost, it takes the next item, never the same one again (pops, SPOP, LREM, XREADGROUP).
- **Test.** `tests/run_concurrency.py` exercises 38 read-modify-write command families under contention.

---

## Appendix R. Examples of extending semantic message handling

Semantic compression (BLDC-1) sends a message's template once, then only what changed:
- **Templates.** A request or reply is matched to a template, learned or primed. The first message carries the template's fields in full, and later ones carry only the dynamic fields. An identical message is sent as a repeat.
- **Handlers.** A *handler* owns this for one kind of body: it learns the template, extracts the dynamic fields, and rebuilds the body exactly.
- **When a body doesn't fit.** If a handler cannot hold a body (it doesn't parse, or its shape is not the template's), it returns an empty extraction. That is a signal, not a failure: the body is sent raw, and the raw body becomes the new reference.

Paths are relative to `opt/frognet_semantic/` unless they start with `opt/fdf/` or `rinab/`.

| Extension | What it adds | Examples |
|---|---|---|
| Format handler (the codec slot) | compression for one body format | JSON, XML, HTML, text and raw (built in); SotF media (the Communicator); `ram_rows` (RAM read answers, C++) |
| Role handler (election and lifecycle slots) | a role the network elects, with the capability it advertises | `databasehost` (built in); `mediahost` (SotF); `lisp` |
| Programs on the handler | the participants that use the role, created by the handler | SotF's `open_stream()`; LISP's `open_site()` and `open_boundary()` |
| Primed templates | a dictionary shipped instead of learned | FD&F's `fdf.prime` |

### R.1 The interface

Every handler has the same shape, in Python (`core/unrest_handler.py:19-40, 82-206`) and in C++ (`ribbit/cpp/include/unrest_handler.hpp`). Callers look the handler up and call the interface; they never branch on the type of the body.

| Slot | Methods | Default |
|---|---|---|
| Identity | `ROLE_NAME`, `CANDIDATE_TYPE`, `DEFAULT_PORT` | none: a content-only handler names no role |
| Codec | `learn_request_template`, `learn_reply_template`, `extract_request_dynamic`, `extract_reply_dynamic`, `rebuild_reply`, `decode_payload` | typed empties, so a handler that is not a codec is inert on this path |
| Election | `score(candidate)`, `evaluate(hosts_list)` | ineligible (`-1`). The C++ form is pure: no I/O, probes or threads. |
| Lifecycle | `advertise()`, `hostReset()` | real for any handler that names a role: it writes `<role>/capability` |

A format handler overrides only the codec slot. A pure role handler overrides only the election slot.

The two languages differ in where the lifecycle writes:
- **Python** writes through the api.php tuple store, to both the control and the data database hosts.
- **C++** writes Ribbit memory (`service=<role>`, `variable=capability`, `instance=host:<ip>:<role>`) and raises on a failed write (`unrest_handler.hpp:25-31`).

### R.2 Adding a handler

**Python** (`docs/history/SOTF_HANDLER_INSTALL.md`). Put the handler in `core/`, then make three edits to `core/format_registry.py`:
1. Import the handler.
2. Create one instance and add it to `FORMAT_HANDLERS` under a mode name.
3. Teach `sniff_body_mode()` to recognize its bodies. The sniffer never trusts Content-Type. A handler whose bodies are JSON must be checked **before** the JSON check, or JSON claims them first.

A role handler is registered in `core/role_registry.py` (`ROLE_HANDLERS`).

**C++.** Derive from `frognet::UnRESTHandler`. The C++ engine (`ribbit/cpp/include/fnwp_engine.hpp`) carries the JSON and text handlers, ported exactly from the Python as `semtpl.hpp`, plus `ram_rows`. The XML, HTML and SotF handlers are not ported, and asking for one raises `NotPorted` (`semtpl.hpp:18-41`).

### R.3 The Communicator's media: SotF

`ribbit/cpp/tests/reference/core/sotf_handler.py` (553 lines). This is the reference copy kept for the C++ port's tests. The live file, `core/sotf_handler.py`, was removed in the October 2026 cleanup, along with the `sotf_media` registry entry (`docs/cleanup-2026-10/T2.md:20, 44-45`). The live registry never carried edit 3, so the sniffer never routed a body to it. Today's Communicator uses tuples on the default API, and its own media bearer (FNAV) for the streams (§Q.3).

SotF is the one handler that fills all three slots plus a program factory.

**Codec: the control envelope.**
- **Which bodies.** It claims JSON bodies with the top-level marker `"_sotf": 1`.
- **Template and frames.** The five session-scoped fields (`session_id`, `codec`, `sr`, `layout`, `level_idx`) are the template. Each frame carries `seq` and `payload`. The first frame sets the template; later frames diff against the peer's reference, so only `seq` and `payload` travel. An identical frame is a repeat, 25 bytes on the wire.
- **Payload.** It travels as native bytes (`TYPE_RAW`), not base64. Every node in a media session must run the same handler.

**Data plane: live audio and video frames.** These do not go through the convergence codec at all: live AV never repeats tick to tick, so diffing it is pure cost.
- `pack_frame()` puts a fixed header (`seq`, `is_key`, `audio_len`, `video_len`) in front of the audio and video.
- They are sent fire-and-forget.
- The same handler owns both planes.

**Role:** `mediahost`, candidate `MediaCandidate`, port 9000.
- It ranks on static capability, not load. Load and temperature swung the score elevenfold and made the elected media host thrash (`[MEDIAHOST_STATIC_RANK_V1]`).
- It is elected from the LAN only.

**Program:** `open_stream()` returns the media codec object, with:
- `send_av` and `recv_av` for frames;
- `store_control` and `read_control` for the envelope;
- `read_livestreams`;
- `set_ffmpeg_options`;
- `conditions_changed`.

### R.4 LISP

`ribbit/examples/lispers.net/ribbit_cpp/lisp_handler.hpp:18-200`. A C++ handler with the same shape as SotF.

- **Identity.** Role `lisp`, candidate `LispCandidate`, port 4342.
- **Codec.** A mapping record as JSON, with fields in this order: `iid`, `eid`, `ttl`, then `rlocs` (each `address`, `priority`, `weight`). A body that is not a mapping record raises.
- **Election.** A node can serve the LISP boundary only if its capability says UDP 4342 is open and it has a public address. Those are two new capability fields, `lisp_udp_4342` and `public_ip`, which `frognet_capability_probe.sh` does not publish yet. Ranking is on static capability. `evaluate()` uses the hosts list that includes WAN hosts, because the boundary faces the Internet (the opposite of `mediahost`).
- **Programs.**
  - `open_site()` returns an ETR/ITR participant holding its own engine on the RAM.
  - `open_boundary()` returns the interop edge: its own engine and a UDP socket. A Map-Register gets a Map-Notify back to the sender; a Map-Request gets a Map-Reply or a negative reply. Site and boundary coordinate through memory and take no locks.

### R.5 RAM read answers: `ram_rows`

`ribbit/docs/RAM-ANSWER-HANDLER-SPEC.md`; `ribbit/cpp/include/ramrows.hpp`; used at both ends of the FNWP wire (`fnwp_engine.hpp:255, 288, 299-301, 453`).

- **Why a handler of its own.** A read answer is `{"ok":true,"rows":[…]}`. The JSON handler would learn `rows` as one atomic array, so any change to any cell re-sends every cell.
- **No learning.** The template is the reference answer both ends already hold for that request: the last one sent or received on the session. Both ends therefore derive the same field order.
- **Fields.** Each row is keyed by its address. Its fields are `<n>.id`, `<n>.updated_epoch`, and `<n>.bag.<path>` for every member of the bag, with nested objects flattened and arrays kept atomic.
- **Cannot hold.** A different address set, or a bag whose member paths differ, cannot be held. The answer goes raw and becomes the new reference.
- **Exact rebuild.** The rebuild reproduces the server's text byte for byte. Numbers travel as float64 and are printed the way the server prints them; ids are integers; `updated_epoch` is the server's `%.3f` text.

### R.6 Primed templates: FD&F

`opt/fdf/templates/fdf.prime` (21 exemplars); `rinab/tools/build-prime.py` (`[FDF_PRIME_V1]`).
- **Recording.** A learning run records exemplars (`RIBBIT_TEMPLATES_RECORD`).
- **Building.** `build-prime.py` keeps one exemplar per (path, op, field shape). Two bodies of one op with different fields are different templates, such as a program's control hello and its data-set hello.
- **Loading.** `RIBBIT_TEMPLATES` names the store a program loads.
- **What priming does not do.** A primed dictionary supplies the shapes in advance, but it does not make the first message compact. The first message of each instance still carries all of its fields.

---

## Appendix S. John Day's DAF model on Ribbit

A working prototype of the distributed-application model in the RINA Reference Model, Part 2, Chapters 1 and 2 (John Day; Day and Trouva), built as a region of the FD&F RAM host. The plan it implements is *The RINA DAF Model on Ribbit: A Prototype Plan* (2026-10-16). Build `fdf-2026-10-16-r21`.

**Where it is.** All paths are relative to `opt/fdf/src/fdf-source/rinab/`.

| What | File |
|---|---|
| The region: every operation, the check point, the DAF file | `src/daf_region.cpp` (`[DAF_MODEL_V1]`) |
| Its entry points in the host | `src/door/rina_region.h`; dispatch in `tools/make_host.sh` (`op=daf`); session end and start in `src/rina_region.cpp` |
| The client library: a session, watching, working sets, find-and-join | `src/daf_client.hpp` |
| Tests, one program per milestone | `tests/daf_names.cpp`, `tests/daf_membership.cpp`, `tests/daf_information.cpp`, `tests/daf_reach.cpp`; run by `tools/daf-check.sh` |
| The hospital capacity manager: the scripted incident and the board | `examples/hcm/hcm_incident.cpp`, `examples/hcm/hcm_board.cpp`; run by `tools/hospital-demo.sh` |

### S.1 Shape

- **One region operation.** `op=daf` (POST, inline, never waits) carries `{"fn": <operation>, ...}`. It runs in the host, beside the memory, like the RINA region (§Q.5). Answers are `{"ok":true,...}` or `{"ok":false,"error":...,"why":...}`.
- **Everything is a cell.** The model's objects are cells of the generic memory, service `daf` (§S.2). Programs watch by holding a read on the memory itself. Nothing is pushed.
- **One decider.** Every operation runs under one lock (`[DAF_ONE_DECIDER_V1]`, `daf_region.cpp:47`), so each read-decide-write is atomic. The lock is held only for the operation, not while a body is parsed.
- **Liveness is the session.** What a session enrolled, joined, subscribed to or started ends when the session ends (`daf_session_down`, `daf_region.cpp:882`). A member that leaves keeps its synonyms for the DAF's synonym lifetime, for a short rejoin.
- **Permanent cells** (DAFs, names, policies, reference data, persistent memberships and their removals, name spaces) are appended and fdatasync'd to the DAF file before they are written, and replayed at start (`[DAF_FILE_V1]`, `daf_region.cpp:66`). The file is `RINA_DAF_FILE`, or `RINA_REGION_FILE` with `.daf` appended.
- **Coalescing.** `op=daf` is coalesced only within one session (`[REGION_COALESCE_PER_SESSION_V1]`, `tools/apply_region_session_key.py`), because its answers depend on the caller's session.
- **Diagnostics.** `DAF_DIAG=<ms>` logs every call slower than that, with its parse, lock-wait and run times (`[DAF_DIAG_V1]`).

### S.2 Cells

`D` is one DAF instance, `<dan>#<daii>`. A cell that is gone holds `{"gone":true}`: the memory replaces rows, and a write is what wakes a held read.

| Variable | Instance | Holds | Kept |
|---|---|---|---|
| `dafs` | `D` | the DAF: dan, daii, owner, synonym sets, synonym lifetime | Permanent |
| `m\|D` | apn | an enrolled member: role, facts, synonyms, session | session |
| `ret\|D` | apn | a member that left: synonyms and role, until its lifetime ends | until then |
| `s\|D\|<set>` | synonym | who holds the synonym | |
| `n\|D` | name | a whatevercast name: rule, lifetime, designated member | Permanent |
| `e\|D\|<name>` | apn | a member of the name: facts, reference | session, or Permanent |
| `nv\|D` | name | written on every change to the name: **hold a read here to watch it** | |
| `pol\|D` | key | a policy (§S.4) | Permanent |
| `audit\|D` | n | one record per check-point decision | |
| `ref\|D` | key | reference data given at enrollment | Permanent |
| `ae\|D\|<sid>` | AE name | a connection's granted object set | session |
| `fl\|D`, `box\|D\|<apn>`, `fd\|D\|<fid>\|f\|r` | | a flow, a member's inbox of flows, the flow's data each way | |
| `subd\|D` | key | a subscription's delivery: hold a read here | while held |
| `in\|D\|<apn>` | key | values distributed to the member | |
| `out\|D` | key | a started script's result | |
| `nsp\|D`, `dlg\|D\|<space>`, `nsn\|D\|<space>` | | name spaces, delegations, names | Permanent |
| `req\|D\|<apn>`, `ans\|D`, `store\|D\|<apn>` | rid | requests from non-members, their answers, stored messages | |
| `prop` | `D` | a DAF proposed by several owners | Permanent |
| `#daf` service | | secrets, never served over the network: authentication keys, owner keys, connection tokens | |

### S.3 Operations

Numbers in brackets are lines of `src/daf_region.cpp`.

| Section of the plan | Operations (`fn`) |
|---|---|
| A. Application naming | `create_daf` (184), `list_dafs`, `enroll` (355), `synonym` (431), `members`, `whoami` |
| B. Whatevercast names | `define_name` (221), `join` (233), `unjoin`, `designate`, `resolve` (298), `names`. Rules: `all`, `one` (filter, rank, ties to the lowest apn), `designated`, `nearest`. Lifetime: `session` or `persistent`. A new DAF has `members`, `join` and `service` (Part 2-1 §4.2). |
| C. Enrollment | `enroll` in six steps (state, role, synonyms, static data, connections, state and the write to continue from); `leave`; `set_ref`; `export`, `merge` (543), `conflicts` for partitions |
| D. Application-Entities | `connect` (464) with an object set, an authentication module and, from another session, the member's token; `disconnect`; `set_objects`; `set_auth`; `flow_open` (to a member or a name), `flow_send`, `flow_close` |
| E. RIB Daemon | `subscribe` (601: state or collect, on change or periodic; identical specs are one subscription), `unsubscribe`, `distribute`, `start` (662), `stop`, `stats`; working sets in `daf_client.hpp` |
| F. Name spaces | `ns_create`, `ns_delegate`, `ns_assign`, `ns_resolve` (730) |
| G. DIF Allocator | registration and search through F; `register_owner`, `propose_daf`, `approve_daf` (796); the redirect policy; `daf::find_and_join` in the client |
| H. Security | `set_policy`, `audit`; the check point (`check`, 142; `must`, 167) |
| I. Requests without enrollment | `request` (754, to a name, or stored for a member), `answer` |

### S.4 The check point and policies

Every operation of a DAF, except the few listed below, calls `must()` before it acts. `must()`:
1. refuses a session that is not enrolled with `ENOENT "not found"`, the answer a DAF that does not exist gets (`[DAF_NOT_FOUND_IS_ONE_ANSWER_V1]`, after Part 2-2 §2.6.3); the audit keeps the real reason;
2. asks the most specific policy: `name:<object>`, then `op:<op>`, then `role:<role>`, then `*`. None means open. Modes: `open`, `refuse`, `roles` (allowed roles), `redirect` (refused with an alternative region, `EREDIRECT`), and `objects:<role>` (the most a role's connection may be granted);
3. on a connection (`"ae"` in the request), asks whether the object is in the granted set, and if not, consults `op:outside_object_set`: open by default (allowed and recorded), refused when that policy says so;
4. writes an audit record: who, role, operation, object, decision, reason.

Not behind the check point: `create_daf`, `list_dafs`, `whoami`, `version`, `stats`, `disconnect` (the caller's own connection), `register_owner`, and `propose_daf` and `approve_daf` (owner keys decide). `merge` into a host that does not hold the DAF yet is an import and needs no enrollment.

As built, every policy is open unless one is set. That is the plan's "access control designed in, not yet enforced".

### S.5 The client library (`src/daf_client.hpp`)

- **`daf::Client`**: one session. `call(fn, fields)` returns the answer whole. `watch(D, name, rev, s)` is a held read on `(daf, nv|D, name)`.
- **`daf::WorkingSet`**: a read cache. Names resolved once and kept current by a held read per name, each on its own session connected as the member (its token). Reads are local and can trail the RAM manager's copy by one change's arrival time.
- **`daf::find_and_join`**: resolve an application in a directory DAF's `apps` space, then enroll at the region it names. Refusal and absence give the same answer.

### S.6 The hospital capacity manager (`examples/hcm/`)

- **`hcm_board`** enrolls in the incident's DAF as `board`, holds one subscription to `facilities` and `receiving`, and serves a page and `/state.json`: each hospital connected or offline, its trauma capability and capacity, the current trauma choice, and the incident's events.
- **`hcm_incident`** is the plan's demonstration: nine steps covering every section, each checking what the system answers and what the board shows. `HCM_PAUSE_MS` pauses between steps for a person watching.

### S.7 Verification

| Run | Result (2026-10-16) |
|---|---|
| `tools/daf-check.sh`: two RAM hosts; phase 1, kill -9 and restart of host A, phase 2 | 55 checks pass: A 4, B 14, C 7, D 5, E 6, F 6, G 4, H 5, I 4 |
| `tools/hospital-demo.sh 3` | 3 of 3 runs pass, 19 checks each |
| Regression: `memory-order-check.sh`, `map-removal-check.sh`, `map-region-check.sh`, `mapwait-check.sh` | all pass, before and after |
| `tools/long-leaf-check.sh` (§S.8) | 900,048 answers identical |

Measured on loopback, one machine:
- A periodic subscription delivered at 0.202 s intervals for a 0.2 s period.
- 50 holders of one subscription: 5 changes cost 5 computations; all 50 woke.
- Working-set reads: 0.6 µs p50, 0.8 µs p99, no request sent; a change reached the set in about 1 ms.
- Short rejoin with 64 reference rows: 0.9 ms against 3.8 ms for a full enrollment.
- Small requests during a 32 MB transfer on a **separate connection**: p99 0.6 to 4 ms, against 0.4 to 2 ms alone, over seven runs (final run: 0.47 ms alone, 2.44 ms during). The separate connection reduces the interference but does not remove it; the plan's pass condition, "unchanged within measurement error", is not met as written, and the test passes on p99 below max(20 ms, 5 x the unloaded p99). With the transfer on the **same session**, p99 ranged from 0.7 ms to 62 ms between runs: a session runs its requests in order (`[SESSION_ORDER_V1]`), so a small request issued while a 1 MB request is in flight waits for it. Day's prescription, separate connections for short and long responses (Part 2-1 §4.2), is the remedy, and is what the test uses.
- A request from a non-member, answered by a member: 1 ms.

### S.8 A platform change found by the bulk test

`[LONG_LEAF_ONE_PASS_V1]` (`tools/apply_long_leaf.py`, applied to `semtpl.hpp` at build):
- **Before.** A 1 MiB value cost 630 to 690 ms to write on loopback, through the raw memory API or a region operation alike. Both ends were on-CPU in `semtpl::leaf_type`. Every template learned from a body types every string leaf, and for each one `is_ipv4`, `looks_like_interface`, `looks_like_enum` (twice) and `is_plausible_host` decoded the whole string into code points, stripped it, and upper-cased it through a table.
- **Change.** For an ASCII string longer than 64 bytes the same answer is reached in one pass: it cannot be an IPv4 address, and it is a host only under conditions checkable in one scan. A string with any byte ≥ 0x80 takes the full path unchanged.
- **After.** A 1 MiB write takes about 105 ms. `tools/long-leaf-check.sh` builds `leaf_type` both ways and compares them on 300,016 strings under three paths: 900,048 answers, 76,536 of them hosts, none different.

### S.9 Where it departs from the text, and its limits

| Item | As built |
|---|---|
| Replication | One copy at the RAM manager: replication of degree one. Day's RIB is "a partially replicated database" for most DAFs. |
| RIB in each member | A member's RIB is a reference to the one shared copy: reads and writes execute at the RAM manager. The exception is a working set (§S.5): a read cache of declared names, refreshed by held reads, whose local reads can trail the copy by the time a change takes to arrive (1.2 ms measured). Writes always go to the copy. |
| Partitions | Each island keeps its own copy. Rejoining is an explicit `export` and `merge`, given the time of the split. Session state is rebuilt by re-assertion, never merged. |
| Who may read cells | `op=daf` enforces membership and object sets. The generic memory itself does no authorization, by design (§9.1, R-S1), so anything that reaches the RAM port can read service `daf` directly. Private secrets live in `#daf`, which the network cannot read. |
| START | A restricted script (from a name: filter, a fact, count, sum, min, max, avg or list), once, periodic or on change. Not arbitrary code. |
| DIF Allocator: create | Creates a new DAF on one RAM host once all owners approve. It does not start a new RAM manager. |
| Authentication | Modules `null` and `key` (HMAC-SHA-256 with nonce, replay refused). Sessions are not encrypted (§9.2). |
| Growth | Audit records, tombstones and answered requests are not compacted, like the rest of the memory (§D.1). |
| Concurrency | One lock for every DAF operation. Correct, and fast enough at these scales; not measured beyond them. |
