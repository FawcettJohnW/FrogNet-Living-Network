# Real-hardware acceptance tests
These need two or more hosts, or a real LAN / Internet path. One container cannot run them: lispers.net binds
`0.0.0.0:4342`, and its `STOP-LISP` kills every process with `lisp-` in its command line on the machine, so a second
lispers.net (a DDT authority, a peer map-server) must be on a second host. Everything else is in `tools/acceptance.py`.

## 1. DDT and map-server peering -- `tools/acceptance_hw.py`
Machines: AUTH (a map-server only, DDT-authoritative for 198.18.0.0/15), MR (a map-resolver whose DDT root is AUTH),
optionally PEER (a second map-server), and the TEST machine the program runs on. A map-server sends its Map-Referral
to the requester's port 4342, so the program listens on 4342 of the TEST machine: run it where nothing else holds 4342.

Configurations -- generated from the suite's fixture, nothing typed by hand:

    python3 tools/make_hw_configs.py --harness-ip <TEST> --auth-ip <AUTH> --mr-ip <MR> [--peer-ip <PEER>] \
        --ram-host <RAM host> --ram-port <RAM port> [--ram-api /ram.php] [--ribbit-udp 4342] --out hwcfg      # <RAM>: the droplet

- lispers.net: copy `hwcfg/lispers-auth/lisp.config` into AUTH's lispers.net directory and `hwcfg/lispers-mr/lisp.config`
  into MR's, then start each with its own RUN-LISP.
- Ribbit: copy `hwcfg/ribbit-auth/lisp-service.config` to `/etc/lispers.d/lisp-service.config` on AUTH and
  `hwcfg/ribbit-mr/lisp-service.config` on MR, then `lisp-service --service` on each (both reach the same RAM host).

Then, from TEST:

    python3 tools/acceptance_hw.py --system lispers.net --local-ip <TEST> --auth <AUTH> --mr <MR> [--peer <PEER>] [--lig <lispers.net dir>/lig]
    python3 tools/acceptance_hw.py --system ribbit      --local-ip <TEST> --auth <AUTH>:<udp> --mr <MR>:<udp> [--peer <PEER>]

| test | what | expected (lispers.net 0.643, observed as a map-server-only authority) |
|---|---|---|
| HW.DDT.1 | DDT-originated Map-Request (ECM, D bit) to AUTH, registered EID | Map-Referral MS-ACK, TTL 1440, the registered prefix, authoritative, referral set = peers (incomplete when none) |
| HW.DDT.2 | same, inside a site, nothing registered | MS-NOT-REGISTERED, TTL 1, the EID /32, the peers |
| HW.DDT.3 | same, outside the authoritative prefix | NOT-AUTHORITATIVE, TTL 0, the EID /32, incomplete, no referral set |
| HW.DDT.3b | same, inside the authoritative prefix, in no site | NOT-AUTHORITATIVE, TTL 0, 198.19.0.0/21 (lispers.net's negative prefix), the peers |
| HW.DDT.4 | an ITR's ECM Map-Request to MR for an EID registered at AUTH | the registered locators (MR follows the referral) |
| HW.DDT.5 | a second request in the same space | recorded: time with the referral cached |
| HW.PEER.1 | a registration at AUTH, asked of PEER | recorded |
| HW.LIG.1 | Dino's `lig <eid> to <MR>` | prints the registered locator |

Where the expectations come from: lispers.net 0.643 run as AUTH with the generated configuration (in the build
container) sent exactly these referrals -- its log records each one and the bytes it sent to the requester's 4342.
Ribbit, run as AUTH from its generated configuration, passes HW.DDT.1-3b there too. HW.DDT.4-5, HW.PEER.1 and
HW.LIG.1 need the second machine and have not run anywhere yet.

## 0. The RAM server -- on the neutral machine (your droplet)
Every participant connects UP to it; nothing connects into any other machine for control. On the droplet, from the
package directory: `STAGES=build tools/qualify.sh`, then `./ribbit_cpp/ram-server --listen 0.0.0.0:<port>`, with that TCP
port open to the other machines. Everything below names it as `<RAM>` (`host:port`). It holds its memory only while it
runs: start it fresh before a run, so no earlier run's registrations are in it.

## 2. The whole suite over a LAN and over the Internet (L6) -- two machines
The systems (lispers.net, and Ribbit's front) run on one machine, SYS; the harness sends the LISP traffic from the
other, TEST. Both connect up to `<RAM>`: the harness writes each command as a tuple, the agent on SYS reads it, starts /
configures / restarts the systems there exactly as the harness does on one machine, and writes the answer back. Ribbit's
front uses `<RAM>` as its memory.

On SYS, the machine lispers.net is installed on (from the package directory, relative path):

    python3 tools/acceptance.py --agent --ram streamingfrog.com:8800 --local-ip <SYS address> --lispers-dir <lispers.net directory on SYS>

On TEST:

    python3 tools/acceptance.py --ram streamingfrog.com:8800 --local-ip <TEST address> --out acceptance-out

`--ram` is the RAM server (Ribbit's memory, and where the two machines meet). The rest defaults: lispers.net's own REST
API is started on 8080 (`--lispers-api-port`), Ribbit's front listens on UDP 14342 (`--ribbit-udp`).

TEST must reach SYS on UDP 4342 (lispers.net) and 14342 (Ribbit's front) -- that is the LISP traffic being measured;
both reach streamingfrog.com:8800 over TCP. The RAM server there must be this package's build (it runs the LISP
region's operations, including this version's site options, policies, encryption keys and (S,G)). With Ribbit's memory on the droplet, L4.11 (the RAM host lost mid-run) is not applicable
and is recorded so. Tried in the build container with a stand-in neutral host and two addresses: P4, L1.1, L1.11-L1.14,
L2.13, L2.15.allowed-rloc, L2.17.set-rloc-address, L3.2 (lispers.net crashed and restarted through the agent), L4.10,
L4.11 -- as on one machine. (That run found and fixed two faults: the RAM host's own Engines dialed 127.0.0.1 even when it
listened on a specific address; the front printed half an answer when a configuration call failed.)
And for performance: `tools/compare_lispers.py ... --record <RAM> --wire lan|inet --version lispers=<v> --version
ribbit=<banner>` from TEST, targets on SYS.

## 3. Coverage on hardware (P5)
As in `tools/coverage/README.md`, with the coverage builds and `COVERAGE_PROCESS_START` on the machine that runs the
systems (the agent's); `tools/coverage/surface_lispers.py` then reports the surface against the combined data.
