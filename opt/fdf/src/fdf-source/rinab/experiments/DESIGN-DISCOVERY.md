# DESIGN-DISCOVERY: FD&F discovery from the capability matrix (experiment 3, done properly)

**John (2026-10-07):**

1. Each machine connects to shared RAM.
2. The machine writes its capability tuple and any other node-specific information into its tuple space.
3. It runs tuple queries on the capability matrix to discover other machines in the network.
4. It sets up the dedicated socket.

## What exists, and what is wrong with the last run

- **Step 1 exists.** The agent's session to the region.
- **Step 2 exists.** The agent states its node tuple: devices with their state and MAC, every IPv4 address with its
  prefix (`rloc.<dev>=a.b.c.d/len`), and capability (`cap.mr`, `cap.rtr`, `nat`). It is rewritten on every kernel
  change.
- **Step 3 is missing.** The agent reads every node tuple on each region signal, but builds its graph only from
  ENROLLMENT records (`adj|`, made by `rlite-ctl ipcp-enroll` / `ipcp-lower-flow-alloc`). With no rlite-ctl command,
  there is no graph.
- **What the last rig did.** It issued those commands itself, from the topology file: the topology was handed in,
  not discovered.

## The design (`[MATRIX_DISCOVERY_V1]`)

- **The query.** On each region signal the agent reads the capability matrix: every machine's node tuple, as it
  already does.
- **Neighbours.** Two machines are neighbours if each holds an address in the same subnet: they share a segment, LAN
  or point-to-point alike, which is FrogNet's own rule (its `Topology.link` requires one shared subnet per segment).
  Read from the tuples, never configured.
- **Routes.** A breadth-first search over the neighbour graph from this machine gives, for every other machine, the
  first neighbour on a shortest path and that neighbour's address on the shared subnet. Machine-level routes are
  published lock-free like the DIF routes, under the area's own key.
- **The dedicated socket.** PDUs to a machine go to its next neighbour's address on the shared subnet, over the
  agent's RMT UDP socket. That is the member-to-member path, never through the region.
- **The check.** A machine-level echo (a PDU type the agent answers): `rina-reach <machine>` sends one and waits for
  the answer. It exercises exactly the discovered routes and sockets.

## The rig, corrected

- **Built by the rig:** the wires (one bridge per segment), and each node's interface addresses as FrogNet's topology
  assigns them.
- **Given to each machine:** the way to the shared RAM (`RINA_RAM`). That is John's step 1. In the sandbox the region
  host is reached through the rig's own network namespace, recorded as the stand-in for "the RAM is reachable".
- **Not given:** no rlite-ctl command, no neighbour, no enrollment, no adjacency.
- **The oracle:** every ordered pair, `rina-reach`.

## Order of work (small steps, each with its evidence)

1. The exporter includes each interface's address.
2. The agent derives neighbours and routes from the matrix, and says what it found.
3. The echo, and `rina-reach`.
4. Rig v2: no rlite-ctl. Topology 0, then all 20.
5. Messages per change, beside FrogNet's crawl.

Programs' DIFs over this machine graph come after.
