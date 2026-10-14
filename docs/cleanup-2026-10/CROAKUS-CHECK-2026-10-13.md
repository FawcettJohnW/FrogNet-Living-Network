# Croakus check, 2026-10-13

Book: `opt/frognet_semantic/docs/magnum_croakus/Magnum Croakus.html`, rendered to text (BeautifulSoup, one line per
block) and read whole. Each statement about how the system works now was checked against the executable code in this
tree, with `docs/OPERATING.md` and `CHANGED-FOR-CROAKUS.md` as reference. History passages were checked only for their
label. Paths below are relative to `/home/claude/clean`.

What checks out (no entry): the install command and every installer flag (frognet_install.sh:181-211); the phase
order D1, A0..A3, B, C1..C3, D, E, F, R, F8; the 26 F6 checks plus echo and snapshot (frognet_install.sh:2110-2167);
R runs only when F6 passed; the dbhost timer enabled only on a LAN-only install (frognet_install.sh:2061-2071);
fdf-region-create's steps, its ports, flags, area rule, key, units, hub.env, firewall lines and refusing a second
region (fdf-region-create:26-144); fdf-hub refusing to start without hub.env (fdf-hub.service ExecStartPre);
fdf-region-join and fdf-join (site.env, join.env, fdf-client and fdf-facts units, timer off, daemon restart, 15 s
report); fdf-leave (units, files, timer on, key kept); the .run's arguments, checks and /fdf/ vhost, and its payload
matching the tree (only opt/fdf/BUILD differs); the daemon's start line, LAN-only and region loops, 5 s rejoin, the
key at first region start (config.py:269-301), the old tunnel.conf keys line, channel = one per live hub, edge
10.253.A.B/16, AllowedIPs 10/8, metric 22, the 60 s hold-down, LAN routes with no probe, the handshake gate
(graph_apply.py:18-42); fdf-pond-add / fdf-forward argument order and in-place switch; the hub's ownership, REFUSED
and "no edge" notes; listeners proxy 0.0.0.0:80, daemon 0.0.0.0:9009, Apache 8080/8443, comms 0.0.0.0:8800/8994,
fdf-services.sh 8840/8799; keepalive 2 s / 1 s x3 / 5000 ms both ends; fdf-client 30 s re-assert, fdf-facts 3 s;
the app's built-in pond frognet|165.232.153.144:51888; Kotlin member REQ_RAW only; the databasehost score;
the one role in role_registry.py; the 15 s publish watcher; the C++ Communicator flags, sliders and build tags; the
monitor's snapshot URL, refresh 3 s / 15 s, domain sources and oracles; the dashboard's snapshot-only drawing; the
cleanup gate figures in Spec C 67 (T4a-T6a records); LimitNOFILE absent from the units; the prune of the old
tunnel.conf keys.

---

## 1. getHosts example shows the wrong role and alive on a LAN-only node

- **Where:** Part V, chapter 13, "WORKED EXAMPLE — What getHosts hands back".
- **Quote:** `{ "ip": "10.80.80.1", "name": "HomeBase", "echo": "...", "role": "", "alive": false },` (and the same `"role": "", "alive": false` on ShopBox and MomBox)
- **Verdict:** Wrong. Off a region every row is `role "node"`, `alive true`.
- **Evidence:** var/www/html/frognet_node.inc.php:136-145 (`'role' => $region ? ... : 'node'`, `'alive' => $region ? ... : true`); getHosts.php serves those rows minus guid.
- **Fix:** Replace the three rows with
  `{ "ip": "10.80.80.1", "name": "HomeBase", "echo": "...", "role": "node", "alive": true },`
  `{ "ip": "10.84.84.1", "name": "ShopBox",  "echo": "...", "role": "node", "alive": true },`
  `{ "ip": "10.90.90.1", "name": "MomBox",   "echo": "...", "role": "node", "alive": true }`
  and change the sentence after it to: "Off a region every host the last merge named is a node and alive. On a region node the list comes from the region (role and alive are the member's; a phone or PC reads role leaf), not from this node's walk."

## 2. dnsmasq does not bind all interfaces

- **Where:** Part XII, chapter 36, first paragraph.
- **Quote:** "Node services bind all interfaces. The proxy on 80 , Apache on 8080 and 8443 , the data daemon on 9009 , dnsmasq: what keeps the internet off them is where the node sits, not what they bind to."
- **Verdict:** Wrong for dnsmasq. The merge writes `listen-address=127.0.0.1,<self .1>` and `bind-interfaces`, so dnsmasq answers only on loopback and the node's own .1, never on the uplink.
- **Evidence:** opt/frognet_semantic/discovery/live.py:996-997; usr/local/bin/mergeHostsAndResolv.bash:230-231; book chapter 16 says the same thing ("listen-address=127.0.0.1,<self .1> plus bind-interfaces, which the merge writes").
- **Fix:** "The proxy on 80 , Apache on 8080 and 8443 and the data daemon on 9009 bind all interfaces; dnsmasq binds only 127.0.0.1 and the node's own .1 (listen-address and bind-interfaces, written by the merge). What keeps the internet off the first three is where the node sits, not what they bind to."

## 3. The node's listener list leaves out dnsmasq

- **Where:** Part I, chapter 1, "And it is invisible".
- **Quote:** "On a FrogNet node the listeners are the node's own machinery (its proxy on :80, its web server on :8080 and :8443, its daemon on :9009) and, on a node that hosts the Communicator, its memory and media servers (comms-ram :8800, comms-media :8994)."
- **Verdict:** Incomplete. Every node also runs dnsmasq: DNS on its .1 and loopback, and DHCP on the LAN it serves.
- **Evidence:** discovery/live.py:996-997; OPERATING.md §1 table (dnsmasq on every node); installer F1 enables dnsmasq (frognet_install.sh:2054).
- **Fix:** "On a FrogNet node the listeners are the node's own machinery (its proxy on :80, its web server on :8080 and :8443, its daemon on :9009, and dnsmasq's DNS and DHCP on its own .1) and, on a node that hosts the Communicator, its memory and media servers (comms-ram :8800, comms-media :8994)."

## 4. "Two timers remain" / "Nothing polls" leave out the daemon's timers

- **Where:** Specification C, chapter 66 ("Two timers remain: ..."); Part VII introduction ("Nothing polls; the tunnel daemon acts on region events").
- **Quote:** "Two timers remain: fdf-client checks its mappings every 30 s and re-registers any the region lacks, and fdf-facts probes the uplink every 3 s."
- **Verdict:** Incomplete. In region mode the tunnel daemon also re-runs the capability probe every 600 s, and while the region does not answer it tries again every 5 s. The book says both elsewhere (Part VI, Appendix A, chapter 19).
- **Evidence:** opt/frognet_semantic/internet_tunnels_v3/poll.py:1355-1359 (`FROGNET_CAP_REFRESH_SEC`, "600"); poll.py:1652 (`backoff = 5`, `[REJOIN_EVERY_5S_V1]`).
- **Fix (ch. 66):** "Three timers remain: fdf-client checks its mappings every 30 s and re-registers any the region lacks, fdf-facts probes the uplink every 3 s, and the tunnel daemon re-runs the capability probe every 600 s. While the region does not answer, the daemon asks again every 5 s."
- **Fix (Part VII intro):** "Nothing polls the region while it answers; the tunnel daemon acts on region events, and when the region is gone its routes, names and tunnel go with it."

## 5. Glossary says templates are learned from FULL; the code learns them from RAW

- **Where:** Appendix M, entries "FULL" and "template".
- **Quote:** "FULL — A BLDC-1 verdict: the whole structure, sent when no template fits yet — and learned in the act of sending it." / "template — ... shared by both ends and learned from the first FULL."
- **Verdict:** Wrong, and it contradicts chapters 0.3 and 26. The first exchange of a shape goes RAW and the template is learned from it. FULL is the next exchange: every value, against a template already learned, with no reference yet.
- **Evidence:** opt/frognet_semantic/proxy/transport_semantic.py:2464, 2811 (`_handle_raw_and_learn`), 3060/3152 (`_handle_resp_raw_and_learn` → `learn_templates_from_real`); book chapter 26 ("The first exchange of a structure goes raw ... learns its template from it. The next exchange of that shape is the first FULL").
- **Fix:** "FULL — A BLDC-1 verdict: every value of an exchange whose template is known, sent when there is no reference yet to diff against." / "template — The learned structure of an exchange — which parts are fixed, which vary, and their types — learned by each end from the first (RAW) exchange of its shape and never sent."

## 6. Chapter 38 says the template is learned from the first FULL

- **Where:** Part XII, chapter 38, first paragraph and "The honest boundary".
- **Quote:** "that template, with its reference values, lives in each node's local cache, learned once from that first FULL and never re-sent" and "an observer present from the first FULL, who captures the whole conversation from its start, learns the template in the same act the endpoints do"
- **Verdict:** Wrong. Same as entry 5: the template comes from the RAW first exchange, which carries the original HTTP. The chapter's own next paragraph says so ("after the first exchange, which on a new path is a RAW frame holding the original HTTP").
- **Evidence:** proxy/transport_semantic.py:2811, 3152.
- **Fix:** "learned once from the first, RAW exchange of its shape and never re-sent" and "an observer present from the first exchange of a shape (the RAW frame, which carries the original HTTP), who captures the conversation from its start, learns the template in the same act the endpoints do".

## 7. Glossary presents channel sets as working

- **Where:** Appendix M, entry "channel set".
- **Quote:** "channel set — An independent transport channel to a peer — its own socket, sequence space, and worker threads. Up to three per peer, so a call and a bulk transfer do not block each other."
- **Verdict:** Wrong now. The allocator exists and is unit-tested; nothing calls it, so no second set is ever opened. Chapter 27 says so ("The allocator is unit-tested; nothing opens a set yet").
- **Evidence:** opt/frognet_semantic/proxy/channel_sets.py:180 (`open_channel_set`); no caller of `open_channel_set(` in the tree outside its own docstring and tests.
- **Fix:** "channel set — An independent socket set to a peer, with its own sequence space and threads, as the proxy's allocator defines it: up to three per peer, so a call and a bulk transfer would not block each other. The allocator is unit-tested; nothing opens a set yet, and all traffic uses set 0 (Part IX)."

## 8. Echo identity is not always present

- **Where:** Appendix B, "What it returns", the field table.
- **Quote:** "identity   the node's .1 — always present"
- **Verdict:** Wrong. Field 2 is getEth0Address, the served LAN device's own 10.x address. It is empty while that device has no address: before the first reboot after install, or when NetworkManager has not brought the profile up.
- **Evidence:** usr/local/bin/getEth0Address:55 ("no 10.x address on the LAN dev"); usr/local/bin/getFrogNet.bash (eth0IP from getEth0Address, and the line is not cached while it is empty); frognet_install.sh:2158 (F6 expects `NAME,,...` before the reboot); OPERATING.md §7 ("the echo's .1 field is empty").
- **Fix:** "identity   the node's .1, the served LAN interface's own address; empty while that interface has no address (before the first reboot after install, for one)"

## 9. Appendix H: one refusal does not mark a host

- **Where:** Appendix H, "A miss is not an absence".
- **Quote:** "a host that refused is marked not-FrogNet and not re-probed in later passes of the same merge. The mark is flushed at the top of every merge."
- **Verdict:** Out of date. A mark takes three consecutive definitive failures (refused or no route) spread over at least 15 s, and each mark expires by itself after 60 s. Part VII½ states the current rule correctly.
- **Evidence:** opt/frognet_semantic/core/not_frognet.py:380 (`STRIKES_TO_MARK` 3), :402 (`NF_STRIKE_WINDOW_S` 15), :262 (`NF_RETRY_AFTER_S` 60), :443-515 (`mark_if_definitive`), :536 (`flush`, called from discovery/runmerge.py:59).
- **Fix:** "A host is marked not-FrogNet only after three consecutive definitive failures (refused, or no route) spread over at least 15 seconds. A mark expires after 60 seconds, and every merge flushes the marks at its start. A timeout is never counted."

## 10. The hub interface name for an area with a dot

- **Where:** Part VII chapter 20 ("at most 12, because the hub's interface is wg-myarea"); Specification C chapter 66 ("wg-AREA"); Appendix K ("wg-<AREA> is an interface name"); glossary "hub" ("one interface, wg-<AREA>").
- **Quote:** "myarea is the region's name: letters, digits, . , _ and - , at most 12, because the hub's interface is wg-myarea ."
- **Verdict:** Incomplete. A `.` is allowed in the name but the interface (and the ufw route line) uses `-` in its place. The `wg-<AREA>` pattern is wrong for any area that contains a dot.
- **Evidence:** opt/fdf/bin/fdf-hub:271 (`"wg-" + a.area.replace(".", "-")`); opt/fdf/bin/fdf-region-create:130 (`wg-${AREA//./-}`).
- **Fix (ch. 20):** add "A . in the name becomes - in the interface: area my.area is served on wg-my-area , and that is the name the ufw route line prints." **Fix (glossary "hub"):** "one interface, wg-<AREA> (a . in the area becomes -), on UDP 50889".

## 11. Sysctls are not set in phase B

- **Where:** Part IV, chapter 9, phase list.
- **Quote:** "B — Pre-extraction setup. Directories, kernel sysctls, the iptables scaffold, the sudoers entries the daemon needs — laid before the world."
- **Verdict:** Wrong. Phase B is the iptables scaffold (B2), directories (B3) and sudoers (B4). The sysctls come from the tree's file in C1a, after C1 has laid the world down.
- **Evidence:** usr/local/bin/installer/frognet_install.sh:733, 759, 781 (B2-B4); :920 (`C1a: sysctl (the tree's 90-frognet-forwarding.conf)`).
- **Fix:** "B — Pre-extraction setup. The iptables scaffold, directories and the sudoers entries the daemon needs, laid before the world." In C, after "Unpacks frognet_world.tgz (or copies a repository checkout)" add ", applies the tree's kernel sysctls".

---

History passages: labelled. These are the broker history in chapter 19, the 17 August scan in chapter 36, the Python Communicator ladder in Appendix J step 6, "Where fifty-eight came from" in Appendix K, the glossary's "broker", and the SotF and mediahost past-tense passages.

Checked 196 claims; 11 problems
