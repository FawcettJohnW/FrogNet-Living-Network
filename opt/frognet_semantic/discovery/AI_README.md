# AI_README — the walk, routing, election

> **Working model: fast, tireless, and not to be trusted.** An assistant reads
> this directory faster than you can and is wrong in ways that look right. Use it
> to find things, draft things, and check things. Do not use it as a source.
> Everything below is here because something in it has already been got wrong.

## What is here

114 Python files. Roughly a third is the engine and two thirds are oracles.

| | |
|---|---|
| `discovery.py` | the bounded walk, `MAX_DEPTH = 2`, the `:9009` liveness gate |
| `routes.py` | the route planner — pure; it plans, it does not touch the kernel |
| `live.py` | commit, and the sync-on-mutation flag |
| `hosts.py` `resolv.py` | `/etc/hosts` and resolver composition |
| `test_*_oracle.py` | 81 oracles. Each asserts an outcome and exits nonzero on failure |
| `sim/` | scenario checks — safeboot, host reset, service election |

Run them: `cd .. && PYTHONPATH=. python3 -m discovery selfcheck`

## Traps

- **The planner is pure and must stay pure.** `routes.py` contains no kernel
  calls; both `subprocess` matches in it are inside comments. Observation,
  planning and commitment are separate on purpose. Adding an `ip route` call to
  the planner breaks the property the oracles rely on.
- **`WINNER_HYSTERESIS = 0.50`.** A challenger must measure at or below *half*
  the incumbent's RTT. A comment in `routes.py` said 10% for a long time and was
  wrong. The constant lives in `discovery.py`.
- **`[ONE_STATE_V1]` removed the `.1`/`.2` carve-out.** A refusal or no-route
  marks a role octet like anything else. The carve-out that used to protect them
  was itself the bug — it required a separate retire set that took real nodes out
  of service until the proxy restarted. Do not reintroduce it.
- **An oracle without a control proves nothing.** If you add one, add the case
  that makes it fail.
