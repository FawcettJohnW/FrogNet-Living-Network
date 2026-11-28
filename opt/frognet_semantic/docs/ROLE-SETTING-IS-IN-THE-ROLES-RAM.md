# A Role's protocol, cipher and key number live in the Role's own RAM, not the ControlRam

Status: IN PROGRESS (started 2026-11-28 01:00 PT, branch `setting-not-written-from-behind-20261126`). This file is the
plan and the log; read it before touching `core/channel_setting.py`.

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

- [ ] 1. channel_setting: settings keyed by memory; read from, held on and written to the Role's memories; reports
        written there; `semantic` stays in the ControlRam.
- [ ] 2. channel_frame: a sealed frame for a Role whose algorithm this end does not hold yet is tried with each
        registered algorithm.
- [ ] 3. channel_admin, channel_chat, channel_test: read and write through channel_setting (no ControlRam for a Role).
- [ ] 4. the oracles (test_setting_not_written_from_behind_oracle, test_report_ends_with_its_process_oracle) and the
        simulator's roles exercise (`simulation/roles_exercise_tier.py`, every step), then `simulation/run_all.py`.
- [ ] 5. a check that after a full exercise the ControlRam holds no (channel, setting, <Role>) and no Role in any
        (channel, at, ...).
- [ ] 6. docs: AI_READ_FIRST.md, the protocol-layer design documents, frognet_monitor's reader (channel.py) if it reads
        the ControlRam.
- [ ] 7. node deliverable (only after 4 and 5 pass).

## Log
