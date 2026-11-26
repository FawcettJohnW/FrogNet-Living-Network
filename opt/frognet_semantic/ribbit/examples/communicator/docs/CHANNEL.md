# A call under the protocol layer's frame

The design and the code are described in full in `docs/CHANNEL-DESIGN-AND-IMPLEMENTATION.md`. This file is the short record.

John, 2026-11-24: "the communicator can use the keys and Cypher and encode the video and audio across the wire. They
would be able to switch protocols and etc. Without interrupting video." "Make it a library kind of thing so it can
apply to anything." "also add the code that implements the security model."

This is what was built for that, how to run it, and what was measured. Tags in brackets are in the code.

## The words

- **Plane.** One of a participant's two permanent TCP connections to the media server: one for audio, one for video.
  They are opened when the participant joins a call and stay open until it leaves.
- **Role.** The name a channel's rules are kept under. A call's planes are for one Role, `mediahost` unless
  `--channel` names another.
- **Setting.** What a Role's owner has set for it: the protocol its data is packed by (`tcp`, `chorus`, `openqasm`),
  a random number (0 means not sealed) and the algorithm it is sealed with (`aes-256-gcm`, `chacha20-poly1305`).
- **Secret.** A file placed by hand on each machine that may carry the Role: `<secrets>/<Role>`, or
  `<secrets>/default` for a Role with none of its own. It never crosses a connection.
- **Key.** Computed by each sender, never stored and never sent: the first 32 bytes of HMAC-SHA384 of the secret over
  the sender's ID, the Role and the setting's number. A new number is a new key.
- **Frame.** The protocol layer's envelope around each message: its length, its packet ID, and the data, sealed or
  not, beginning with the protocol's word. It is `core/channel_frame.py`'s, the one the nodes' daemons speak.
- **Switch.** A change of setting. The sender puts one call into its stream (SWITCH PROTOCOL or SWITCH ENCRYPTION),
  the old way, and everything after it is the new way. Nothing is closed, reopened or negotiated.

## What there is

| | |
|---|---|
| `ribbit/cpp/include/channel_frame.hpp`, `src/channel_frame.cpp` | [THE_FRAME_IS_A_LIBRARY_V1] the frame as a library with no socket in it: a `Codec` is one end of one connection. `pack()` gives the bytes to put on the connection, `feed()` takes the bytes that arrived, `undo()` takes back a unit that did not go. The same bytes as `core/channel_frame.py` (build v13), so the other end may be a node's `Link`. Needs only libcrypto. Vendored here in `third_party/ribbit/cpp`. |
| `src/channel_frame_c.cpp`, `python/channel_codec.py` | the same library for any language that calls C (`libchannel_frame.so`), and its Python binding |
| `src/fnav/plane.{hpp,cpp}`, `python/frogcomms/fnav.py` | [THE_CALL_IS_UNDER_THE_FRAME_V1] the frame under every body a plane sends, in both Communicators. [ONE_WRITER_PER_PLANE_V1] each plane has one writer thread; the lock around the socket is gone |
| `src/comms/channel.{hpp,cpp}`, `python/frogcomms/channel.py` | the process's channel: the read it holds on the Role's setting, the proof of each setting, every plane told |
| `tools/comms_channel.cpp` (`comms-channel`) | the owner's command, with `core/channel_admin.py`'s words |
| `tests/test_channel.cpp`, `tests/py/test_call_under_the_frame.py`, `ribbit/cpp/tests/test_channel_frame_oracle.py` | the proofs below |

## The security model, and where each part is

1. **Sealed with a key nobody sends.** Every body on a plane is sealed under the sender's key for the Role (AEAD,
   the packet ID bound in). `Codec::seal`, `key_for`.
2. **Every sender has its own key.** The sender's ID is in the key. On a FrogNet node it is the node's FNID
   (`/etc/fnid`); a machine that is no node sends as `comms-<name>`. `channel_sender_id`.
3. **A setting proves itself.** [A_SETTING_PROVES_ITSELF_V1] The setting carries a check, HMAC-SHA384 under the
   Role's secret. A process that holds the secret ignores a setting whose check does not verify and keeps the one it
   has, so nobody without the secret can turn a Role's sealing off. `take_setting`, `Channel::read`.
4. **A Role's own secret moves forward.** [A_ROLES_OWN_SECRET_MOVES_FORWARD_V1] With a secret file of its own, each
   new key raises the setting's step; each machine replaces its file with a one-way function of it and erases the old
   one. `secret_at`, `keep_secret` (not on Windows: there the step is worked out in memory and the file is not moved).
5. **No key is tried but the current one.** A frame that does not open goes back for a retry; the sender sends it
   again under its current key. `Codec::frame`, NOT OPENED and AGAIN.
6. **A sealed Role takes nothing unsealed.** [A_SEALED_ROLE_TAKES_NO_UNSEALED_MESSAGE_V1] When the setting an end
   holds is sealed, an unsealed message is not taken, whatever the sender says of itself. The media server does not
   let a participant that does not speak the frame into a call it holds as sealed. This rule is this library's:
   `core/channel_frame.py` v13 takes unsealed messages from a sender that names its sequence unsealed (oracle check
   11 shows both).
7. **The setting is read before any plane is opened.** A program run with `--channel` reads the Role's setting from
   the memory first, so nothing of a sealed Role is ever sent in the clear. `channel_for_program`.
8. **Every end reports what it runs.** `(channel, at, <Role>:<end>)`: the setting it is running, or the one it
   ignored and why. No key and no secret is in it. `comms-channel set` returns when every end's report shows the
   setting, and names any end that ignored it.

What it is not: there is no key exchange, and a participant's secret file is as safe as the machine it is on.

## Running it

Place the same secret on every machine that may be in the call (any 32 bytes or more):

    mkdir -p /etc/frognet/channel_secrets && head -c 48 /dev/urandom > /etc/frognet/channel_secrets/default

(`FROGNET_CHANNEL_SECRETS=DIR` names another directory.) Then, with the memory and the media server as before:

    comms-media --listen 0.0.0.0:8994 --ram HOST:8800                  # speaks the frame to every far end that does
    comms-app  --ram HOST:8800 --media HOST:8994 --name John --channel mediahost
    comms-feed --ram HOST:8800 --media HOST:8994 --name "Chapel camera" --video FILE --channel mediahost
    python3 python/comms_app.py --ram HOST:8800 --media HOST:8994 --name Donna --channel mediahost

Without `--channel` a participant's planes are what they were, and the media server reads from a connection's first
unit which kind it is. The owner, on a machine that holds the secret, while the call runs:

    comms-channel --ram HOST:8800 set mediahost --seal
    comms-channel --ram HOST:8800 set mediahost --new-key
    comms-channel --ram HOST:8800 set mediahost --algorithm chacha20-poly1305
    comms-channel --ram HOST:8800 set mediahost --protocol chorus
    comms-channel --ram HOST:8800 set mediahost --unseal
    comms-channel --ram HOST:8800 show mediahost                        # every end and what it is running

Each `set` prints the setting and, for example, `3 end(s) there: 3 running it (9 ms after the write)`.

## What was measured (one 2-core machine, 2026-11-24)

**A real call** (`tests/py/test_call_under_the_frame.py build`): the C++ `comms-feed` sends the stock 720p video and
a 440 Hz tone through the media server to a Python viewer, all three under the frame, the Role sealed from the start.

| | video frames/s | audio blocks/s | longest time with no new frame | with no new audio block |
|---|---|---|---|---|
| 6 s, nothing switched | 23.9 | 50.0 | 51 to 61 ms | 30 to 31 ms |
| 21 s, nine switches (new key, chorus, chacha20, openqasm, new key, not sealed, sealed, tcp, new key) | 23.9 | 50.0 to 50.1 | 61 ms | 32 to 42 ms |

Frames are 42 ms apart and blocks 20 ms. All nine settings reached the viewer, the media server and the feed. The
picture stayed 1280x720, the tone 440.0 Hz, with 0 decode errors. A viewer whose machine holds another secret joined
the sealed call for 4 s and got 0 frames and 0 blocks. The other direction, a Python feed watched by the C++ viewer,
both under the frame and sealed: 119 to 120 frames in 5 s.

**Planes** (`build/test_channel`): two planes under the frame with 18 to 26 switches under 3,000 bodies each way, one
end shedding and aborting about a third of its frames because the other reads in stalls: exactly the bodies whose send
said "whole" arrived, in order, intact, in 9 of 9 runs with nothing else on the machine. With both cores kept busy
by other work, 175 of 182 runs of this check passed; the 7 that did not all ended the same way, described under
"Found by running it under load". Through the media server, 8 switches under 7 s of audio:
350 of 350 blocks, longest gap 23 to 40 ms. A participant with another secret heard nothing and nothing of its was
passed on. A participant with no frame was not let into the sealed call.

**The library against the nodes' own code** (`ribbit/cpp/tests/test_channel_frame_oracle.py`): one end
`core/channel_frame.py`'s `Link`, the other the library, in both directions, then the library at both ends. 30 checks
pass: not sealed, sealed, new keys, both algorithms, the three protocols, a sender that switches 40 messages before
the receiver's setting arrives (none taken until it does, then all 40 in order), a Role's own secret at steps 0, 1, 2,
`undo`, 200 random rounds with about 95 switches, another secret (nothing delivered), the setting's check (the
library's is the node's), and a loss asked for twice (every message arrives once). With one letter of the key's text changed in the library, the oracle fails.

**The Communicator's own tests on the one-writer plane**, without the frame: `test_call`, `test_call_audio`,
`test_jitter`, `test_bottom`, `test_edges`, `test_media_link`, `test_frame`, `test_segment`, `test_bearer` and
`tests/py/test_call_interop.py` pass. `test_media_rates` passed 7 of 8 runs here and 3 of 3 on the tree before this
change; the one failure was not repeated. `test_plane` needs the original `fnav.py`, which is not in the repository,
and was not run.

## Found by running it under load

The plane test was run more than 300 more times with one or both cores kept busy. Three faults came out; all three are fixed.

1. **The library sent fewer messages again than it had announced.** [A_REPEAT_ONCE_ANNOUNCED_IS_SENT_WHOLE_V1] When the
   far end asked twice for the same loss and its RECEIVED for the first repeat arrived during the second, the library
   skipped what had been acknowledged. The far end counts what it skips, so it skipped new messages too: 10 to 300
   bodies lost, about 1 run in 20 under load. `core/channel_frame.py` does not have this fault (it sends a repeat in
   one piece). Oracle check 12 reproduces it: 201 of 251 messages without the fix, 251 of 251 with it.
2. **fnav padded over a unit's length.** [THE_LENGTH_IS_NEVER_PADDED_V1] When the socket took fewer than the 4 length
   bytes of a unit and the plane gave the unit up, the abort padding was written where the rest of the length
   belonged. The far end then read a length nobody sent (2.9 GB in the run that was caught) and waited for ever.
   This was in fnav before this work, in C++ and Python, with or without the frame. Now the rest of the length goes
   as it is and only the body is padding.
3. **A plane whose writer failed left both ends waiting.** [A_BROKEN_PLANE_IS_CLOSED_V1] Now the writer says why on
   stderr and shuts the socket, so this end's reader and the far end both read "closed".

What is left is fnav's own rule and was not changed: a sender that waits (a participant, not the media server) ends
the connection when a unit it began cannot be finished in 250 ms and its padding cannot go in another 250 ms. With
both cores busy the kernel dropped segments at a full receive buffer and TCP waited 400 to 800 ms to send them again
(`ss` showed `rto:408 backoff:1 cwnd:1 lost:1` on both sockets with empty receive queues), which is longer than that
rule allows. That is the 7 of 182. A link that loses packets can do the same to a call with or without the frame.

## Since 2026-11-25: the Security tab, the memory moves, the phone

- **Every Communicator process** holds the read on the Role's setting and reports, under the frame or not. A report
  now says `framed` and `ram`, is written again at least every 10 s, and is read no older than 25 s.
- **Change RAM host** [THE_MEMORY_MOVES_V1]: `ram_host` and `ram_port` in the Role's setting, under its check, say
  where the memory is. `comms-channel --ram HOST:PORT move ROLE NEWHOST:NEWPORT` (another comms-ram must be running
  there) writes the setting at the new address first, carries the chat, then writes it at the old one. Every
  participant and the media server follow by their held read, assert their own tuples there and set up their held
  reads again. Measured under a real call: the command returned in 0.03 to 0.18 s ("3 end(s) there: 3 attached there
  (13 to 115 ms after the write)"), 24.0 video frames/s and 50.1 audio blocks/s across it, longest time with no new
  frame 61 ms; the old comms-ram was then killed and the call went on.
- **The Security tab** on the C++ window, the Python window and the phone: the cipher, New key, the protocol, Change
  RAM host, and every end with what it is running. The owner's work is in the library (`channel_show`,
  `channel_change`), which `comms-channel` calls too.
- **The phone** (`opt/fdf/src/frognet-android`): the frame in Java (`core/ChannelFrame.java`), one writer per plane,
  the Security tab, Import secret…. Its core passes 32 checks against the C++ library and, in a sealed call, nine
  switches and a memory move made by its own owner code with 23.8 to 23.9 video frames/s and 50 audio blocks/s
  throughout. The app was not built or run on a phone here.

`docs/CHANNEL-DESIGN-AND-IMPLEMENTATION.md` sections 3.10, 3.11, 4.6 and 6.5 to 6.7 have the detail.

## Not done

- The Windows build and the Android APK were not built or run (the phone's core ran on the JVM). The Windows code paths (the writer's waker, no file moved forward)
  are written and not compiled.
- Nothing ran on separate machines.
- The setting is in the Communicator's own memory, not the ControlRam, because the C++ Communicator builds without a
  FrogNet tree. A node's `channel_admin.py` does not write it; `comms-channel` does.
- Two things the frame does differently from `core/channel_frame.py` v13, both on the receiving side only and both
  using the same calls on the wire: rule 6 above, and [TWO_SETTINGS_AHEAD_IS_NOT_A_STRANGER_V1] (a receiver that has
  read "sealed" then "not sealed" before the sender's switch to sealed arrives retries; v13 ends the connection there).
