# Channel setting in a capability tuple, changed under load: build and simulator results

> **21 Nov 2026:** the driver this document calls "soft Homa" / `softhoma` is called **chorus** from this date (the word `chorus`, `core/chorus.py`). The text below is as written and keeps the old name. Why: `EXPLORATION-LOG.md`, entry of 21 Nov 07:48.

19 November 2026. Built and run on branch `homa-under-croakos-20261116`. First run on commit `2783ff28`; applied
unchanged to `46075258` and run again there before it was committed.

## The result

The channel's driver and key were changed ten times under a running workload on each of three links. A link's profile is applied to every machine's uplink, so a path crosses two of them. Every run
passed. No request failed, no answer was wrong, and no daemon or proxy was restarted.

| Link | Requests | Failed | Wrong | Processes restarted | Asking proxy learned of a change | Longest time with no answer, 2 s before a change | Same, 2 s after |
|---|---|---|---|---|---|---|---|
| Perfect link, run 1 | 27003 | 0 | 0 | none | 90 to 199 ms | at most 30 ms | at most 63 ms |
| Perfect link, run 2 | 27353 | 0 | 0 | none | 64 to 236 ms | at most 24 ms | at most 43 ms |
| Perfect link, on `46075258`, run 3 | 28376 | 0 | 0 | none | 68 to 221 ms | at most 30 ms | at most 766 ms |
| Perfect link, on `46075258`, keys loaded when named, run 4 | 28553 | 0 | 0 | none | 102 to 151 ms | at most 22 ms | at most 50 ms |
| Perfect link, on `46075258`, keys loaded when named, run 5 | 27716 | 0 | 0 | none | 77 to 202 ms | at most 32 ms | at most 49 ms |
| Perfect link, on `46075258`, keys loaded when named, run 6 | 27675 | 0 | 0 | none | 98 to 163 ms | at most 26 ms | at most 57 ms |
| `wan-100ms` profile on every uplink (a request takes about 220 ms) | 3350 | 0 | 0 | none | 342 to 576 ms | at most 207 ms | at most 455 ms |
| 4,800 baud on every uplink (a request takes about 1.2 s) | 1063 | 0 | 0 | none | 2500 to 5254 ms | at most 648 ms | at most 644 ms |

The pause a change causes is the difference between the last two columns. On the perfect link the longest silence
after a change was 43 to 63 ms in five of six runs, against 22 to 32 ms before. In run 3 it was 766 ms, once, at the
first message sent under a key; nothing failed. After that run the keys a setting names are loaded when the setting
is read, not at the first message, and in the three runs since the longest silence at that change was 22 to 29 ms.
One occurrence does not prove that was the cause. On the `wan-100ms` link the longest silence was 455 ms against
207 ms, at the change from soft Homa back to TCP. On the 4,800 baud link the two are the same to within 10 ms.

The stock tier still passes with the changes in, on `46075258`: `wire_between_machines_tier.py` on tcp (37 checks) and on softhoma
(40 checks).

## What was run

The machines are `wire_between_machines_tier`'s, unchanged: Seattle5, Seattle3B and New-York-1, each in its own
network namespace with its own database, api.php, real daemon (`daemon_main.py`) and real proxy (`proxy_main`).
Programs on New-York-1 read and wrote through the wire to Seattle5 for 8 seconds per change (40 seconds on the
4,800 baud link): six readers and three writers at once (two and one on the 4,800 baud link). Half way through, the
owner wrote the channel's tuple from the ControlRam machine.

The ten changes, in order, were these.

1. The driver, tcp to softhoma.
2. Key A accepted as well as no key (preparing).
3. Requests go under key A.
4. No key is no longer accepted.
5. Key B accepted as well as key A (preparing).
6. Requests go under key B.
7. Key A is no longer accepted.
8. No key accepted as well as key B (preparing).
9. The driver, softhoma under key B back to tcp.
10. Key B is no longer accepted.

After the tenth change, a read through the wire of what New-York-1's writers wrote equals Seattle5's own api.php.

Run it with `python3 simulation/channel_swap_tier.py [--links wan-100ms | radio-4800 --light --seconds 40]` from
`opt/frognet_semantic`, using the simulator's Python.

## What was built

1. `core/channel_setting.py` (new). The channel's setting is the tuple (channel, setting, semantic) in the
   ControlRam. Its JSON is this module's own: `driver`, `key` (the key's name) and `accept` (the names a message may
   arrive under). Each daemon and proxy holds a read on it. Nothing polls and nothing runs on a timer.
2. `core/homa_transport.py`. When the tuple names a driver, that is the word for a new session. A process with no
   such tuple behaves as before.
3. `proxy/transport_semantic.py`. When the tuple changes the driver, new requests open a new session at once. The
   old session finishes what is outstanding on it and ends after the new one is up. Nothing in flight is failed.
4. `core/homa_frames.py`. On the semantic wire's soft Homa connections, a message is sealed with AES-256-GCM under
   the key the tuple names, before it is cut into segments. The key's name travels in the clear; the key is a
   32-byte file on the machine. A reply goes under the key its request came under.
5. Each end writes its own tuple, (channel, at, its address and process), saying which setting it runs. The owner
   holds a read on those and goes to the next change only when every end says it runs the current one.
6. `simulation/channel_swap_tier.py` (new), and a timed workload added to the tier's client.

## Five things found in the existing code

1. **In `wire_between_machines_tier`, no held read ever returns on a fire.** `held_fire.php` looks for a holder's
   socket in the constant `/run/frognet/held`. The tier gives each machine its own `FROGNET_HELD_DIR`. Every fire was
   answered 410 "no holder here" (25 of 25 in one run). The new tier tells each machine's copy of the file its own
   directory. The stock tier needs the same.
2. **The tuple that sets the channel is read over the channel it sets.** An end that missed one change had its
   requests refused by the ControlRam's daemon, and its attempt to read the tuple was refused the same way, more than
   a hundred times in a row. The order of changes above is the answer used here: accept the new thing everywhere
   first, wait until every end says it runs that, then start sending, then take the old thing away. With that order
   there were no refusals.
3. **A machine that moves from one driver to another is treated as having left.** Each carrier's listener counts
   its own sessions. When Seattle5's last TCP session to the ControlRam ended, its tuples were removed, though it had
   a soft Homa session to the same daemon. The patch asks the other carrier's listener first, and brings the new
   session up before the old one ends.
4. **A hold retried at the merge can be refused by the machine's own proxy.** The first hold fails because the role
   is not named yet. The retry at the hosts event reached the proxy before it had read the new hosts file and got
   HTTP 400 "Missing/invalid Host header". The hold then waited for a network event that did not come. The liveness
   hold on (homa, can) shows the same 400 in the logs. The new module holds again at the next use of the channel.
5. **Writers creating new rows at the same moment can fail in api.php** with a foreign-key error on `SensorData`
   (1 request in 3,036). The tier writes each writer's first row before the writers start together.

## Decisions that are John's

- The tuple carries the key's name, never the key.
- AES-256-GCM for the test. (It was first done with the Python module `cryptography`; it is now the machine's
  libcrypto, see "Added the same day".)
- The `accept` list and the accept, send, retire order.
- Each end's (channel, at, ...) tuple and the owner waiting on them.
- Only the soft Homa driver is keyed. The TCP driver carries no key in this build.
- The node's own word (`/etc/frognet/semantic_transport`) still stands when there is no tuple. The machines were
  started with `auto,databasehost_control.frognet softhoma` so that each was told of soft Homa.

## Not done

- A check in the tier that a request under a retired or unheld key is refused. Refusals were seen in earlier,
  failing runs ("a request arrived under the key 'A' and the channel accepts none"), but no check asserts it.
- Every pair of machines under load at once. The workload ran from New-York-1 to Seattle5.
- Real nodes, and kernel Homa.

## Added the same day, 11:45: four pieces from a second build of the same handoff

A second thread built the same handoff separately, on `46075258`, with a different design (one write per change, the
proxy closing the old connection and failing what was in flight). John chose to keep this build and add to it what
the other had and this did not. That build's own code is not on the branch; its patch is
`channel-setting-in-tuple-20261119-0845.patch`.

| Piece | What it is here | Where |
|---|---|---|
| The machine's libcrypto, not the Python module `cryptography` | AES-256-GCM through OpenSSL's libcrypto by ctypes. The nodes have libcrypto and do not have the module. The sealed bytes are the same for the same key, nonce and associated data (compared). `setup.sh` no longer installs the module. | `core/channel_protection.py` |
| A protection is registered by name | Two functions and a name; the tuple's `protection` names the one used (absent: aes-256-gcm). | `core/channel_protection.register_protection`, `channel_setting._protection` |
| A driver registers its word | `DRIVERS` and `register_driver(word, session kind)`; the file's word and the tuple's `driver` are checked against it. `VALID` is no longer the gate. | `core/homa_transport.py`, `channel_setting.driver` |
| One RAM's connection has a setting of its own | The tuple (channel, setting, <RAM's role name>) is the setting of every node's connection to the machine that RAM is on, in place of the channel's. | `core/channel_setting.py` (`setting_for`, `here`, `write(value, ram=...)`), `core/homa_frames.py` |

The other build's fifth piece, a rule that a daemon takes no more frames from a node's old session once its new one
is there, was NOT brought over. It cured a fault that build made for itself: its proxy failed what was in flight and
the caller's next write went out on the new connection while the failed one was still on a 4,800 baud link, arrived
second, and was executed as a difference against the wrong reference (the far machine held a number nobody sent).
This build fails nothing, so a writer's next write waits for the answer to its last. To look for the fault here
anyway, two checks were added to the tier and run:

- Every writer's row on the far machine, read from that machine's own api.php after each change, is the last write
  that writer was answered 200 for: its number and its text (`[WHAT_IS_HELD_IS_THE_LAST_WRITE_V1]`). Before this the
  tier compared a read through the wire with the far machine's own read, which agree whatever is held.
- `--shared`: the programs come in pairs sending the SAME requests (two readers of one read, two writers of one
  row), so requests of one instance are outstanding together when the driver changes; run at 4,800 baud, four
  changes of driver.

Neither found anything wrong: 0 failed and 0 wrong in every run below. That is four changes of driver under the
shared workload, not a proof; requests of one instance sent by two programs at once do travel on two sessions for
a moment at a change of driver, and nothing orders them across the two.

### Results with the four pieces in

| Run | Changes | Requests | Failed | Wrong | Restarted | Asking proxy learned of a change | Longest time with no answer, 2 s before | Same, 2 s after |
|---|---|---|---|---|---|---|---|---|
| Perfect link | 14 | 41,644 | 0 | 0 | none | 87 to 188 ms | at most 31 ms | at most 55 ms |
| 4,800 baud on every uplink (`--links radio-4800 --light --seconds 40`) | 14 | 1,527 | 0 | 0 | none | 2,097 to 4,716 ms | at most 676 ms | at most 565 ms |
| 4,800 baud, shared requests (`--links radio-4800 --shared --seconds 60`) | 4 | 934 | 0 | 0 | none | 2,506 to 4,660 ms | at most 497 ms | at most 506 ms |

The 14 changes are the ten above and four more, for one RAM's connection (databasehost.frognet, which is on
Seattle5): soft Homa with key A accepted; requests to that machine under key A; back to tcp with key A still
accepted; the RAM's tuple emptied. In the second of them the tier checks that New-York-1's requests to Seattle5 went
under key A and its requests to Seattle3B stayed on tcp with no key.

`wire_between_machines_tier.py` after the four pieces: tcp 37 checks, softhoma 40 checks, all pass.
`core/test_channel_setting_oracle.py` (new, 13 checks in one process) passes.

Still not done: real nodes; kernel Homa; softhoma told to a node by the tuple alone (a node must have soft Homa in
its own word first); more than one protection on a connection at once.

## The numbers, change by change

### Perfect link, run 1

| Change | Requests | Failed | Wrong | Asking proxy learned (ms) | Every end learned (ms) | Longest gap, 2 s before (ms) | Longest gap, 2 s after (ms) | Median before (ms) | Median after (ms) |
|---|---|---|---|---|---|---|---|---|---|
| the driver, tcp to softhoma | 2618 | 0 | 0 | 145.8 | 145.8 | 17.3 | 20.9 | 25.1 | 28.3 |
| (preparing) key A accepted as well as no key | 2697 | 0 | 0 | 156.4 | 156.4 | 30.1 | 25.4 | 26.0 | 25.8 |
| a key where there was none: requests now go under key A | 2722 | 0 | 0 | 122.8 | 248.5 | 21.4 | 59.2 | 25.1 | 26.3 |
| no key is no longer accepted | 2662 | 0 | 0 | 198.6 | 198.6 | 17.7 | 31.6 | 25.6 | 26.3 |
| (preparing) key B accepted as well as key A | 2716 | 0 | 0 | 102.5 | 110.7 | 17.7 | 63.3 | 25.6 | 24.5 |
| the key changed: requests now go under key B | 2631 | 0 | 0 | 148.4 | 148.4 | 21.7 | 23.4 | 26.1 | 26.2 |
| key A is no longer accepted | 2705 | 0 | 0 | 116.4 | 118.5 | 15.3 | 22.2 | 24.0 | 27.9 |
| (preparing) no key accepted as well as key B | 2625 | 0 | 0 | 90.3 | 101.0 | 23.2 | 18.7 | 26.6 | 26.7 |
| the driver, softhoma under key B back to tcp | 2642 | 0 | 0 | 100.5 | 121.6 | 19.2 | 33.4 | 27.5 | 24.6 |
| key B is no longer accepted | 2985 | 0 | 0 | 90.6 | 100.4 | 13.9 | 29.2 | 22.9 | 23.3 |

### Perfect link, run 2

| Change | Requests | Failed | Wrong | Asking proxy learned (ms) | Every end learned (ms) | Longest gap, 2 s before (ms) | Longest gap, 2 s after (ms) | Median before (ms) | Median after (ms) |
|---|---|---|---|---|---|---|---|---|---|
| the driver, tcp to softhoma | 2655 | 0 | 0 | 154.1 | 154.1 | 14.9 | 25.3 | 25.9 | 26.4 |
| (preparing) key A accepted as well as no key | 2655 | 0 | 0 | 141.5 | 141.5 | 20.3 | 43.0 | 26.1 | 25.5 |
| a key where there was none: requests now go under key A | 2642 | 0 | 0 | 235.8 | 235.8 | 16.9 | 42.4 | 24.6 | 28.3 |
| no key is no longer accepted | 2804 | 0 | 0 | 127.1 | 127.1 | 24.2 | 32.6 | 24.1 | 25.7 |
| (preparing) key B accepted as well as key A | 2759 | 0 | 0 | 107.3 | 107.3 | 19.4 | 21.1 | 24.3 | 25.9 |
| the key changed: requests now go under key B | 2739 | 0 | 0 | 110.0 | 110.0 | 16.8 | 23.7 | 24.7 | 25.9 |
| key A is no longer accepted | 2719 | 0 | 0 | 92.9 | 92.9 | 16.5 | 30.3 | 26.3 | 24.7 |
| (preparing) no key accepted as well as key B | 2759 | 0 | 0 | 64.3 | 121.7 | 15.3 | 26.4 | 24.0 | 26.4 |
| the driver, softhoma under key B back to tcp | 2704 | 0 | 0 | 108.4 | 114.8 | 24.5 | 29.4 | 26.3 | 24.3 |
| key B is no longer accepted | 2917 | 0 | 0 | 94.5 | 100.2 | 14.7 | 37.3 | 24.1 | 23.2 |

### `wan-100ms` profile on every uplink

| Change | Requests | Failed | Wrong | Asking proxy learned (ms) | Every end learned (ms) | Longest gap, 2 s before (ms) | Longest gap, 2 s after (ms) | Median before (ms) | Median after (ms) |
|---|---|---|---|---|---|---|---|---|---|
| the driver, tcp to softhoma | 335 | 0 | 0 | 341.8 | 389.7 | 155.9 | 134.0 | 220.8 | 218.2 |
| (preparing) key A accepted as well as no key | 339 | 0 | 0 | 351.8 | 564.3 | 149.5 | 82.6 | 217.1 | 218.4 |
| a key where there was none: requests now go under key A | 339 | 0 | 0 | 352.7 | 642.4 | 164.9 | 191.6 | 217.5 | 220.7 |
| no key is no longer accepted | 335 | 0 | 0 | 380.3 | 391.7 | 141.5 | 122.7 | 219.7 | 218.9 |
| (preparing) key B accepted as well as key A | 335 | 0 | 0 | 350.9 | 352.2 | 143.2 | 109.5 | 221.6 | 220.2 |
| the key changed: requests now go under key B | 337 | 0 | 0 | 359.6 | 359.6 | 144.9 | 134.7 | 217.9 | 217.2 |
| key A is no longer accepted | 336 | 0 | 0 | 346.9 | 356.4 | 177.2 | 140.9 | 221.4 | 218.6 |
| (preparing) no key accepted as well as key B | 336 | 0 | 0 | 361.4 | 361.4 | 183.9 | 164.3 | 219.5 | 220.3 |
| the driver, softhoma under key B back to tcp | 321 | 0 | 0 | 382.9 | 393.4 | 207.2 | 455.1 | 219.5 | 219.8 |
| key B is no longer accepted | 337 | 0 | 0 | 576.2 | 586.7 | 123.6 | 97.5 | 220.9 | 218.3 |

### 4,800 baud on every uplink (`radio-4800`, added to slowlink.py)

| Change | Requests | Failed | Wrong | Asking proxy learned (ms) | Every end learned (ms) | Longest gap, 2 s before (ms) | Longest gap, 2 s after (ms) | Median before (ms) | Median after (ms) |
|---|---|---|---|---|---|---|---|---|---|
| the driver, tcp to softhoma | 90 | 0 | 0 | 4146.0 | 9538.8 | 647.6 | 644.1 | 977.9 | 1152.5 |
| (preparing) key A accepted as well as no key | 109 | 0 | 0 | 3212.2 | 3640.0 | 535.4 | 534.8 | 1105.0 | 1104.7 |
| a key where there was none: requests now go under key A | 106 | 0 | 0 | 5253.7 | 5253.7 | 527.8 | 533.4 | 1103.9 | 1205.3 |
| no key is no longer accepted | 100 | 0 | 0 | 2737.2 | 3472.1 | 533.2 | 530.6 | 1206.4 | 1205.5 |
| (preparing) key B accepted as well as key A | 101 | 0 | 0 | 3445.4 | 3445.4 | 532.2 | 522.0 | 1205.7 | 1205.8 |
| the key changed: requests now go under key B | 101 | 0 | 0 | 2804.2 | 2825.2 | 528.5 | 532.5 | 1205.7 | 1206.6 |
| key A is no longer accepted | 101 | 0 | 0 | 2823.4 | 2823.4 | 533.3 | 530.4 | 1205.1 | 1206.1 |
| (preparing) no key accepted as well as key B | 101 | 0 | 0 | 2834.5 | 2834.5 | 536.1 | 523.0 | 1205.5 | 1205.3 |
| the driver, softhoma under key B back to tcp | 114 | 0 | 0 | 2833.8 | 2937.6 | 531.5 | 532.7 | 1205.5 | 864.7 |
| key B is no longer accepted | 140 | 0 | 0 | 2499.7 | 3000.6 | 550.9 | 559.8 | 864.5 | 865.4 |

"Learned" is measured from the owner's write returning to the process's own log line for its held read. "Longest
gap" is the longest time with no answer arriving. "Median" is one request's time, for requests answered before the
change and for requests sent after it.
