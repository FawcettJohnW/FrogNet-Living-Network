################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#                                                              #
#  SPDX-License-Identifier: GPL-2.0-only                       #
#                                                              #
#  This program is free software; you can redistribute it      #
#  and/or modify it under the terms of the GNU General Public  #
#  License as published by the Free Software Foundation;       #
#  version 2 of the License, and no other version.             #
#                                                              #
#  This program is distributed in the hope that it will be     #
#  useful, but WITHOUT ANY WARRANTY; without even the implied  #
#  warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR     #
#  PURPOSE.  See the GNU General Public License for details.   #
#                                                              #
#  See COPYRIGHT and LICENSE at the root of this tree.         #
################################################################
import sys; sys.path.insert(0,'/tmp/claude-0/verify1011'); from patchlib import *
import patchlib

def span(a, z):
    s = patchlib.s
    if s.count(a) != 1: raise SystemExit('SPAN START (%d): %r' % (s.count(a), a[:120]))
    i = s.index(a); j = s.index(z, i) + len(z)
    return s[i:j]

# ---------------- Part XIV ----------------
# §44 Authority: the role name is boardgame.frognet
sub('resolved by name as "), mono("<game>.frognet")', 'resolved by name as "), mono("boardgame.frognet")')

# §44: the requests are schematic
sub('The value changes in shared memory, and the engine, watching its region, notices.") ]),',
    'The value changes in shared memory, and the engine, watching its region, notices. Schematically, a move is a write such as the one below; the request line shows its shape, not a live endpoint.") ]),')
sub('''        "# Player X states intent \\u2014 a write to X's OWN object, not the board",
        "POST <game>.frognet/api.php?entity=move&action=upsert_by_name",''',
    '''        "# schematic: the shape of the write, not a live endpoint",
        "# Player X states intent \\u2014 a write to X's OWN object, not the board",
        "POST boardgame.frognet/api.php?entity=move&action=upsert_by_name",''')

# §44: the Communicator resolves no role name
sub('it is the same loop the Communicator uses for a call: resolve the role name, read your region to render, write your own intent.")',
    'it is the same loop the Communicator uses for a call: read your Region to render, write your own rows.")')

# §45: list and join examples are schematic; the oracle is not green in production
sub('''        "GET <game>.frognet/api.php?entity=game&action=list&game=backgammon",''',
    '''        "# schematic: the shape of the read, not a live endpoint",
        "GET boardgame.frognet/api.php?entity=game&action=list&game=backgammon",''')
sub('''        "# Bao writes a JOIN intent into Bao's OWN object (not the board)",
        "POST <game>.frognet/api.php?entity=move&action=upsert_by_name",''',
    '''        "# schematic: the shape of the write, not a live endpoint",
        "# Bao writes a JOIN intent into Bao's OWN object (not the board)",
        "POST boardgame.frognet/api.php?entity=move&action=upsert_by_name",''')
sub('''        "# the oracle above is now GREEN.",''',
    '''        "# open_tables() turns the oracle green against the engine; the",
        "# production store and the lobby UI still have to be wired.",''')

# §45 lesson: the Communicator is not built from game handlers
sub('The engine you just extended sits beneath the same handlers the whole user-facing surface is built from \\u2014 presence, chat, media, and games, each a handler over the one interface. That surface is the Communicator, and it is where the book ends.")',
    'The engine you just extended is one handler; the Communicator in the next part is presence, chat and calls written as rows in its own Region, with media on its own wire. It carries no games.")')

# ---------------- Part XV opening ----------------
sub(span('lead([ t("Every term of this book at once, in one application. The Communicator is alive', 'so the layers get tested.") ]),'),
    U(r'''lead([ t("Every term of this book at once, in one application. The Communicator is alive — it finds its servers in the pond’s memory instead of being told an address. It has memory — every participant publishes what it alone knows, and reads everyone else’s. It learns — its rows cross the wire as differences against what both ends hold. It grows — one script on a node starts its servers and says where they are, and every member can call. And it is wrong in places, which is the last term and the reason the next part exists. It is a very complicated Hello, World, and its job is to touch every layer so the layers get tested.") ]),'''))

# ---------------- §46 REWRITE ----------------
sub(span('      P([ t("The implementation chapters converge on one substantial worked example.',
         'everything you add is another handler that plays by them.") ]),'),
    U(r'''      P([ t("The Communicator is a video phone with a lobby and chat. It is an "), i("example program"), t(", not the product: the worked proof that a Ribbit Region, the FNWP wire with BLDC-1 under it, and Song of the Frogs policy compose into an application a person opens, and a program you can read end to end to learn how to build your own. It was rewritten in C++ in October 2026; the Python Communicator, its games and its "), mono(":9000"), t(" media host are gone.") ]),
      img("hello_world.png", 600, 250),
      // [COMMS_R3_REWRITE_V1] section 46 rewritten against comms-app r3 and Communicator r5, 2026-10-11
      H2("The pieces"),
      P([ t("Four programs, in "), mono("ribbit/examples/communicator"), t(":") ]),
      bullet([ mono("comms-ram"), t(", on TCP 8800: the Communicator’s memory. It is a plain Ribbit RAM host with no operations of its own. Presence, chat, calls and media speeds are rows in it.") ]),
      bullet([ mono("comms-media"), t(", on TCP 8994: the media server. Every call meets there. It forwards each participant’s audio to the others in the call, and passes each participant’s video through or transcodes it down for a viewer who cannot take it.") ]),
      bullet([ mono("comms-app"), t(": the window (C++, Qt 6). Lobby on the left, the call in the centre, chat on the right.") ]),
      bullet([ mono("comms-feed"), t(": an unattended camera or file. It shows in the lobby as "), mono("*name"), t(".") ]),
      P([ mono("python/comms_app.py"), t(" is a second implementation of the same contract (PySide6). It shares nothing with the C++ but the rows in "), mono("comms-ram"), t(", the wire to "), mono("comms-media"), t(" and the rules, and the two meet in one lobby and one call.") ]),
      P([ t("On the phone the Communicator is a screen of the FrogNet app, "), mono("CommunicatorActivity"), t(" (r5). Its core — room, fnav wire, bearer, mixer — is Java, proven against the C++ and Python. It sends VP8 through MediaCodec from a Camera2 picture drawn by GL into the encoder, and Opus through MediaCodec.") ]),
      H2("Building and running it"),
      P([ t("On Linux or a Pi, "), mono("./build.sh"), t(" builds all four. On Windows, "), mono("build-windows.ps1"), t(" builds "), mono("comms-app"), t(" and "), mono("comms-feed"), t(" with Visual Studio and vcpkg; "), mono("comms-ram"), t(" and "), mono("comms-media"), t(" run on the Linux side. The libraries are FFmpeg (libavcodec, libswscale, libavdevice), libvpx, libopus, PortAudio and Qt 6.") ]),
      P([ t("On a FrogNet node, "), mono("fdf-comms.sh"), t(" starts "), mono("comms-ram"), t(" and "), mono("comms-media"), t(" and writes one cell in the pond’s memory saying where they are:") ]),
      ...codeBlock(["(pond, where, w:<guid>)", "  {\"comms\": {\"ram\": \"w:<guid>:8800\", \"media\": \"w:<guid>:8994\"}}"]),
      P([ t("The cell names the node by identity, not by address; each reader resolves it to the address that node has in the reader’s own scope. The region removes the cell when the node’s session ends, and "), mono("fdf-comms.sh"), t(" writes it again on the event that shows the node back. On the droplet, which has no .1, "), mono("fdf-services.sh"), t(" publishes the public address instead.") ]),
      P([ t("Every program says what it is first: "), mono("comms-app"), t(" prints "), mono("[app] build comms-app-2026-10-10-r3"), t(" and carries it in the window title; the phone logs "), mono("comms-android-2026-10-11-r5"), t(".") ]),
      H2("Finding your camera, microphone and speakers"),
      P([ mono("comms-app --list-devices"), t(" prints the cameras as the exact "), mono("--camera"), t(" value to type — v4l2 on Linux, DirectShow on Windows — and the PortAudio table, with the index you give as "), mono("--mic N"), t(" and "), mono("--speaker N"), t(". With no index the system default is used. A camera is asked for MJPEG: without it V4L2 hands out YUYV, and 1920x1080 YUYV is a USB 2.0 limit read as a machine limit. MJPEG’s full-range levels are said, not assumed, so the picture is not washed out.") ]),
      P([ t("A device’s claim is not trusted. The microphone and speaker are opened mono at the device’s own rate and resampled to and from the 16 kHz wire, and the rate the microphone really delivers is measured in its first seconds: an eMeet C950 claims 44.1 kHz and delivers about 16 kHz. A live camera has its own reader thread that keeps only the newest frame, so the picture does not fall behind the voice.") ]),
      P([ t("You do not need a camera. Receiving needs none, only "), i("sending"), t(" does. A camera that will not open is reported and the call goes on receiving.") ]),
      H2("Finding the servers"),
      P([ t("On the desktop you give the Communicator its name and the two addresses: "), mono("--name"), t(", "), mono("--ram HOST:8800"), t(", "), mono("--media HOST:8994"), t(". On Windows, "), mono("fdf-bridge.cmd ask comms"), t(" prints the "), mono("--ram"), t(" and "), mono("--media"), t(" of the forward pond’s Communicator. On the phone nothing is typed: the FrogNet member reads its pond’s where cells for "), mono("comms"), t(", resolves the identity in its own scope, and opens the Communicator with the two addresses and the device’s name. If no member of the pond runs one, it says so and opens nothing.") ]),
      H2("Presence, the lobby and chat"),
      P([ t("Joining is not a request a server grants. There is no login and no session handed to you. Each participant writes one presence row, "), mono("host:<ip>:presence:<me_id> {name, status, caps, ts}"), t(", and writes it again every 5 seconds. A row older than 15 seconds is gone. There is no logout and no delete: stale is gone. A "), mono("comms-ram"), t(" that restarts loses nobody, because the roster was never its; it is the set of fresh rows.") ]),
      P([ t("Each participant keeps its own copy of the Communicator’s state by one held read — “wake me when anything newer than write-order id N exists” — and answers the lobby, chat, offers and invitations from that copy. Its own writes go into the copy at once. A chat line reaches the other participant’s copy in about a millisecond; an idle room makes a handful of requests a minute.") ]),
      P([ t("Chat is one row per line, "), mono("session:chat:<session>:<msg_id>"), t(", written by the sender and read in time order.") ]),
      H2("Starting and joining a call"),
      P([ t("You start a call by writing your "), mono("CallsAnnounce"), t(" row, at "), mono("user:<your name>"), t(", listing the session and the media server’s host and port. You invite someone by writing a "), mono("CallsInvitations"), t(" row at "), mono("user:<their name>"), t(". An invitation rings only while it is under 60 seconds old and its caller still offers that session, so a call the caller gave up on stops ringing. Hanging up takes the session out of your rows.") ]),
      P([ t("Joining is reading the offer and opening two TCP connections to the media server, one for audio and one for video. The first frame on each says which plane it is, an 8-byte teardown tag shared by the pair, and the session. Every call shares the one port; the server keeps their fans apart by session. Losing one plane closes its mate, and only its mate: half a caller is not a caller.") ]),
      P([ t("The split is total. Control — who is here, what is offered, who is invited, each viewer’s speed — is rows in "), mono("comms-ram"), t(", crossing the FNWP wire full, then diff, then same. Audio and video never touch it: they go to "), mono("comms-media"), t(" in fnav frames, "), mono("[len][kind][src][payload]"), t(", send-or-drop.") ]),
      H2("What the media server does"),
      P([ t("Audio is forwarded unchanged to every other participant in the session, and each client mixes the voices it receives. Audio is never what is given up while video is still going to that viewer.") ]),
      P([ t("Video is one upload. A viewer who can take it gets the sender’s own stream, byte for byte. A viewer who cannot gets a smaller picture the server encodes — once per size, whatever the number of viewers at that size, on a worker that is not the thread reading the upload. A viewer is never sent anything larger than the upload. What a viewer can take is the smaller of two things: its MediaSpeed row, "), mono("session:<session>:viewer:<me_id> {bps}"), t(", which the server holds a read on, and its measured link. One slow viewer never lowers anyone else.") ]),
      P([ t("The server publishes what it is doing for each viewer as a MediaHold row every 2 seconds — pass-through or the size it sends, keyframe held, audio only — and the window shows it, including “No video — Audio only” instead of a frozen last frame.") ]),
      H2("The tests"),
      P([ t("The claims above are proven by the tests in the tree, and you can run them: "), mono("test_room"), t(" and "), mono("test_room_watch"), t(" on a real "), mono("comms-ram"), t(" (lobby, chat in order, MediaSpeed matched by identity, the held read), "), mono("test_ring"), t(" (rings only while offered), "), mono("test_call"), t(" and "), mono("test_call_audio"), t(" (two participants through "), mono("comms-media"), t(", video at each other’s size, tones in each other’s speakers), "), mono("test_feed_view"), t(" (a receive-only viewer), "), mono("test_media_rates"), t(" and "), mono("test_media_link"), t(" (per-viewer sizes from the cap and from the measured link), "), mono("test_bottom"), t(" (the bottom walk). The phone’s core runs the same vectors and a Robolectric run of the app’s own screen against live servers ("), mono("communicator-test/run_tests.sh"), t("). "), mono("TESTING.md"), t(" says how to run each.") ]),
      P([ t("The Communicator carries no games. The game model is Part XIV’s.") ]),'''))

# ---------------- §47 ----------------
sub('t("The ladder in Part VII tells you which rung a call should be on.', 't("The ladder in Part X tells you which rung a call should be on.')
sub(span('      P([ t("The data plane is a "), b("non-blocking TCP socket")', 'Halve it, or do not ask.") ]),'),
    U(r'''      P([ t("Each plane is a "), b("non-blocking TCP socket"), t(", and the write is the test. There is no send queue and no pacing thread. An inter frame gets one try; if the socket will not take it, it is dropped and the next one is offered. A keyframe gets three tries and an audio block three, 8 ms apart, because one would-block is not a verdict.") ]),
      P([ mono("select()"), t(" reports a socket writable when the send buffer has "), i("some"), t(" room — not room for your frame. On Linux every sender computes free space as "), mono("SO_SNDBUF"), t(" minus "), mono("SIOCOUTQ"), t(", and believes a shortage only when the queue is past three quarters of the buffer, because the two numbers are not in the same units: Linux doubles the buffer it reports and counts its own overhead in the queue. Windows has no "), mono("SIOCOUTQ"), t("; there the socket’s would-block decides, and the buffer is 64 KiB instead of 128 KiB, to bound how much stale picture can wait in it.") ]),
      P([ t("One lock per socket serialises whole frames onto it. On the media server a viewer’s socket is written in debt mode: a part-written frame’s remainder is owed as abort padding and paid as the socket takes it, and new frames for that viewer are shed meanwhile. A viewer never makes a sender’s thread wait.") ]),'''))
sub('      P([ t("That is why audio still arrives at a hundred and twenty kilobits a second while the video is visibly degraded and may freeze outright. The picture is being rebuilt in pieces around a conversation that never stopped.") ]),',
    '      P([ t("That is why audio still arrives \\u2014 Opus at 24 kb/s \\u2014 while the video is visibly degraded and may freeze outright. The picture is being rebuilt in pieces around a conversation that never stopped. A frame larger than what the socket has been accepting in 20 ms (at most half the buffer) goes as "), mono("VSEG"), t(" pieces of 1368 bytes, the tunnels\\u2019 MSS.") ]),')
sub('It makes the per-viewer bandwidth cap honest as well. The relay budgets the video plane and never touches the audio plane.',
    'It makes the per-viewer bandwidth cap honest as well. The media server sizes and sheds the video plane per viewer and never touches the audio plane.')

# ---------------- §48 ----------------
sub(span('      P([ mono("select"), t(" reports a socket writable when the send buffer has SOME room', 'where it returns -1.") ]),'),
    '      P([ mono("select"), t(" reports a socket writable when the send buffer has SOME room, not room for your frame. The one call that gives a real number is "), mono("SIOCOUTQ"), t(" against "), mono("SO_SNDBUF"), t(", and it is Linux-only.") ]),')

# ---------------- §49 ----------------
sub('in the high bit of the level field, which carries a value of 0 to 7 and has bits to spare.',
    'in the high bit of the level field, which carries a rung of 0 to 8 and has bits to spare.')
sub(span('      P([ b("A held keyframe is allowed to overdraw its budget."),', 'it is paid in arrears rather than in advance.") ]),'),
    '      P([ t("On the media server a held keyframe waits at most three seconds; then it is dropped and the viewer resumes at the next one. An unanchored viewer with nothing held asks for a keyframe: of the sender, for its own stream, at most once per 3 seconds; of the server\\u2019s own encoder, for a transcoded size. The server has no budget bucket: a viewer\\u2019s cap chooses the picture size it is sent, so the keyframe fits by construction. The overdraw lives in the sender\\u2019s throttle, the bandwidth slider: a keyframe always goes, the budget goes negative, and the inter frames behind it repay the debt.") ]),')

# ---------------- §50 ----------------
sub(span('      P([ t("The obvious way to choose a rung is to compare its bitrate against the link rate.', 'So the ceiling follows the fact.") ]),'),
    '      P([ t("The obvious way to choose a picture size is to compare its bitrate with the link rate. It is also wrong: there are links on which the arithmetic says video is impossible, and the wire was carrying it.") ]),')
sub(span('      P([ t("The relay already knows. A viewer with a keyframe it cannot send', 'the case above being exactly that.") ]),'),
    U(r'''      P([ t("The media server keeps the evidence per viewer and acts on it for that viewer only; the sender is never told. Every 2 seconds it compares what it aimed at each viewer with what that viewer’s socket took. Two windows below 80 percent and the viewer steps down one size — from its transcoded size, or from the upload’s own size, read from the rung in the upload’s frame headers — and the size that failed is held off for 15 seconds, doubling to five minutes. Three good windows, and no change in the last 45 seconds, and it climbs one size; back at the upload’s size it is passed the upload again. A window in which nothing was aimed is not a measurement.") ]),
      P([ t("The sender has its own ladder, the bearer, and it governs only the upload: what the wire to the media server takes. The two never cross. A slow viewer gets a smaller picture; the sender keeps sending what its own link carries.") ]),
      P([ b("The link rate is still worth knowing, as an opening value."), t(" A client publishes its bandwidth setting as MediaSpeed, and the media server serves that viewer within it from the first frame: the largest size whose bitrate plus 32 kb/s of audio fits, or audio only. The sender’s own ceiling is what its camera can fill; the bearer finds the rest. Where a call SETTLES is decided by the evidence.") ]),'''))

# ---------------- §51 ----------------
sub('      P([ t("Part VII describes the rungs. This is the controller: what makes it step, and why each rule is shaped the way it is. Down is cheap and fast; up is expensive and slow. That asymmetry is the whole design.") ]),',
    U(r'''      P([ t("Part X describes the rungs. This is the sender’s controller, the bearer, sampled once per video frame — 24 a second by default. Down is cheap and fast; up is expensive and slow. That asymmetry is the whole design.") ]),
      P([ t("The rungs today: L8 1920x1080 at 3 Mb/s, L7 1280x720 at 1.2 Mb/s, L6 854x480 at 600 kb/s, L5 640x360 at 300 kb/s in gray. Below L5 the picture shrinks: 480x360 at 250 kb/s, 320x240 at 150 kb/s, 160x120 at 60 kb/s, all gray. Video is VP8 (realtime, cpu-used 8, a keyframe at least every 2 seconds); a sender never sends a picture larger than its source.") ]),'''))
sub(span('      P([ t("The bearer steps down immediately on any of these, and they are checked in this order:") ]),', 't(" per the previous chapter.") ]),'),
    U(r'''      P([ t("The bearer steps down at once on any of these, checked in this order:") ]),
      bullet([ b("Audio shed by the wire."), t(" The smallest, most protected thing on the plane being refused is the loudest congestion signal there is.") ]),
      bullet([ b("More than three consecutive samples carrying drops, or a backlog of eight frames."), t(" A short vicious burst should not have to wait for a clock to tick.") ]),
      bullet([ b("More than five drops inside one second."), t(" This is the rate rule, and it exists because of the sampling problem below.") ]),
      bullet([ b("More than three consecutive samples below the frame-rate floor,"), t(" the greater of ten frames per second and 75 percent of the target. A rung that has just changed is not judged until 15 frames and 2 seconds have been sent at it.") ]),'''))
sub('      exLabel("The ladder walked by hand, both ends watching"),',
    '      exLabel("The ladder walked by hand, August 2026, the Python fnav Communicator"),')
sub(span('      P([ t("The controls that produced this are on the call bar rather than on a router:', 'ordinary desk.") ]),'),
    U(r'''      P([ t("The controls that do this today are in the window: a Bandwidth slider and a Jitter slider. Bandwidth is your link, both directions. It runs from 32 kb/s to 4 Mb/s on a log scale, unlimited at the far right, and takes effect when the slider rests for 200 ms. It throttles your upload, so your bearer walks down and the others see a smaller picture of you, and it writes your MediaSpeed, so the media server sends you a smaller picture. Jitter, 0 to 500 ms, is also both directions: each arriving frame is delayed by a random 0 to N ms without reordering, and each frame you send waits up to N ms. Being able to starve your own call from inside the application is what makes the behaviour checkable by somebody who does not own a link shaper, and it is why Appendix J can ask a reader to reproduce it on an ordinary desk.") ]),
      P([ t("Each sender burns its frame count and its wall clock at capture into the bottom-left of the picture, and the viewer shows its own clock beside it, so the lag is read off the screen: 64 ms feed to media server to window on one machine. "), mono("COMMS_STAMP=0"), t(" turns it off.") ]),'''))
sub(span('      P([ t("Congestion walks the ladder down to the protected audio rung and stops there.', 'the bottom is a long way down. Appendix J'),
    U(r'''      P([ t("Below L5 the picture is not thrown away; it gets smaller — 640x360, 480x360, 320x240, 160x120 — one step at most every 2 seconds, and only when the wire is refusing frames, because shrinking a picture helps only if the wire is the limit. It grows back one step after three clean 2-second windows, and when it is back at 640x360 the ladder resumes at L5. Only when 160x120 is refused too does the sender go audio only, and it stays there until the ladder is back at L5. Video is not gone; it is smaller. Audio is never what the ladder gives up.") ]),
      P([ b("A video rung needs a camera, not a microphone."), t(" A camera with no microphone sends video with no audio bed, and the far end is told there is no audio.") ]),
      P([ t("On one run — the window at 128 kb/s — it received 160x120 gray at 24 fps with audio continuous while its own sender walked down the ladder.") ]),
      P([ b("Do not take that number as a specification."), t(" A static scene compresses to almost nothing and a moving one does not; a software encoder on a small board runs out before the link does. The figure is one run with one thing in front of one camera, and quoting it without those conditions would make it a benchmark rather than a measurement. What generalises is the "), i("shape"), t(": the picture is what the ladder spends, the voice is what it protects, and the bottom is a long way down. Appendix J'''))

# ---------------- §51a / §52 ----------------
sub('      P([ t("The ladder chapters above describe how the media path decides. This chapter is the bill for getting it right, kept because every line of it is a mistake somebody else can now avoid, and because several are not about media at all.") ]),',
    '      P([ t("The ladder chapters above describe how the media path decides. This chapter is the bill for getting it right, kept because every line of it is a mistake somebody else can now avoid, and because several are not about media at all.") ]),\n'
    '      P([ t("In August every participant derived one rate for the whole call from the same rows, with "), mono("derive_rate"), t(". The rewrite replaced it: one upload, and the media server converts down per viewer.") ]),')
sub('"256 KiB at 30 KB/s is 8.5 seconds of stale queued video, during which every new frame is refused at any size."]',
    '"256 KiB at 30 KB/s is 8.5 seconds of stale queued video, during which every new frame is refused at any size; the buffer is now 128 KiB on Linux and 64 KiB on Windows."]')
sub('The Communicator is valuable because it exercised all of those concerns in one program and exposed where the original boundaries were wrong.',
    'The Communicator is valuable because it exercised all of those concerns in one program \\u2014 its rows over Ribbit, BLDC-1 and FNWP, its media on a wire of its own \\u2014 and exposed where the original boundaries were wrong.')

# ---------------- anchors for removed sub-headings ----------------
sub('''  "s-part-i-basics-and-common": "part-i-basics-and-common",
  "s-part-ii-the-video-phone": "part-ii-the-video-phone",
  "s-part-iii-games": "part-iii-games",''',
    '''  "s-part-i-basics-and-common": "46-the-communicator",
  "s-part-ii-the-video-phone": "starting-and-joining-a-call",
  "s-part-iii-games": "46-the-communicator",
  // [COMMS_R3_REWRITE_V1] sub-headings removed by the section 46/51 rewrite
  "part-i-basics-and-common": "46-the-communicator",
  "part-ii-the-video-phone": "starting-and-joining-a-call",
  "part-iii-games": "46-the-communicator",
  "the-rule-itself-in-full": "51a-what-the-media-path-cost",
  "what-it-does-when-you-drag-it": "51-how-the-ladder-actually-decides",''')
done()
