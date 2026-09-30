# AI_README — the Communicator application

> **Working model: fast, tireless, and not to be trusted.** An assistant reads
> this directory faster than you can and is wrong in ways that look right. Use it
> to find things, draft things, and check things. Do not use it as a source.
> Everything below is here because something in it has already been got wrong.

## What is here

115 Python files plus a React app: presence, chat, A/V calling, the media ladder.
This is an **application** written on FrogNet, not part of the fabric.

| | |
|---|---|
| `sotf_ladder.py` | the nine rungs and the step rules |
| `media_codex.py` | the A/V call codex — 48 kHz stereo |
| `fnphone_pa.py` | the FNWP voice phone — 16 kHz mono |
| `comms_control.py` | the five call-state tuples |

## Traps

- **Two audio formats, both correct.** fnphone is 16 kHz mono; the Communicator
  A/V call is 48 kHz stereo. A ladder rung names the tracks carried and the video
  size, never a sample rate or channel count. Do not write one into a rung.
- **Down is cheap and fast; up is expensive and slow.** Stepping down needs any
  one of four conditions. Stepping up needs three consecutive clean seconds *and*
  a per-rung hold-off that doubles from 15s to a 5-minute ceiling. Making them
  symmetric will oscillate.
- **The frame-rate floor is relative, not absolute.** An absolute floor leaves a
  dead band where a node runs at half rate forever, sheds nothing, and reads
  healthy in every counter.
- **Five call tuples, each written by the party with standing to know.** Host
  rows by host, user rows by name, call rows by id. Identity is the name — no
  per-launch id and no display marker in a key.
