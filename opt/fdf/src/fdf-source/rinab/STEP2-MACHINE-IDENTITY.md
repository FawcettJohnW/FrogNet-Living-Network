# FD&F build step 2: machine identity (`[MACHINE_IDENTITY_V1]`, src/machine_id.hpp)

**A member is a machine, named by a GUID, not by the name of a Linux network namespace.**

## The identity

| case | identity |
|---|---|
| `RINA_MACHINE_ID` set | that, exactly (a deployment can name its machines itself) |
| otherwise | `m:` + the machine's GUID (`/etc/fnid`, FrogNet's machine GUID, if present; else `/etc/machine-id`) + `/` + the inode of the process's network namespace |

**Why the inode is always appended:**

- In the one-machine suite, each namespace stands for a separate machine and must be one.
- On a real machine every process is in the initial namespace, whose inode is a kernel constant, so the identity is the
  same for every process and across boots.
- Comparing with pid 1's namespace (the first attempt) does not work: unprivileged programs cannot read it, and neither
  can this sandbox. Every test machine came out with the same identity, and tests 24–30 failed until the rule changed.

**Where it is used:**

- The library, the machine agent and the rlite-ctl client name the machine by it in every request. The region already
  took the caller's identity from the request, so the region did not change.
- The data plane's machine address (`rmt::my_node()`) is derived from it.

## Measured

- **rlite's 30 tests:** 30/30 (1–23, then 24–30 together).
- **Tests 24–30:** the agents ran under 6 distinct machine identities and forwarded 227 PDUs, none without a route.

## What it enables

- **Experiment 8 runs on separate physical machines unchanged.** A machine's identity now comes from the machine, not
  from where Linux put a process.
- **Identity stays separate from locator** (step 3): a machine keeps its identity when its addresses change.
