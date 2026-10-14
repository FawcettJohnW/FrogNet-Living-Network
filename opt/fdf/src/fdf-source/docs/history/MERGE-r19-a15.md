history: superseded by docs/OPERATING.md

# r19-a15 — devices stay named after runMerge

Build `fdf-2026-10-11-r19-a15` (on r19-a14). Android fdf-kt a15 / fdf-tun b11, Windows fdf-kt a14 / fdf-tun b11 unchanged.

| tag | where | what |
|---|---|---|
| `LEAVES_NAMED_BY_EVERY_WRITER_V1` | discovery/elect.py (`given_names`, `leaf_lines`), discovery/live.py | **Found on Seattle5 (John, 2026-10-11):** after a runMerge every device (the phone, the PC) was gone from /etc/hosts until the service restarted. Two writers own /etc/hosts on a region node: the daemon's election (each region event, with the members' facts: it names the leaves) and runMerge (it rebuilds the file from region_hosts' .1 lines and never named a leaf). runMerge now names every alive leaf and reaching participant from the same facts, through the same function the election uses. Its log says `LEAVES named: N (from the region's facts)`. |

Proof: netns-region-loop-test's new check — runMerge (`python3 -m discovery.live`, region mode) on node a, then a's /etc/hosts must still name the leaf: **FAIL on r19-a14's code** (`0 line`), **PASS on a15** (`LEAVES named: 1; 1 line`); region-loop RESULT PASS on the a15 binaries; election sim PASS (6 runs; one earlier run tripped its 200 ms timing check while the edited module recompiled).
