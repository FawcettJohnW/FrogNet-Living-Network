<!-- Generated from the site page spec-03-node.html (https://fawcettinnovations.com/spec-03-node.html) on 2026-11-15. The site is the rendered edition; this is the same text for reading in the repository. -->

Specification §3

# Node model, identity and bootstrap.

What a node is, how it comes to exist, and how it knows itself. Key words are as defined in §1.2 \[RFC2119\] \[RFC8174\].

Author commentary

The author walking through this material on camera. It is commentary, not specification: where the video and this document disagree, this document governs (§18.8). The full series is at [watch](https://fawcettinnovations.com/watch.html).

What must be hardware, and what only must be software

frognet_install, the prompts, and the pond

## Current implementation clauses: October 12, 2026

## Identity

The member has a persistent GUID. Application endpoints can be published as w:\<guid\>:\<port\> and resolved in a scope. Identity names a participant; an IP locator is its current path.

## Node facts

A Linux node publishes its addresses, served and held leases, uplink, region tunnel, capabilities and generation. It applies its local graph plan using ordinary kernel routes and interfaces.

## Device placement

A PC or phone already inside a live member’s served /24 is on-mesh. Outside it, the region assigns a leaf address and the bridge constructs its hub path. This assignment and alias ledger are real mechanisms; do not describe every address as an allocator-free GUID calculation.

## Install boundary

A clean node is installed with `frognet_install.sh --from-repo`, LAN-only or, with `--invite`, into a region (`docs/OPERATING.md` §2–3). The `fdf-install-<arch>.run` file joins an installed node to a region with its invitation and, re-run with a newer build, updates it. The broker was removed on 12 October 2026. A node’s WireGuard key arrives in its invitation (`fdf-region-join` writes it to `/var/lib/frognet-tunnel/node_private.key`); Windows generates keys on first run.

## Related current material.

[FD&F](https://fawcettinnovations.com/fdf.html) · [Programming](https://fawcettinnovations.com/programming.html) · [Memory contract](https://fawcettinnovations.com/memory-contract.html) · [Evidence](https://fawcettinnovations.com/evidence.html) · [Source baseline](https://fawcettinnovations.com/implementation.html)

The detailed clauses, rationale, source citations and author commentary below remain the mechanism reference. Where the region or the modern Ribbit contract differs, use the implementation-specific scope stated above. Clauses below that name a broker describe the mechanism the region replaced on 12 October 2026 (§7; docs/cleanup-2026-10/T3b.md); the node has two modes, LAN-only and region, and no third (internet_tunnels_v3/config.py \[NO_BROKER_V1\]).

FrogNet Specification — Draft 0.9, revision 2026-08-29. Cite a conformance claim against this revision, not against a section number alone.

In this document

1.  [1The node](#n1)
2.  [2Identity](#n2)
3.  [2aThe `.frognet` namespace](#n2a)
4.  [3Gateway and child](#n3)
5.  [4Bootstrap inputs](#n4)
6.  [5Install-time requirements](#n5)
7.  [6What runs on a timer, and what does not](#n6)
8.  [7References](#n7)

## 1. The node

Croakus: [§4](https://fawcettinnovations.com/magnum-croakus.html#4-one-world-any-box), [§8](https://fawcettinnovations.com/magnum-croakus.html#8-the-world-and-the-installer), [§9](https://fawcettinnovations.com/magnum-croakus.html#9-what-the-installer-does), [§10](https://fawcettinnovations.com/magnum-croakus.html#10-configuring-from-the-browser), Appendix I

A node is a 64-bit host running a Debian-family Linux (Debian 12 Bookworm, Ubuntu 24.04) with a FrogNet stack installed. A node MUST have at least one network interface and MUST be able to serve a /24 from it. A node is a whole network alone; joining a pond widens the set of writers whose values it can observe and changes nothing about how it is programmed (§2e).

## 2. Identity

A node carries **two distinct identities**. They are unrelated, they are used by different subsystems, and conflating them is a documented error.

| Identity     | Is                                                          | Used by                                                       |
|--------------|-------------------------------------------------------------|---------------------------------------------------------------|
| Node GUID    | 32 hex characters at `/etc/fnid`, allocated once at install | The region announcement (`facts.guid`) and rinab's machine id |
| Node address | The `.1` address of the /24 this node serves                | Routing and route source selection (§5)                       |

**Neither is an input to the SAME identifier.** That token is HMAC-SHA-256 over a shared secret at `/etc/frognet/sem_cache_secret` (§11.9). Published material stating that the SAME identifier derives from the GUID in `/etc/fnid` is incorrect.

### 2.1 The node GUID \[NODE_GUID_V1\] \[GUID_IDENTITY_V1\]

Machine identity is an install-time GUID and **MUST NOT be a MAC address**. A GUID is stable across a NIC swap and across `eth0`/`wlan0` failover; a MAC is not, and a node whose identity moves with its hardware does not return to the region as itself (the GUID is the identity the node announces in its facts: `internet_tunnels_v3/region.py` `register_node`).

``` code
value     32 hex chars — a UUID with dashes stripped, or 16 random bytes
          (uuidgen, else openssl rand -hex 16)
path      /etc/fnid
mode      0444
lifetime  allocated ONCE, generate-if-absent, NEVER regenerated automatically
```

The file lives at `/etc/fnid`, **outside `/etc/frognet`**, and MUST do so: a reinstall wipes `/etc/frognet`, and identity that a reinstall destroys is not identity. Mode 0444 makes the file readable but not casually overwritten. Removal MUST be a deliberate act.

Three operations, and they are not interchangeable:

| Operation          | Behaviour                                                                                  | Permitted at                              |
|--------------------|--------------------------------------------------------------------------------------------|-------------------------------------------|
| `--ensure`         | Create if absent, then stop. Returns success if one already exists.                        | **Install only.** The sole creation path. |
| `--read` (default) | Read and print. **MUST NOT create.** Absent or empty is a loud error with a non-zero exit. | Anywhere                                  |
| `regenerate`       | Write a fresh one, saying what it replaced (local only).                                   | Deliberate rotation only                  |

**A read MUST NOT mint an identity.** A freshly minted GUID announces a new node under this node's name: its facts (name, guid, subnet, pubkey) are what the region and its members know it by (`region.py` `register_node`). The correct response to a missing GUID is to fail and say so, naming both remedies — restore the prior value, or rotate deliberately. This is the general rule of §18 applied to identity: a fallback that guesses is the system deciding on the operator's behalf.

In the region the session is the enrolment and the GUID in `/etc/fnid` is the identity (`region.py`: “The session is enrollment; the guid is the identity”). A member’s facts live as long as its session; when the session ends they go with it (§7.1). There is no registration row to revive and none to retire: a returning node announces the same GUID through a new session and is itself again, and rotation is one write to `/etc/fnid` followed by a daemon restart — nothing is posted anywhere. The broker’s `retire-guid` call, which this clause once required, went with the broker on 12 October 2026 (`frognet_prune_removed.sh`: `frognet-guid` removed; `docs/cleanup-2026-10/T3b.md`).

Identity allocation failure at install is **fatal**. It MUST NOT be masked, retried silently, or worked around: a node without identity cannot announce itself in a region (`config.py` logs `CRITICAL: No GUID at /etc/fnid`), and an install that continues past it produces a node that appears healthy and cannot join anything.

### 2.2 The node address

Each node serves exactly one /24 and takes the `.1` address of that /24 as its address identity. That address MUST be the source address for every discovered FrogNet route the node installs.

A node MUST NOT source a discovered route from a transit /30 or from a borrowed uplink lease. Neither is propagated across the mesh, so a host beyond a tunnel cannot route a reply back to it, and the return path fails silently for every child behind the peer — a failure that presents as an application problem several hops away from its cause.

## 2a. The `.frognet` namespace, and why it is not a registered TLD

Node names resolve under `.frognet`. That label is not registered with IANA, is not delegated, and MUST NOT appear in a query sent to a public resolver.

**The obligation this creates is real and it is met by construction.** \[RFC6761\] sets out what a special-use domain name has to guarantee: that queries for it do not leak to the global DNS, and that resolvers, caches and authoritative servers behave predictably when they see one. FrogNet meets those requirements the strict way rather than the registered way — \[NO_FALLBACK_V1\] the name resolves from the hosts file and MUST NOT fall back to DNS (§13.3a), so a `.frognet` label has no path to a public resolver to leak through. There is no split-horizon server to misconfigure and no forwarder to omit.

An implementation that resolved `.frognet` through a stub resolver, a search list, or any path that could reach an upstream server would violate this section, and would leak node names to whoever operates that server.

**An operator who wants a registered name has one**: \[RFC8375\] reserves `home.arpa` for exactly this class of use, and nothing in this specification depends on the literal string `frognet`. The label is a default, a literal in the reference implementation's source (e.g. `discovery/hosts.py`), not a protocol constant.

## 3. Gateway and child

A node with a non-10.x WAN uplink is a **gateway** and MUST advertise transit for its entire downstream LAN subtree. A node without one is a **LAN child** and MUST exclude its mesh-ward uplink device from the interfaces it offers as transit. The role is derived from what the node can see, not configured; a node that gains or loses an uplink changes role on the next merge (§4a).

## 4. Bootstrap inputs

The installer takes **a name and an address**, and one operator-supplied database password. It MUST lay down the stack, the tunnel daemon, the semantic proxy and the backing database, and MUST leave the node in a state where the remaining configuration can be completed over a local browser session. **No configuration file is expected to be hand-edited.**

**The node name has a single source of truth on disk**, and so does the node address. The name is the `domain=` line in the name-service options file; the address is that file's router option, which is by definition this node's `.1`. Every other configuration file has its domain declaration stripped **precisely so that this one cannot be contradicted**. An implementation MUST NOT permit a second file to assert the node name.

On an in-place upgrade the installer MUST recover both from that file and MUST NOT re-prompt \[PRESERVE_KNOWS_ITSELF_V1\]. Prompting is not merely redundant: a typo at that prompt re-IPs or renames a live node, changing its identity (§3.2.1) and its whole /24, on an operation the operator asked for as an upgrade in place. An explicit argument still wins, and where the file is missing or malformed the installer MUST fall through to the prompt rather than guessing — **a wrong identity is worse than a question**.

The collected password is written into every secret-bearing file in one pass, so that the database and every reader of it agree by construction rather than by an operator repeating themselves. \[INSTALL_DB_PASS_PROMPT_V1\]

## 5. Install-time requirements

The reference installer runs forty-one phases in six stages. The stages are informative; the requirements below are not.

| Stage | Covers                                                                                                                                 |
|-------|----------------------------------------------------------------------------------------------------------------------------------------|
| A     | Reinstall reset, system update, APT and pip packages                                                                                   |
| B     | iptables scaffold, directories, sudoers                                                                                                |
| C     | World tar extraction, secret injection, config consolidation, Python venv, **pre-activation simulator gate**                           |
| D     | Interface detection, MariaDB, Apache, dnsmasq, NetworkManager, schema and database user                                                |
| E     | Node identity, hostname, node provisioning (`setup_lillypad_v4.bash`) and WireGuard credentials, SSL, iptables, Wi-Fi AP, link watcher |
| F     | Cache purge, service enablement, logging, verification, reboot                                                                         |

### 5.1 The merge is quiesced for the whole install \[INSTALL_QUIESCE_MERGE_V1\]

The installer MUST hold the merge controller's own lock (`/var/run/runMerge.lock`) for the entire install. The dispatcher hooks it writes fire the controller on any interface event, and the controller rewrites the default route and `resolv.conf` — so a DHCP renewal, a carrier blip, or an interactive prompt simply sitting idle would tear up the route the operator's session rides on.

This introduces no new mechanism: the controller already bails cleanly when the lock is held (§4a.4.1), touching `runAgain` and exiting 0. The installer is one long-lived process, so the lock is held through the final reboot, and the descriptor releases on any exit — success, failure, or kill. `/var/run` is tmpfs, so the file cannot survive to block the next boot.

### 5.2 A reinstall lands on a clean machine \[REINSTALL_RESET_V1\]

The default for a machine that already carries an install is a full wipe. Stale schema, an old GUID, orphaned WireGuard keys and half-written configuration are what produce “it worked on a fresh box but not this one.” The wipe MUST be performed by the tool that owns it, which knows which FrogNet-owned files live inside shared OS directories and never removes those directories wholesale. An in-place upgrade preserving databases and identity MUST be an explicit opt-in.

\[VENV_SURVIVES_RESET_V1\] The Python virtual environment is neither state nor configuration: it is architecture-specific build output, several wheels of which compile on ARM. It MUST be carried across the wipe and restored, and MUST be architecture-checked on restore so that a wrong-architecture environment is still rebuilt.

### 5.3 Stale bytecode \[PYCACHE_PURGE_V1\]

Extraction restores `.py` files with their original mtimes, so a leftover `.pyc` from a prior install can be *newer* than the fresh source and be used in preference to it. Every `__pycache__` directory and `.pyc` file under the code roots MUST be purged after extraction. The same requirement applies at every merge (§4a.6).

### 5.4 The pre-activation gate

Before any FrogNet service is enabled, the installer MUST run the simulator against the real code just installed on this box — the same discovery and orchestration code that will run live — through its oracle proofs. The startup-to-shutdown lifecycle is a development gate and is not run at install.

If the simulator does not pass, **the install terminates and nothing is activated**. The gate MUST default to on, the only means of skipping it MUST be an explicit environment variable, and a skip MUST be logged loudly. This is the last point at which a broken build can be stopped before it becomes a running node.

### 5.5 Nothing is restarted before the reboot \[NO_RESTART_BEFORE_REBOOT_V1\]

The install ends in an unconditional reboot, so a mid-install service restart buys nothing and costs the operator's session: restarting NetworkManager drops a remote SSH connection and truncates the run. Services MUST be enabled rather than restarted, and configuration MUST be written to take effect on the reboot.

### 5.6 Joining a region is by invitation, not polled \[ADMITTED_BY_INVITE_V1\]

The install joins a region only with `--invite fdfinv1.<...>`, the line `fdf-invite NAME` printed on the region host, and only after F6 passed: phase R then runs `fdf-install-<arch>.run` with it (`frognet_install.sh`: “F6 found N failure(s): NOT joining the region on a node that did not verify”). Without an invitation the node is LAN-only; `--region`, `--site-key` and `--area` are refused (`frognet_install.sh:199-201`). A node installed LAN-only joins later with the same file and the invitation.

Deferred broker enrolment \[DEFERRED_BROKER_SETUP_V1\] and its bootstrap timer, which this subsection once described, were removed on 12 October 2026 (`frognet_prune_removed.sh`: `frognet-pond-bootstrap.sh`, `.service`, `.timer` gone). When a joined node cannot reach its region, the tunnel daemon withdraws what the region gave it and asks again every 5 s (`poll.py` `run_region_loop` \[REJOIN_EVERY_5S_V1\]; §7.5).

## 6. What runs on a timer, and what does not

§4a states that no timer schedules a merge. That remains true, and this section states the boundary precisely so the two are not read as contradicting each other.

| Mechanism                | On a timer?                                                                                            | Why                                                                                                                                                                                                                                      |
|--------------------------|--------------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Merge / discovery        | **No**                                                                                                 | Caused by a link or lease event, or by a peer's announcement (§4a)                                                                                                                                                                       |
| Region events            | **No**                                                                                                 | One held `fdf-events` session; each region event is one reconcile (`poll.py` `run_region_loop`: “never a timer”). The one exception is the rejoin after a lost region: every 5 s until it answers (\[REJOIN_EVERY_5S_V1\]; §3.5.6, §7.5) |
| Capability advertisement | **No** — published at every merge (LAN-only); in a region, the daemon's probe at start and every 600 s | Publication of what only this node knows, so the merge-end election has rows to score (§8)                                                                                                                                               |

The distinction is that the advertisers **publish a fact about this node**; they do not observe the network and they do not cause a merge. A host opts out of a role by stopping that role's advertiser. Server software is not a precondition for advertising: eligibility is decided by the capability blob's own gate, not by what happens to be installed.

## 7. References

Specification [§1](spec-01-scope.md), [§2](spec-02-invariants.md), [§4a (Triggering)](spec-04a-triggering.md), [§5 (Addressing and routing)](README.md#s5), [§7 (Region)](README.md#s7), [§8 (Election)](README.md#s8), [§11.9 (SAME identifier)](spec-11-wire.md#w9), [§16 (Simulator)](README.md#s16). Source: `usr/local/bin/frognet-node-guid.sh`, `usr/local/bin/installer/frognet_install.sh`. Build manual: Croakus [§8](https://fawcettinnovations.com/magnum-croakus.html#8-the-world-and-the-installer), [§9](https://fawcettinnovations.com/magnum-croakus.html#9-what-the-installer-does), [§10](https://fawcettinnovations.com/magnum-croakus.html#10-configuring-from-the-browser), Appendix I (Machine Identity), Appendix J (From Clean to the Communicator).

[← §2 Architectural Invariants](spec-02-invariants.md) [Index](README.md) [§4 Discovery and Membership →](README.md#s4)
