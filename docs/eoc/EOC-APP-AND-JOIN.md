# EOC: the app and how people join

2026-10-31 · John Fawcett, with Claude. The live copy is a Claude Doc; this file is its export, kept in the tree so the plan travels with the code.

## The app

One icon on the phone or the PC: **EOC**. It opens to the work, and the person never reads the words FrogNet, region, pond, Ribbit or invitation. Joining is scanning a code or typing a short one; everything else happens underneath.

The screens are the DisasterComm apps (`DC_APPS_V1`, `opt/fdf/www/dc` on the `fdf-carry` branch) plus what RangerTrak does today:

| Screen | Who writes | What |
| --- | --- | --- |
| Help requests | The person needing help, or someone for them | Name, how many, needs, status (open / safe / resolved), location, contact |
| Responders | Each team, ambulance, equipment holder, volunteer | Kind, capabilities, availability, crew, location |
| Hospital capacity | Each hospital | Receiving status (open / limited / diversion / closed), capabilities, beds |
| Coordination | Whoever acts on a request, per unit | Assigned, en route, on scene, assisted, transported, released |
| Chat | Each sender | Text for communities, field teams and coordination centres |
| Positions | The scribe at the post, or the device itself | Where a team is, when, condition; lat/lon, MGRS, UTM, Maidenhead, Plus Codes |
| Radio log | The scribe | ICS-309 entries; ICS-213 messages |

Every truth has one owner and the owner writes its own cell; nobody edits anybody else's record. A request's state is read, not stored: its own status plus the newest action from each unit. Which screens a device shows is decided by the class it registered as (below), in the app.

## Two shapes, one cell model

At the post there is a node. The scribe's laptop is on its Wi-Fi, an ordinary DHCP client, and the right shape there is what `DC_APPS_V1` already is: pages the node serves, writing cells into the node's RAM. Nothing to install; every device at the post sees the same roster, radio log and positions on its held read.

In the field, with no node nearby, the phone or PC is a member of the region through the hub: that is the EOC app. Same cells, same owners, so the two shapes cannot disagree.

RangerTrak's problem, each device keeping its own log and logs merged by hand as files, is gone in both shapes: the cells are the log.

## Why today's bridge and app cannot join an r31 region

The Windows bridge (`fdf-kt-2026-10-11-a15`) and the Android app join by connecting to the RAM manager over the Internet: `etc\ponds.txt` holds `area=host:51888`, and `Member.runOnce` (`member/.../Member.kt`) opens that TCP session first, announces, and only then gets its tunnel from the hubs the region lists (`Leaf.tunnel`, `Hubs.from`).

An r31 region does not answer there. `fdf-region-create` puts the RAM manager on `10.253.253.254:51888` inside the tunnel (`[RAM_IN_THE_TUNNEL_V1]`), and the hub answers only a WireGuard key it was given ahead of time by `fdf-invite` (`[ADMITTED_BY_INVITE_V1]`, `fdf-hub` `read_admitted`). The member's first packet has to be a WireGuard handshake from an admitted key; the session comes after.

So the join has to be turned around: tunnel first, from the invitation; session second, through it. `fdf-invite NAME --leaf` already exists for exactly this and nothing on the member side uses it yet (`grep invite` in `frognet-android/`: nothing).

## Join by invitation

An invitation is one line, `fdfinv1.<base64url JSON>`, made by `fdf-invite NAME [--leaf]` on the region's machine. It carries (`fdf-invite`, `inv = {...}`):

| Field | What |
| --- | --- |
| `area`, `site_key` | the region's name and the key that signs mappings |
| `ram` | `10.253.253.254:51888`, the RAM manager inside the tunnel |
| `hub.name`, `hub.pubkey`, `hub.endpoint`, `hub.addr` | the WireGuard peer: its key, its `ip:port` on the Internet, `10.253.253.254` |
| `member.name`, `member.edge`, `member.kind`, `member.privkey` | this member's name, its `10.253.X.Y` on the tunnel, `node` or `leaf`, and the private key the hub already admitted |

The member's order of operations, replacing the a15 order:

1. Decode the invitation. Store it once (`var\invite`); the private key in it is this device's key from now on, replacing the one `ensureIdentity` would have made.
2. Bring the forward tunnel up from the invitation alone: `Bridge.setForward(Hub(name, pubkey, endpoint, addr), edge)`, which sends fdf-tun one `set` with the hub as the forward peer, `allowed 10.0.0.0/8`, `given` = the edge. No region has been asked anything.
3. Open the member session to `ram` through the tunnel and announce (`Member.runOnce` as it stands: `node`, `announce`, `version`, `watch_participants`, the held wait).
4. `Leaf.tunnel` no longer decides whether a tunnel exists; it only confirms the hub the region lists is the one the invitation named, and says so if not.
5. Session ends, tunnel stays (the hub peers the key whether or not a session is up, `fdf-hub desired()`); the next session reopens through it. A dropped session means not alive, nothing more. Revocation is the other fact: the hub drops the peer and no session can start until a new invitation arrives.

The app's `Identity.kt` and `MemberService.kt` get the same change; `ponds.txt` and `Add a pond` go away.

## Groups and doors

Hand-run `fdf-invite NAME` stays for the few people the admin wants to walk a 600-character line to. Everyone else, victims included, comes in through a **door**: a generic invitation the admin makes once per privilege group and can print as a QR on a poster, a card or a radio script.

- The admin creates a group: a name and a challenge policy. `fdf-group create hospitals --challenge password` prints the group's door, one `fdfinv1` line.
- Challenge policies: `none` (tap and you are in), `name` (type who you are; recorded, admitted), `password` (the admin issues a short per-person code, `fdf-group issue hospitals "St. Mary's ED"` prints `K7Q4-M2`; single use, bound to that name). Codes are hashed at rest and burn on use, so the poster can be public and the code can go over voice radio.
- On first contact through a door the app uses the door's key for one exchange only: it brings the door tunnel up, sends its own freshly made public key, and gets back an individual key's admission, a restricted edge of its own. It drops the door tunnel and comes back on its own key. Everything human-paced happens there: it asks the **registrar** for the group's challenge, puts that one question to the person, sends the answer, and receives its own invitation. The registrar then lifts that edge's restriction. The door is never used again by that device.
- Revoking or rotating a door touches nobody already registered. Revoking a person is `fdf-invite NAME --revoke`, unchanged.
- The registrar is the one thing that writes `admitted/`; `fdf-invite` and `fdf-group issue` are the same code path with the registrar's step done by hand.

New on the region's machine: `fdf-group` (create, issue, list, revoke a door) and `fdf-register`, listening inside the tunnel on `10.253.253.254` beside the RAM manager, nothing outside (`[NOTHING_LISTENS_OUTSIDE_V1]` holds).

Registration through a door, in six steps (the live doc draws this):

1. The app scans the group's door and brings a tunnel up on the shared key.
2. One exchange with `fdf-register`: the device's own public key in, admission of that key on a restricted edge out.
3. The door tunnel is dropped; the device comes back on its own key.
4. On its own restricted tunnel the device asks for the group's challenge and the person answers: nothing, a name, or a one-time code.
5. The registrar writes `admitted/NAME.json`, returns the device's own `fdfinv1` line, and rewrites the edge's rule to full reach.
6. Every later start is a member session on the device's own key: announce, held reads, the screens.

The shared key lives for one exchange; the device's own key carries everything after.

## Joining is a library, not EOC code

How people join is a standard pattern and belongs in the ++ libraries beside API++ (`ribbit/docs/API-PLUS-PLUS.md`), so every app on FrogNet joins the same way and none writes its own. Three pieces:

| piece | where it runs | what it is |
| --- | --- | --- |
| `join(invitation)` | every member: the EOC app, the Windows bridge, the Communicator, a node | decode, tunnel up from the invitation, member session through it; the member's order of operations above |
| `Door` | the member, first run only | one exchange on a door's shared key for the device's own key and restricted edge; the challenge on the device's own tunnel; store the invitation that comes back; the door is never used again |
| `Registrar` | the region's machine | an API++ `Answerer` on service `register`, on its own small RAM manager (second port, hub address) that holds nothing else: holds a read on `register.ask`, checks the answer against the door's policy, writes `admitted/NAME.json`, writes the invitation to `register.answer` for that asker |

The registrar as an `Answerer` means registration is cells, not a new listener: the device writes `(register.ask, <me>, <door>)` with its pubkey, name and answer; the registrar replies in `(register.answer, <me>, <door>)`. The one new listener is that second RAM manager, inside the tunnel, reachable from a door edge and from a restricted edge, and from nowhere else.

What the fabric does not learn from any of this: there is no `kind` or class in the admitted record, the hub's peers or the RAM manager. The admitted record stays what `fdf-invite` writes today: name, pubkey, edge. The class a device registered as is the app's to know and the app's to enforce in what screens it shows and what cells it writes. One writer per truth is a programming rule, as it is everywhere else on FrogNet.

## Constraints

- A door's key is one WireGuard peer on the hub. Two devices on the same door at the same instant collide: the hub's endpoint for that peer flips to whoever handshaked last, and the other gets no answer. The door is used for one exchange, the allocation of the device's own key, so the window is one round trip, and the library retries that exchange itself; the person never does. The challenge and the rest of registration run on the device's own tunnel, where nothing is shared. This is the reason a door is never a device's identity.
- A `none` door on a public poster is the one place anyone can spam registrations. The registrar caps admissions per door per minute and logs the rest.
- On a node's Wi-Fi none of this applies: the phone is a DHCP client and talks to the node's RAM directly. Doors and invitations are for a device on the Internet reaching the region's machine.
- A device joins with the private key its invitation carries. If that device is lost, the admin revokes the name; the person registers again through the door and is a new member with a new key. No key is ever re-issued.

**What a door reaches.** The hub forwards between its peers in the kernel, so a door key or a fresh individual key alone would reach every node and every cell. Neither does: the registrar has its own small RAM manager on a second port on `10.253.253.254`, holding only `register.ask` and `register.answer`, and `fdf-group create` writes one firewall rule (nft on the hub) per door edge, and the registrar one per edge it allocates: that `/32` may reach that port and nothing else. An unregistered device never sees the region's RAM. When registration completes the registrar rewrites that edge's rule to full reach; holding an individual key is not admission to the region, the registrar's rule is. The rules are made by the thing that made the door and the edge; the hub, the RAM manager and the admitted record learn nothing.

**Alive and admitted are two facts.** Alive is the session (`[SESSION_IS_LIVENESS_V1]`): a device whose session drops is not alive, and keeps its peer and its permission to reconnect. Admitted is the peer: a revoked device loses it and no session can start. Neither implies the other.

**Latest values and logs.** Positions, statuses, capacity and availability are latest-value cells. ICS-309 radio-log entries and ICS-213 messages are one cell each, owned by the scribe, lingering by family (`[LINGER_BY_FAMILY_V1]`) so the scribe's machine going away does not take the afternoon's log with it. The durable record is the post's own: the node at the post writes the ICS-309 file from its held read, which is the printed form. Nothing is replayed; the file is what is kept.

## Code findings, from the reading

| Finding | Where | What to do |
| --- | --- | --- |
| A leaf gets two addresses. The RAM manager hands every off-mesh leaf the lowest free `10.252.X.Y` and writes it into its facts; the r31 hub peers an admitted leaf on its `10.253.X.Y` edge only and routes that `/32` on-link. Nodes route to the `10.252` one, which the hub drops. | `rinab/src/rina_region.cpp:610-632` (`[LEAF_ADDR_FROM_THE_REGION_V1]`); `fdf-hub:194-196` | The RAM manager gives an admitted member the edge the hub admitted it on (same machine, `admitted/` by pubkey). Until then the member uses the edge from its invitation and ignores the region's `addr`. |
| The app's built-in first pond is a dead address, `165.232.153.144:51888`. | `app/.../Identity.kt:36` | Goes with `ponds.txt`: the app starts with no region and asks for an invitation or a door. |
| No APK ships; the app is source only, built with an Android SDK (API 34). | `docs/OPERATING.md`, Phones | The EOC story needs a built, signed APK on the download page. |
| The Windows bridge in the checkout is the a15 build, `ponds.txt` join. | `opt/fdf/windows/bridge`, `BUILD.txt` | Replaced by the EOC app's Windows build. |
| `DC_APPS_V1` is on `fdf-carry`, not `main`, and nothing in the gates tests it. | `opt/fdf/www/dc` | Comes to `main` with the app; its cell model is the app's. |
| The member code never saw an invitation. | `grep invite frognet-android/`: nothing | `join()` above. |

## Order of work

Windows first, then Android. Each step is one deliverable that runs.

1. **`join()` in the member library** (`member/`, Kotlin, shared by Windows and Android): decode `fdfinv1`, forward tunnel from the invitation, session through it. Proven on Linux in a network namespace against a real `fdf-region-create` region and `fdf-invite --leaf`, the way the a15 member was proven (`netns-reach-test.sh`, `fdf-member-kt`).
2. **Windows join**: the bridge package (`windows/make_package.sh`, the trimmed OpenJDK 21 runtime, `fdf-tun.exe`) takes an invitation instead of `ponds.txt`. `fdf-tun.exe` already cross-builds here (3.8 MB).
3. **`Door` and `Registrar`**: `fdf-group` on the region's machine; the registrar as an API++ `Answerer`; the door flow in the member library. Proven the same way, in the namespace.
4. **The Windows EOC app**: one window, the screens of the table above, on the same JVM as the member (Swing; the runtime grows by `java.desktop`). The screens read and write the `DC_APPS_V1` cells and hold reads on them.
5. **Android**: the app's `MemberService` and `Identity` take `join()`; `ponds.txt`, `Add a pond` and the built-in pond go; the EOC screens as activities over the same cells; a signed APK.
6. **The RAM manager** gives an admitted member its edge (`rina_region.cpp`); rebuilt and shipped with the next `opt/fdf/dist` bundles.

What each step is tested against is the incumbent suites, extended where the behaviour is new; nothing is accepted on the strength of an AI saying it works.

## What RangerTrak contributes

John Cornelison's [RangerTrak](https://github.com/EOCOnline/rangertrak) (AGPLv3, beta) is the single-device version of the field case: a scribe types what teams say over voice radio, offline, no server. Three parts of it carry over as library code with no framework in them:

- coordinate conversion on the device: lat/lon, MGRS, UTM, Maidenhead, Plus Codes;
- the offline basemap: MapLibre with PMTiles, areas saved beforehand;
- the printed forms: ICS-309 radio log, ICS-213 message.

Everything else, the Angular PWA, the per-device log, the hand-merged backups, the read-only Board over local Wi-Fi, is the part FrogNet removes. That is the conversion story for his site: the TypeScript client was written to work around having no shared state, and the cells are the shared state.
