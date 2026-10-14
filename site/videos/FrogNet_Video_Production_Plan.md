# FrogNet — Video Production Plan

Engineering demonstrations, not tutorials. Every video is evidence.

---

## The style (read before shooting anything)

**Five beats, every video, in order:**

1. **Claim** — one sentence, falsifiable.
2. **Experiment** — the actual thing, run live, unedited.
3. **Observation** — what the screen/monitor/capture shows, read out plainly.
4. **Explanation** — why it happened, one layer deep.
5. **Reference** — where it's written down (Magnum Croakus §, DOCTRINE, the code).

**The look — Bell Labs, 1975, not YouTube.** Screen, terminal, monitor, Wireshark, phone, graphs, numbers. No music, no drone shots, no transitions, no "subscribe," no hype. The message is "here's how it works," not "here's a product."

**The honesty beat is mandatory.** Any video that makes a claim also states a limit — what *isn't* solved yet, or what an attacker *can* still do. The website earned its credibility by volunteering the broker and labeling estimates; the videos keep that reflex or they'll claim more than the page does and get caught for it.

---

## Accuracy guardrails (say it right on camera)

These are the exact places a sharp viewer catches an overclaim in real time. Hold to them.

- **"No attack surface" is scoped, never absolute.** The line is: *no discoverable application endpoints, no exposed application protocols, no remotely reachable application interfaces.* Do **not** say "the attack surface is gone" over an nmap or capture that shows anything answering. Say "that's what we mean by no attack surface" and point at the scope.
- **It sends only what's relevant — it does not compress JSON.** The win is memory-diffed exchange (SAME/DIFF against a shared structure), not a better zip. Never frame the point as "compression."
- **FrogNet does not encrypt.** WireGuard (user-supplied) does the bearer crypto; FrogNet's security is *architectural*. Never say BLDC-1 or the codec encrypts anything.
- **Wireshark shows two different things — name which.** On the public-internet leg it's WireGuard **ciphertext** (nothing to read). On a plaintext LAN pond it's **FNWP semantic diffs** (no REST URLs, no JSON, no API — but not encrypted). You cannot call the same capture both opaque and readable; show both legs and label each.
- **Graded numbers stay graded.** The ~89% is a did-vs-would **estimate** against published WebRTC bandwidth for the same session — not a packet capture. Say so on screen the first time it appears.
- **Don't touch a healthy route on camera as if it were dynamic churn** — incumbency holds; that's the design, not a bug to explain away.

---

## Canonical numbers (use these exact figures)

| Fact | Figure |
|---|---|
| Flagship call | 1280×720, 22 fps, bidirectional, no delay |
| Bearer | 900 MHz radio, ~1 Mbit ceiling |
| HD holds down to | ~850 kbps (L7); degradation not visible until 450 kbps |
| Observed ladder | 987 → 637 → 450 kbps, in-session recovery, no redial |
| Bandwidth saved | ~89% did-vs-would estimate (published WebRTC), ~6× |
| Backgammon turn | 40,011 bytes traditional → 1,981 FrogNet, ~20×, real engine |
| BLDC-1 production average | 93.8% |
| UnREST typical | ~3.4× vs a competent delta protocol; far more vs a naive one; ~44× narrowband |
| Track record | ~18 months cross-internet; ~10 years at home |
| Independent loop | Apr 17 2026 — NY actuator through the Seattle DB, ~2,400 mi; Queens 900 MHz drive at traffic speed, no drops |

---

## VIDEO 1 — What is FrogNet? (4–6 min, full shot list)

**Claim.** Most networks expose applications. FrogNet exposes a networking fabric instead — and it still carries a live HD call over a starved link.

| # | Shot | On screen | Voiceover (tight, no filler) |
|---|---|---|---|
| 1 | Black, then title card | `FrogNet — the network with no attack surface` | "Most networks expose applications. FrogNet doesn't." |
| 2 | Same card, sub | the three-line scope | "No discoverable application endpoints. No exposed application protocols. No remotely reachable application interfaces. It exposes a networking fabric." |
| 3 | Cut to the phone, mid-call | live 1280×720, both directions, call chip visible | "And yet — this is a live HD call, both directions, no delay." |
| 4 | Pull to show the bearer | link readout ~850 kbps, `900 MHz` | "Over an 850-kilobit link. We'll show you the capture later so you don't have to take my word for it." |
| 5 | Roadmap card | bullet list | "Today: hardware, installation, discovery, semantic transport, UnREST, AI — and why the attack surface, scoped the way we just defined it, disappears." |
| 6 | **Limits beat** | plain text card | "One honest note up front: 'no attack surface' does not mean nothing on the internet. There's exactly one coordination point — a broker behind a silent WireGuard endpoint — and getting in takes a minted credential, not a found port. We'll come back to that in the security video and not hide it." |
| 7 | Cut to terminal, cursor blinking | `$` | "Let's build one." |

**Reference on end card:** *Magnum Croakus — the build manual · DOCTRINE · the code. Every claim in this series is cited.*

---

## VIDEO 8 — Security (the important one, full shot list)

**Claim.** A normal web app hands a scanner a map. A FrogNet hands it nothing to talk to — and the call keeps running the whole time.

| # | Shot | On screen | Voiceover |
|---|---|---|---|
| 1 | Terminal, ordinary web app | `nmap` against a normal host | "This is a normal application server. Watch what a scan finds." |
| 2 | nmap results scroll | open ports, HTTP, a Swagger/OpenAPI hit, an API path | "Ports. A web server. A documented API. Endpoints an attacker can enumerate, version, and probe. This is the surface." |
| 3 | Cut — same scan, FrogNet node | `nmap` against the FrogNet | "Same tool. Now against a FrogNet node." |
| 4 | nmap returns nothing useful | no application ports; WG port gives no reply | "No application ports. The one thing facing the internet is a WireGuard endpoint, and it doesn't answer a stranger — to the scanner it isn't there." |
| 5 | **Observation, said plainly** | freeze the empty result | "There is no discoverable endpoint, no exposed protocol, no remotely reachable interface. That is what we mean by no attack surface. Precisely that — not magic." |
| 6 | Wireshark, internet leg | WireGuard packets, opaque | "Capture the internet leg and it's WireGuard ciphertext. Nothing to read — that's the tunnel, which FrogNet does not provide; you do." |
| 7 | Wireshark, LAN pond leg | FNWP frames, no URLs/JSON | "Now capture a local leg in the clear. Still nothing an attacker uses: no REST URLs, no JSON, no API — just semantic diffs against state the far end already holds. Readable bytes, meaningless without the memory." |
| 8 | Split screen: capture + phone | call still live | "And the whole time — the call never stopped." |
| 9 | **Limits beat (do not skip)** | plain text card | "What can still get you in? A stolen or leaked credential — a WireGuard key or an enrollment token — or compromising the broker host itself. That's remote and it's real. It is a much smaller, nameable surface than 'every service you expose' — but it is not zero, and we won't say it is." |
| 10 | End card | citation | "That's the claim, scoped and demonstrated. If you think the scope is wrong, the architecture is written down — tell me where." |

**Reference on end card:** *Magnum Croakus § on the proxy/daemon and the wire · DOCTRINE (Security posture).*

> **Rewrite of the plan's original closer.** The draft ended "The attack surface is gone." Replaced above with the scoped version, because shot 4 shows the broker/WG endpoint existing — an unscoped absolute contradicts the very frame playing underneath it.

---

## VIDEOS 2–7 and 9 — five-beat outlines

Each carries a limits/accuracy note so the honesty reflex survives to shooting.

### Video 2 — Hardware (5 min)
- **Claim:** transport and hardware don't matter; the network forms over whatever's there.
- **Experiment:** show a Pi, a laptop, a NUC, a VM; then Ethernet, Wi-Fi, internet/WireGuard, 900 MHz. Show the topology.
- **Observation:** the same fabric over mixed bearers in one pond.
- **Explanation:** connectivity is accomplished *by* transport; multiplexed roles in one flat 10/8.
- **Reference:** Magnum Croakus (bearers/transport).
- **Limits/accuracy:** a FrogNet's size is bounded by its hardware — most of all the elected database host. Say it. And **HAM is out** — never use it as a bearer example.

### Video 3 — Installation (10 min)
- **Claim:** you don't install software; you bring a node into a living network.
- **Experiment:** fresh machine, run the installer; narrate certificates, services, proxy, daemon, broker, database.
- **Observation:** the monitor opens — no neighbors yet.
- **Explanation:** the node *becomes* the box's networking (.1 identity, AP, DHCP/DNS, port-80 intercept, routes, elections).
- **Reference:** Magnum Croakus (standing up a node) — this is the `standing-up-a-node.mp4` install walkthrough.
- **Limits/accuracy:** no pip-install exists or should; the unit of the product is a *network*, so a one-box install is a network of one until a second node arrives.

### Video 4 — Discovery (15 min, the best demo)
- **Claim:** it self-forms, self-heals, with no operator commands.
- **Experiment:** one node → add a second → hosts, routes, broker election, tunnel appear. Then **unplug**: two independent networks, both usable. **Reconnect:** merge, election, heal.
- **Observation:** the monitor shows exactly what FrogNet currently believes.
- **Explanation:** current-value-wins; convergence is the means, operation is the goal — not consensus, not CRDTs.
- **Reference:** Magnum Croakus § 16 (split, merge & why it's safe).
- **Limits/accuracy:** during the split, name what each side *can't* see yet — don't imply the partition is invisible. Incumbent routes hold; don't stage fake churn.

### Video 5 — Semantic Transport (15–20 min)
- **Claim:** the wire carries only what changed.
- **Experiment:** normal HTTP first (REST/JSON, everything resent). Then semantic mode: FULL / SAME / DIFF, FNWP frames, live byte/RTT counters, optional Wireshark.
- **Observation:** the byte count collapses; the socket stays full instead of idle between round trips.
- **Explanation:** it **sends only what's relevant** — memory-diffed against a shared structure. *Not* compressing JSON.
- **Reference:** FNWP, proxy, daemon, template engine (Magnum Croakus).
- **Limits/accuracy:** the win needs a cooperating FrogNet on **both ends**; against a plain public server you get plain HTTP with none of it.

### Video 6 — Basic UnREST (15 min)
- **Claim:** coordinate by shared memory, not messages.
- **Experiment:** backgammon. Traditional: message/reply per move. UnREST: one board, everyone reads state, nobody sends moves; add spectators at zero extra cost.
- **Observation:** 40,011 bytes → 1,981 on a real turn (~20×), spectators free.
- **Explanation:** tuple-space (Linda homage); the wire carries diffs so the far copy converges on the near.
- **Reference:** Magnum Croakus (UnREST core; REST vs UnREST side-by-side).
- **Limits/accuracy:** the ratio depends on the workload; the backgammon figure is one measured case, not a universal constant.

### Video 7 — Advanced UnREST (20 min)
- **Claim:** independent applications cooperate through shared state without knowing each other exist.
- **Experiment:** sensors, actuators, AI, phone, media, tasks writing/reading the same tuples; dashboard + phone + AI on one structure.
- **Observation:** multiple independent writers, everyone reading current value, no direct calls.
- **Explanation:** the transient DB is a *role* (floats to highest IP), not a store; PERM is the authority.
- **Reference:** Magnum Croakus (UnREST Unleashed; roles/elections).
- **Limits/accuracy:** a network may have more than one AI host; AI hosts do **not** join the services election.

### Video 9 — Putting It Together (one uninterrupted take)
- **Claim:** everything, at once, survives everything.
- **Experiment:** fresh Pi → install → join → discover → phone → sensor → AI → split → heal → semantic compression → shutdown → restart. Phone never drops.
- **Observation:** continuous operation across every disruption.
- **Explanation:** one architecture, not a stack of features.
- **Reference:** "Everything here is documented, every claim cited, the code is available. Read Magnum Croakus, then tell me where I'm wrong."
- **Limits/accuracy:** keep one visible imperfection in the take (a ladder step-down, a brief reconverge) — a flawless reel reads as staged; a real recovery reads as true.

---

## Production checklist (per video)

- [ ] Claim is one falsifiable sentence, on screen.
- [ ] Experiment is live and unedited (or the cut is disclosed).
- [ ] Numbers on screen match the canonical table; estimates labeled.
- [ ] "No attack surface" appears only in its scoped form.
- [ ] "Compression" not used for the UnREST win; "sends only what's relevant" instead.
- [ ] Wireshark legs labeled (WG ciphertext vs plaintext FNWP diffs).
- [ ] A limits beat is present and honest.
- [ ] End card cites Magnum Croakus / DOCTRINE / code.
- [ ] No music, no transitions, no hype.
