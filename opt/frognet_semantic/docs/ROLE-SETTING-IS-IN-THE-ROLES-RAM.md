# A Role's protocol, cipher and key number live in the Role's own RAM, not the ControlRam

Status: DONE IN THE SIMULATOR, 2026-11-28 02:50 PT (channel-setting v11, channel-frame v14), on branch
`setting-not-written-from-behind-20261126`. Every step of `simulation/roles_exercise_tier.py` passes, with a check that
the ControlRam holds nothing of the Role. NOT yet run on John's nodes. The C++ and Java frames (the Communicator's
planes) do not use this module's settings and were not changed. Read this before touching `core/channel_setting.py`.

## The ruling

John, 28 Nov 2026, after the dashboard showed every Role's protocol, cipher and key tag by reading the ControlRam:

- "A viewer should not be able to tell that a connection is encrypted or what the parameters are."
- "That data should not exist in control ram if it is not needed. Ciphers, key, and protocol are local to the role."
- "Make the change."

This replaces the 19 Nov placement ("It is a tuple. A control tuple and part of the system capability tuple"): the
tuples stay tuples, their place changes.

## What was (through channel-setting-is-a-tuple-v10-20261123d)

In the ControlRam (`databasehost_control.frognet`), service `channel`, readable by every machine of the network:

    (channel, setting, <Role>)             driver, number, protection, check, (step)
    (channel, setting, semantic)           the setting of traffic that belongs to no Role
    (channel, at, <ip>:<process>.<pid>)    what each end holds and runs, for every Role

One held read per process, on the ControlRam's `channel` service.

## What it becomes

    in each memory of the Role (a Role's memory is <app>Host.frognet or <team>Host.<app>; core/app_hosts.py):
        (channel, setting, <Role>)             the setting of THAT memory's traffic
        (channel, at, <ip>:<process>.<pid>)    what each end that carries that memory's traffic holds and runs
    in the ControlRam, unchanged:
        (channel, setting, semantic)           traffic of no Role (the SystemRam's and the ControlRam's own)
        (channel, at, ...)                     each end's report for `semantic` only: no Role is named in it

- The owner's tool (`channel_admin.py`) writes a Role's setting into every memory of that Role its node's hosts file
  names, and reads the ends' reports from those memories.
- An end holds its read on each Role memory its hosts file names (one held read per memory), and on the ControlRam for
  `semantic`. A memory that leaves the hosts file ends its hold; a new one starts one. Nothing polls.
- A machine that does not carry a Role reads nothing about it, and the ControlRam says nothing of any Role's protocol,
  whether it is sealed, its algorithm or its number.

## The catch, and the answer (checked in core/channel_frame.py)

An end reads a Role's setting through the connection that the setting governs.

- The number: the first frame of a sequence, in each direction, states the sender's number in the clear, and the
  receiver computes the sender's key from the Role's secret, the sender's FNID, the Role and THAT number
  (`_rekey_in(side, number, ...)`), not from the tuple. So an end that holds no setting yet sends unsealed (number 0),
  is read, and reads the reply at the number the reply states.
- The algorithm: today the receiver takes it from the tuple (`wanted(self._setting(role))[1]`). An end that holds no
  setting for the Role yet does not know it. There are two (aes-256-gcm, chacha20-poly1305): the first sealed frame
  is opened with each registered algorithm; the one that opens is the sender's. No change on the wire.

## Steps (tick each as it is committed)

- [x] 1. channel_setting: settings keyed by memory; read from, held on and written to the Role's memories; reports
        written there; `semantic` stays in the ControlRam.
- [x] 2. channel_frame: a sealed frame for a Role whose algorithm this end does not hold yet is tried with each
        registered algorithm.
- [x] 3. channel_admin, channel_chat, channel_test: read and write through channel_setting (no ControlRam for a Role).
- [x] 4. the oracles (test_setting_not_written_from_behind_oracle, test_report_ends_with_its_process_oracle) and the
        simulator's roles exercise (`simulation/roles_exercise_tier.py`, every step), then `simulation/run_all.py`.
- [x] 5. a check that after a full exercise the ControlRam holds no (channel, setting, <Role>) and no Role in any
        (channel, at, ...).
- [x] 6. docs: AI_READ_FIRST.md, the protocol-layer design documents, frognet_monitor's reader (channel.py) if it reads
        the ControlRam.
- [ ] 7. node deliverable (only after 4 and 5 pass).

## Log

### 2026-11-28 01:00 to 01:50 PT

Written (in `docs/wip/role-setting-in-the-roles-ram-20261128.patch`, against 47bf46f; `git apply` it to go on):

- `channel_setting.py` v11: `memories()` (the Role memories this node's hosts file names), a held read on each
  (`_hold_the_memories`, `_run_memory`), the Role's setting taken as the newest cell among its memories, `write()` of
  a Role into every memory of the Role, `read_setting()`, `reports()`, each end's report for a Role written into the
  Role's memories and only its no-Role part into the ControlRam, the cleanup of an earlier build's Role cells in the
  ControlRam. `channel_admin.py` and `channel_test.py` read and write through it.

Run: `simulation/roles_exercise_tier.py --step admin` (the outputs are in `docs/wip/`).

1. First run found two faults of mine, fixed in the patch: a memory's name must be used as the hosts file writes it
   (the proxy looks a Host up as written; lower case got 400), and a memory's hold that was not taken must be woken
   by the next use of the channel, as the ControlRam's is.
2. With those fixed: the first setting (a protocol) reached all six ends in 2 s. Every switch of ENCRYPTION after it
   left ends behind for more than 60 s. Cause: an end learns the new number FROM the setting; its held read on the
   setting was answered under the Role's own channel, sealed with the key that number makes; on SWITCH ENCRYPTION a
   receiver takes the number from its own setting (`channel_frame.py`, "read before this end's setting changed; what
   follows goes back for a retry until it has"), which is the very thing in the frames it cannot open. Before, the
   setting came by another road (the ControlRam), so this could not happen.
3. Tried: carry the `channel` tuples themselves as traffic of no Role (`is_the_channels_own` in the patch; the
   proxy's `ctx["ram"] = ""` for them). Then the daemon refuses them once the Role is sealed:
   [A_SEALED_ROLE_IS_NOT_TAKEN_UNSEALED_V1] (daemon/engine/session.py) refuses any request for a sealed Role's memory
   that arrives outside the frame, and it decides from the destination alone, in three of its five places before the
   request is decoded.

### 2026-11-28 01:30 to 02:50 PT: built John's way

John, 01:27: "I thought you said the index for the key was passed in the clear. As long as they have the original
secret or the most recent secret, and they know what generation the most recent secret is, they should be able to
generate the current key very quickly." 01:31: "all we have to do is advance the existing secret until it has a key
that matches the current one, and we're done. If we go too many generations (100?), it fails and must be reset by
hand." 01:33: "Reset means using the qr code again."

What was built, each found by a failing simulator step and named in the code:

| Marker | What | Found by |
|---|---|---|
| A_SWITCH_STATES_THE_SENDERS_NUMBER_V1 | the sender states its number in the clear at every switch of encryption (before: first frame of a sequence only) | the bootstrap loop of 01:50 |
| THE_SECRET_IS_ADVANCED_UNTIL_ITS_KEY_OPENS_V1 | the receiver advances its secret in memory, both algorithms, until a frame opens; 100 steps, then "take it again from the FrogNet QR code". The step is not on the wire; no file is written from a frame | John's ruling; frame oracle: 3, 100 and 101 steps |
| A_REPORT_NAMES_ITS_ROLE_V1 | a Role's report is named `<Role>~<end>`: the memories of one node are one store and three Roles' reports were one cell | step memories: hold 0 of 6 |
| A_HOLD_NAMES_ITS_ROLE_V1 | the hold is on the Role's one tuple by name: two holds of one process with one pattern on one store are one hold | step admin: one end never woke |
| THE_CONTROLRAM_AND_A_MEMORY_ON_ONE_NODE_ARE_ONE_STORE_V1 | the removal of an earlier build's cells from the ControlRam is skipped where that store is the Role's memory: it removed the setting just written | steps memories, pages: ends went back to unsealed |
| EVERY_END_OF_THE_NETWORK_IS_WAITED_FOR_V1 | the Admin's command waits for every end the ControlRam lists to report in the Role's memory | step admin: "every end holds it (3)" of six |
| A_REFUSAL_STATES_THE_NUMBER_V1, AN_END_THAT_HAS_JUST_STARTED_LEARNS_THE_NUMBER_V1 | an end that restarted holds no setting, sent plain and was refused for ever. The refusal says `[SEALED_AT_NUMBER n]`; the end seals from the secret it holds, learns step and algorithm from the first frame that opens, then reads the setting, which proves itself | step admin-restart |
| A_MISSING_CELL_CHANGES_NOTHING_V1 | the store removes a machine's tuples when it leaves (WRITER_GONE). The Admin had written units' setting from another node; that node restarted, the cell went, and every end UNSEALED the Role. Now a proved setting stays until its owner writes another, and the memory's own node writes the cell back | step admin-restart (when the store saw the writer leave) |

The frame oracle's checks that a receiver whose setting is behind "takes nothing until its setting arrives" were
the old rule and now assert the new one (it takes the frames at once); the retry (NOT OPENED, start again) is
exercised with a wrong secret that is then set again.

Runs, 02:25 to 02:50: frame oracle 33/33; setting oracle pass; report, secret-moves-forward,
setting-not-written-from-behind, homa-carrier oracles pass; roles exercise memories 21, pages 18, admin 18,
admin-restart 10, holds 9, fires 9, test (the hardware test, 92 checks, and its must-fail case), chat 16,
chat-again 25: all pass; wire_between_machines (chorus, topo_dual_wg_transit_bridge) 213/213.
run_all.py: door_semantic_tier and proxy/test_homa_ordering_oracle.py fail the same way on the branch before this
work (d826c68); the rest of what ran passed; it was stopped in wire_between_machines `--topology all` for time.

## Known limits

- On a node that hosts the ControlRam and a memory of a Role, they are one store (api.php does not keep memories
  apart): that Role's cells are in the table the ControlRam's are in.
- A node whose secret for a Role is more than 100 steps behind, or wrong, opens nothing for that Role and says so
  once per connection; its secret is taken again from the QR code.
- What an end learns from a refusal or from the wire is in memory only and is not reported as a setting it holds.

## Superseded: the two ways considered at 01:50 (kept for the record)



The setting has to reach a Role's ends by a road that the Role's own switch of encryption does not close. Two ways:

A. The Role's `channel` tuples are carried as traffic of no Role (as the Communicator's setting is read on its room
   connection and only its planes are sealed), and the daemon's refusal lets exactly those requests through: the
   read, the held read and the write of (channel, setting|at, ...). The refusal must then decide after the request is
   decoded on the three semantic paths (`_reexecute_semantic`, the template path at 1195, `_handle_req_diff`).
   Consequence: a Role's protocol, algorithm and number cross under the system's setting, not the Role's, and any
   machine that asks the Role's memory for them is answered (as any machine could read the ControlRam before).
B. The frame changes: after SWITCH ENCRYPTION the sender states its number again in the clear (as a sequence's first
   frame does), the receiver keys from that and tries each registered algorithm. The setting then travels sealed
   under the Role's own key. This goes against "It does not say what from or to", and the C++ and Java frames follow
   the Python one.

(B was built, without stating anything but the number, as John ruled.)

One thing neither way changes: on a node that hosts both the ControlRam and a memory of a Role, they are one store
(api.php does not keep memories apart), so that Role's cells are in the same table the ControlRam's are.
