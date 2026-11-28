<!-- Generated from the site page spec-04a-triggering.html (https://fawcettinnovations.com/spec-04a-triggering.html) on 2026-11-15. The site is the rendered edition; this is the same text for reading in the repository. -->

Specification §4a

# Triggering and the merge controller.

§4 specifies the walk; this specifies what fires it. Key words are as defined in §1.2 \[RFC2119\] \[RFC8174\].

## Current implementation clauses: October 12, 2026

## Region events

The daemon receives member, fact and mapping changes, reads the world once and applies the plan in-process. Its per-event WireGuard snapshot is reused until kernel state changes.

## No merge for every region event

runMerge is not forked on each region event. It remains for boot and interface changes. Node generation announces impactful changes from a defined list.

## Facts events

The node’s watcher uses interface/lease events and a debounce; uplink probing runs separately so a fact write does not wait on the gateway RTT.

## Hosts writers

r19-a15 makes runMerge and the daemon name alive leaves from the same published facts. The red-first regression prevents an interface merge from erasing a still-live device’s name.

## Related current material.

[FD&F](https://fawcettinnovations.com/fdf.html) · [Programming](https://fawcettinnovations.com/programming.html) · [Memory contract](https://fawcettinnovations.com/memory-contract.html) · [Evidence](https://fawcettinnovations.com/evidence.html) · [Source baseline](https://fawcettinnovations.com/implementation.html)

The detailed clauses, rationale, source citations and author commentary below remain the mechanism reference. Where the added region or modern Ribbit contract differs, use the implementation-specific scope stated above; the LAN-only and region paths remain separately identified.

FrogNet Specification — Draft 0.9, revision 2026-08-29. Cite a conformance claim against this revision, not against a section number alone.

In this document

1.  [1Scope](#t1)
2.  [2External triggers — the host network stack](#t2)
3.  [3Ordering: name service and the merge's view](#t3)
4.  [4The merge controller](#t4)
5.  [5Internal triggers — the mesh telling itself](#t5)
6.  [6Hygiene requirements](#t6)
7.  [7Non-goals](#t7)
8.  [8References](#t8)

## 1. Scope

Croakus: [§14](https://fawcettinnovations.com/magnum-croakus.html#14-the-walk), [§30](https://fawcettinnovations.com/magnum-croakus.html#30-hostreset-nothing-is-pushed)

§4 specifies the walk and §6 specifies what a pond does when membership changes. This section specifies **what causes either to run**. A conforming implementation has two independent trigger surfaces — the host's own network stack, and the mesh telling itself — and one controller that both of them enter.

Nothing in FrogNet polls for topology change on a timer. Every merge is caused: by a link event on this host, by a peer's announcement, or by an operator. An implementation MUST NOT introduce a periodic merge as a substitute for a missing trigger.

## 2. External triggers — the host network stack

Two independent event sources on the host invoke the merge controller: NetworkManager link events, and dnsmasq DHCP lease events. Both exist because they observe different things. A link coming up is this node's own connectivity changing; a lease being issued is **another node arriving**, which this node would not otherwise see until its next walk.

### 2.1 NetworkManager dispatcher

| Hook                      | Fires on                                                                                                                                                                        | Action                                                                      | State                                                               |
|---------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------|---------------------------------------------------------------------|
| `90-frognet-merge`        | `up` and `down` only; all other states exit 0. The loopback interface `lo` is excluded, and a merge is asked for only when the FrogNet /24 routes or the default route changed. | Drops a merge request for the merge watcher (`/run/frognet/merge_requests`) | Live                                                                |
| `99-ifup`                 | Any dispatcher invocation                                                                                                                                                       | `exec`s `runMerge.bash` in the background                                   | **Removed.** The installer deletes any left by a prior install.     |
| `91-frognet-connectivity` | —                                                                                                                                                                               | —                                                                           | **Removed** from the tree; the prune script deletes it from a node. |

An implementation MUST exclude `lo` from the merge trigger and MUST restrict `90-frognet-merge` to `up` and `down`. A burst of events is collapsed by the merge watcher and, while a merge runs, by the controller (§4a.4.1).

### 2.2 dnsmasq DHCP lease events

dnsmasq invokes a script on every lease transition. The tree ships:

``` code
/etc/dnsmasq.d/frognet_dhcp_event.conf

    dhcp-script=/usr/local/bin/dhcp_tracking.bash
```

dnsmasq calls it with the lease action and the lease itself:

``` code
$1  action     "add" | "del" | "old"   (old = renewal)
$2  MAC address
$3  IP address
$4  hostname   (optional)
```

The handler MUST filter on two axes before invoking the controller, and MUST invoke it on nothing else:

| Condition                                           | Meaning                         | Action                 |
|-----------------------------------------------------|---------------------------------|------------------------|
| `add` of a FrogNet address                          | A peer joined                   | ask the merge watcher  |
| `del` of a FrogNet address                          | A peer left                     | ask the merge watcher  |
| `old`                                               | Lease renewal — nothing changed | Ignore                 |
| Address outside 10/8, or in `10.253.*` / `10.254.*` | Non-FrogNet DHCP churn          | Ignore                 |

**The handler MUST NOT run the controller; it asks the merge watcher** \[LEASE_ASKS_THE_WATCHER_V1\], the same way `90-frognet-merge` does: one empty file in `/run/frognet/merge_requests`, and it returns. dnsmasq runs its `dhcp-script` synchronously and waits for it, so the handler must return at once; and whatever the handler starts runs inside `dnsmasq.service`'s control group, which a new session (`setsid`) does not leave. The reference handler used to start `runMerge.bash` in its own session. To systemd that merge was part of dnsmasq, so when the merge had something to tell dnsmasq — a node had joined, `/etc/hosts` and the forwarders had changed — its own `systemctl restart dnsmasq` stopped the unit the merge was running in: 90 s later (`State 'final-sigterm' timed out`) systemd killed the merge, its Python and its `systemctl`. Measured on 28 November 2026 on five clean-installed nodes: three of seven lease merges ended that way, each with no DHCP and no DNS on that node for 90 s, no merge tail (default route, `resolv.conf`, propagation), and nothing in a log, because the handler sent the merge's output to `/dev/null`. The merge the watcher starts runs in the watcher's unit and writes to the watcher's log.

**Both filters are required.** Firing on `old` makes every renewal a topology event and merges on a timer that is really the lease interval. Omitting the address filter makes any DHCP client on the segment — a phone, a printer — merge the mesh. An implementation MUST log the event and the filter decision; the reference implementation tags every branch through `debugTag`.

`dhcp_tracking.sh` is a one-line `exec` shim onto `dhcp_tracking.bash`. Either path works; **the `.bash` is canonical** and is what the `dhcp-script` directive MUST name.

\[DNSMASQ_USE_EXISTING_TRACKER_V1\] An earlier install wrote its own trigger by heredoc. That version fired on `add` only, applied no address filter, and logged nothing — so a departing peer produced no merge, and unrelated DHCP traffic produced spurious ones. A trigger written inline drifts from the tree it triggers; the shipped node tool does not. The installer now points at the shipped tool and removes `/usr/local/sbin/dnsmasq_merge_trigger.sh` from any prior install.

DEPLOYMENT NOTE — the obsolete `/usr/local/sbin/dnsmasq_merge_trigger.sh` (add-only, unfiltered) is gone from the source tree and the installer removes it from a node (frognet_install.sh), and `frognet_dhcp_event.conf` ships in `etc/dnsmasq.d/`.

## 3. Ordering: name service and the merge's view

Separately from triggering, dnsmasq's unit ordering exists so that a merge reads a settled network rather than a mid-start one:

``` code
[Unit]
Wants=network-online.target
After=network-online.target
       NetworkManager-wait-online.service

[Service]
Restart=on-failure
RestartSec=2
```

A dnsmasq that starts before NetworkManager has settled produces the boot race in which dnsmasq starts before NetworkManager has an address on the interface it binds. An implementation MUST order name service after network-online. **A dnsmasq restart is not itself a topology event** — only a lease transition through §4a.2.2 is.

## 4. The merge controller

`runMerge.bash` is the **single authoritative merge controller**. Every trigger in §4a.2 and §4a.5 enters here and nowhere else. Its contract is: acquire the lock, mint a merge id, install firewall state, flush the discovery caches, run the merge, decide convergence, and announce if warranted. The merge itself — bring-up, discovery, promotion, and the `/etc/hosts` commit — is `discovery.live`.

### 4.0 The merge carries no deferred region work \[NO_BROKER_V1\]

This clause once required the merge tail to retry broker enrolment left pending at install \[DEFERRED_BROKER_SETUP_V1\]. That retry, its sentinel and the broker autoswitch were removed on 12 October 2026 (`runMerge.bash:94`: “the broker autoswitch that stood here is gone”; `frognet_prune_removed.sh`: `frognet-pond-bootstrap.sh`, `frognet-tunnel-setup-v3.sh`, `pond_bootstrap_done` gone). The merge retries nothing on the region’s behalf: a region is joined by invitation (§3.5.6), and in region mode a region event is not a merge at all — the tunnel daemon applies the region’s plan itself (`poll.py` `run_region_loop` \[NO_MERGE_ON_REGION_EVENTS_V1\]). The merge keeps a LAN-only node free of WireGuard (`runMerge.bash:94`).

### 4.1 Re-entrancy

The controller takes a non-blocking exclusive lock on `/var/run/runMerge.lock`. **A trigger arriving while a merge runs MUST NOT queue and MUST NOT block.** It touches `/etc/sentinels/runAgain` and exits 0, leaving a flag for the running instance to observe.

``` code
flock -n 9  ->  held?
                  yes:  touch /etc/sentinels/runAgain
                        exit 0            ("for_running_instance")
                  no:   proceed
```

This is why a second `runMerge.bash` started while one runs (by hand, or by the tunnel daemon) is harmless.

The merge watcher in front of the controller (a 30 s window, reset by each request) does not use the flag \[REQUEST_KEPT_UNTIL_ITS_MERGE_STARTS_V1\]. **A request leaves the watcher's queue only when a merge is started for it.** When the window expires while a merge holds the lock, the requests stay in `/run/frognet/merge_requests`; the watcher waits on the lock (a blocking `flock`: the kernel returns when the running merge exits, there is no polling and no timer), then consumes the batch and starts one merge, which therefore reads the world after every event in the batch. The watcher used to consume the batch and write `runAgain`. The controller reads `runAgain` once, straight after its discovery stage, and holds the lock for 0.8 to 2.2 s more (measured on 16 merges, 28 November 2026); a batch whose window expired in that stretch, or while the lock was held by a merge that was never going to finish, left a flag nothing read, and its event got no merge. That is how a node that had just joined was never learned by its neighbour that day.

### 4.2 The converge loop \[RUNAGAIN_ON_MUTATION_V1\]

A pass that changed a /24 sets `runAgain`. The mesh rarely converges in one pass, because a route this node installed is a fact its peers see next pass. The controller MUST re-invoke itself until a pass changes no /24, or until the depth cap is reached.

``` code
changed_this_pass  ($SYNC_REQUIRED present)   -> chain_dirty = 1
runAgain           ($RUN_AGAIN present)       -> re-invoke

  depth < MAX_MERGE_DEPTH (10)
      exec self, FROGNET_MERGE_DEPTH+1, carrying chain_dirty
  depth == cap
      "runAgain_capped" — treated as converged-enough, and announced
      if the chain was dirty
```

Re-invocation is `exec`, not a fork: the depth counter is the only recursion bound and an implementation MUST carry it in the environment. `chain_dirty` MUST be carried forward across the chain, because the pass that announces is not the pass that changed anything.

### 4.3 Announcing \[PROPAGATE_ON_CONVERGED_V1\]

Announcement happens on the converged pass only, and only if some pass in the chain changed a /24. A node MUST NOT announce a half-converged state: an intermediate pass has installed routes its peers would act on and then revise.

``` code
converged AND chain_dirty  ->  propogateNotification
converged AND NOT dirty    ->  nothing sent ("no_change_in_merge_chain")
```

## 5. Internal triggers — the mesh telling itself

The announcement of §4a.4.3 is an ordinary HTTP request to each peer, which is why it costs almost nothing after the first one (§11.13).

### 5.1 The endpoint contract

``` code
GET /propogateNotification.php?event=<id>

  event absent            -> 400, "missing event"
  event present           -> sanitized to [A-Za-z0-9_.-], other chars to '_'
                             already seen  -> 200, "DUP"
                             else record it in the seen-set and
                                 queue a poke in /run/frognet/merge_requests
                          -> 200, "OK"   (500 if either write fails)
```

The event identifier MUST be sanitized before it is interpolated into a file name. The endpoint MUST NOT block: it returns to the announcing peer immediately and the merge proceeds under the merge watcher, so a slow or unreachable downstream cannot hold the announcer.

### 5.2 Peer set derivation \[NEIGHBOR_SCOPE_V1\]

The fan-out set is the node's **directly attached neighbours**, and nothing else. Epidemic re-propagation from each receiver covers the whole connected mesh, so notifying beyond the adjacency buys no reach and costs fan-out proportional to the fleet.

``` code
LAN neighbours   the distinct next hops (`via X`) of FrogNet /24 routes
                 on a PHYSICAL device.  Every FrogNet subnet reached over
                 Ethernet or Wi-Fi exits via an L2-adjacent neighbour, so
                 a relayed-but-distant node is a destination and never a
                 next hop — it falls out by construction.  Captures both
                 downstream clients and the upstream gateway.

tunnel peers     the identity address of each active tunnel
```

This is **O(neighbours) per node, not O(fleet)**, and an implementation MUST derive the set this way. The earlier behaviour notified every node address in the hosts file from every node, which is the same delivery at quadratic cost.

The derivation reads live routing state, active tunnel state, and the node's local addresses. Where it cannot complete, the failure is logged and that wave is not sent; the next merge’s notification asks again. There is no hosts-file scan behind it.

**Every local address MUST be skipped, not merely the primary one.** A FrogNet host appears under several — tunnel endpoints, secondary aliases, transit addresses — and skipping only the obvious one leaves the rest eligible, so the node notifies itself repeatedly, each self-notification starting a process whose only work is to notice it has already seen the message.

### 5.3 Suppression and dedup

| Rule                | Keyed on                                     | Effect                                                                                               |
|---------------------|----------------------------------------------|------------------------------------------------------------------------------------------------------|
| Forward dedup       | `<host> <ip> <dev>`                          | A key already forwarded is skipped. First-seen keys fire.                                            |
| Pending suppression | Discovery already in flight for that address | The propagate is suppressed rather than firing into a walk that is about to produce the same result. |

Together with the wave identity and neighbour-only fan-out of §11.13, these are what make the epidemic terminate. All of them are required; removing any one produces a broadcast storm rather than a slower one.

## 6. Hygiene requirements

Two requirements on the controller are not about topology and are stated because both were established by defect.

**Bytecode purge** \[PYCACHE_PURGE_V1\]. The controller MUST remove every `__pycache__` directory and `.pyc` file under the code roots before anything runs. A stale `.pyc` from a prior build causes Python to load the old bytecode in preference to the updated source, so a freshly deployed fix silently does not run.

**Working directory** \[MERGE_RUNS_FROM_ROOT_V1\]. The controller deletes whole directory trees and MUST NOT run from one. It pins to `/` at entry. An operator whose shell sits in a matching directory otherwise has their working directory removed underneath them, and every subsequent fork in the merge's process tree emits a `getcwd` failure — hundreds of times, interleaved with the merge log. The merge still completes, but it buries its own log.

## 7. Non-goals

There is **no timer**. Nothing schedules a merge, and an implementation MUST NOT add one: the dnsmasq `dhcp-script` and the NetworkManager up/down hooks are the whole of the external surface, and both ask the merge watcher, which runs the one controller. There is **no queue** in the controller: a second controller started during a merge leaves a flag, not an entry. The queue is the merge watcher's own (`/run/frognet/merge_requests`), and a request stays in it until a merge is started for it (§4a.4.1). There is **no coordinator**: no node instructs another to merge, and an announcement is a notification that something changed here, never an instruction or a derived view (§2d). There is **no delivery guarantee** on an announcement: a peer that misses one discovers the change on its own next trigger.

## 8. References

Specification [§4 (Discovery)](README.md#s4), [§6 (Pond formation)](spec-06-pond.md), [§11.13 (Change propagation)](spec-11-wire.md#w13), [§13 (Failure semantics)](spec-13-failure.md), [§2d (Topology derived, never distributed)](spec-02-invariants.md#b5). Source: `/etc/NetworkManager/dispatcher.d/`, `/usr/local/bin/runMerge.bash`, `discovery/live.py`, `discovery/propagate.py`, `propogateNotification.php`. Build manual: Croakus [§14](https://fawcettinnovations.com/magnum-croakus.html#14-the-walk), [§30](https://fawcettinnovations.com/magnum-croakus.html#30-hostreset-nothing-is-pushed).

[← §4 Discovery and Membership](spec-04-discovery.md) [Index](README.md) [§5 Addressing and Routing →](spec-05-routing.md)
