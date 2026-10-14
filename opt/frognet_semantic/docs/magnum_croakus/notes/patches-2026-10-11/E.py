import sys; sys.path.insert(0,'/tmp/claude-0/verify1011'); from patchlib import *
# Report E -- Part X (Ribbit Handlers and Domain Protocols). Source strings are raw, so \u2014 is the source escape.

# 1. opcodes
sub(r'''gld([ t("Fifteen opcodes are assigned. Two hundred and forty-one are free, and the bands were laid out with room to grow because nobody believed the first fifteen were the last fifteen.''',
    r'''gld([ t("Sixteen opcodes are assigned in Ribbit\u2019s wire (fifteen in the original Python core). Two hundred and forty are free, and the bands were laid out with room to grow because nobody believed the first sixteen were the last sixteen.''')

# 2. boardgame row
sub(r'''"databasehost    \u2014  (no-op)      implemented     implemented", "boardgame       \u2014  (no-op)      implemented     implemented", ''',
    r'''"databasehost    \u2014  (no-op)      implemented     implemented", ''')

# 5 + 6 + 7. lifecycle slot
sub(r'''onto the floating data name where ordinary consumers see it \u2014 so the very fact that a node "), i("can"), t(" host a role is itself a tuple in shared memory, put there by the handler, scored by everyone. The node runs its capability probe once; each handler folds any service-specific facts its own probe gathers into that picture \u2014 the media handler, for instance, adds whether OpenCV2 is present \u2014 and "), b("publishes"), t(" the result as its "), mono("<role>/capability"), t(" tuple. Nothing is handed''',
    r'''onto the floating data name where ordinary consumers see it \u2014 the second write is skipped while both names resolve to the same machine \u2014 so the very fact that a node "), i("can"), t(" host a role is itself a tuple in shared memory, put there by the handler, scored by everyone. That is FrogNet\u2019s own mode. On an FD&F region the "), mono("<role>/capability"), t(" tuples are not written; the election reads the node\u2019s capability from the facts the tunnel daemon announces. The node runs its capability probe once and every role handler "), b("publishes"), t(" that same blob as its "), mono("<role>/capability"), t(" tuple; the media election gates on what the probe found (ffmpeg with libvpx). Nothing is handed''')
sub(r'''The published tuple \u2014 shared base plus that service's own checks \u2014 is the complete picture the election in Part V scores''',
    r'''The published tuple is the complete picture the election in Part V scores''')

# 3. game protocol -> design example
sub(r'''The interface earns its name at the "), i("other"), t(" end of the range, where you implement a genuinely new protocol. Two do, and they are deliberately opposite, which is the proof: the same handler shape spans both.") ]),''',
    r'''The interface earns its name at the "), i("other"), t(" end of the range, where you implement a genuinely new protocol. SotF does. A turn-based game would be its opposite, and the two together show the range: the same handler shape spans both.") ]),''')
sub(r'''P([ t("The game protocol is the opposite extreme, and it shows the historical handler framework being used when there is no stream at all. Take the simplest turn-based game there is \u2014 tic-tac-toe. The board is one small value in the shared working memory: nine cells and whose turn it is. A game handler is not a codec and not a forwarder \u2014 it is the "), b("authority"), t(". A move arrives as an ordinary request carrying "), mono("_game"), t("; the handler reaches into that shared memory, checks it really is your turn, marks the cell, looks for three in a row, and writes the board back \u2014 then it sits until the next move. No stream, no delivery ladder, no rate machinery: turn-based and slow by nature, the game needs none of it. You read the board when your turn comes, exactly the way the monitor reads a sensor.") ]),''',
    r'''P([ t("A turn-based game would be the opposite extreme: the handler framework used when there is no stream at all. Take the simplest turn-based game there is \u2014 tic-tac-toe. The board would be one small value in the shared working memory: nine cells and whose turn it is. A game handler would not be a codec and not a forwarder \u2014 it would be the "), b("authority"), t(". A move arrives as an ordinary request carrying "), mono("_game"), t("; the handler checks the turn, marks the cell, looks for three in a row, and writes the board back \u2014 then it sits until the next move. No stream, no delivery ladder, no rate machinery: turn-based and slow by nature, the game needs none of it. You would read the board when your turn comes, the way the monitor reads a sensor. An early "), mono("/game"), t(" origin and a boardgame role were built and were removed on 25 September 2026; FrogNet ships no games.") ]),''')
sub(r'''exLabel("A move in tic-tac-toe is a write to shared memory"),''',
    r'''exLabel("A move in tic-tac-toe would be a write to shared memory"),''')
sub(r'''"the board is one tuple at databasehost.frognet:"''',
    r'''"the board would be one tuple at databasehost.frognet:"''')
sub(r'''"X moves \u2014 an ordinary POST carrying _game:"''',
    r'''"X would move \u2014 an ordinary POST carrying _game:"''')
sub(r'''"the game origin (the authority) checks it is X's turn, marks", "cell 8, looks for a line, writes the board back, sets turn:O.", "O reads the new board when it is O's turn."''',
    r'''"the game handler (the authority) would check it is X's turn,", "mark cell 8, look for a line, write the board back, set turn:O.", "O would read the new board when it is O's turn."''')
sub(r'''Tic-tac-toe is only the smallest one to hold in your head; the same "), mono("_game"), t(" machinery runs FrogNet's real games \u2014 connect four, reversi, backgammon, hearts, liar's dice \u2014 each just a different rulebook over the one shared-memory protocol.") ]),''',
    r'''Tic-tac-toe is only the smallest one to hold in your head.") ]),''')
sub(r'''Take backgammon \u2014 a real game in the set above \u2014 and count''',
    r'''Take backgammon \u2014 a game \u2014 and count''')

# 4. SAME size
sub(r'''an unchanged turn collapses to a four-byte SAME''',
    r'''an unchanged turn collapses to a 21-byte SAME''')

# 13 + 14. classifier
sub(r'''Content-Type is a weak hint and the body decides, resolving to json, xml, html, text or raw.''',
    r'''Content-Type decides for JSON and XML, HTML must also look like HTML, and otherwise the body decides, resolving to json, xml, html, text or raw.''')
sub(r'''a body that falls through to raw learns nothing and diffs against nothing, so every send of it is a FULL.''',
    r'''a body that falls through to raw is learned as a single field, so a repeat is SAME and any change resends the whole body.''')

# 8. worked example
sub(r'''"its transient DB is empty.  the merge fires hostReset() everywhere:"''',
    r'''"its transient DB is empty.  each node notices the move and re-advertises for itself:"''')

# 12. media codex
sub(r'''The media handler leans on this on purpose, nulling its control reference''',
    r'''The Python media codex in the simulator leans on this on purpose, nulling its control reference''')

# 10. advertise implementations
sub(r'''There is one implementation of advertise in the entire system, and every role gets it identically.''',
    r'''There is one implementation of advertise per platform, on the base class, and every role gets it identically.''')

# 9. the watcher
sub(r'''Instead, every process that holds live modules \u2014 the daemon, the proxy, the Communicator shell, each bundle application \u2014 already has a loop, and in that loop it does one trivial thing: it resolves the control name and compares the answer to the answer it got last time. If the address is different, it reconciles its own modules. If it is the same, it does nothing at all.''',
    r'''The daemon runs a watcher that, every 15 seconds, resolves databasehost_control.frognet from /etc/hosts and compares the answer to the one it got last time. If the address is different, it reconciles. If it is the same, it does nothing at all. The proxy publishes once at boot; the discovery merge publishes for its own node on each merge.''')

# 11. duck typing
sub(r'''Nothing registers it, nothing is wired, no interface is declared. It answers the verb, so it gets called.''',
    r'''No interface is declared: any module the process hands its watcher is called if it has a hostReset(); one that has none is skipped.''')

# 27. channel sets
sub(r'''and it rides its own channel sets so it never blocks, or is blocked by, anything else.''',
    r'''and it rides its own sockets, audio apart from video, so it never blocks, or is blocked by, anything else.''')

# 22 + 24. Adaptation paragraph
sub(r'''The ladder is a small set of named rungs, and the source simply sends the highest rung the link sustains \u2014 stepping down framerate first, then resolution, then shedding video for a mono voice bed, then text, then a single presence bit \u2014 and climbing back''',
    r'''The ladder is a small set of rungs, and the source sends the highest rung the link sustains \u2014 each step down a smaller picture, then shedding video for the voice, and stopping there \u2014 and climbing back''')

# 15 + 16. Server paragraph
sub(r'''SotF's mediahost is an elected role, not a box. It floats to a capable node, one per LAN, and exists because a call needs it. It mixes audio as the sum of the others and video as a grid of the others, minus your own image, composed per recipient. Nothing is provisioned; the fabric elects what it needs and lets it go when the need passes.") ]),''',
    r'''In the FrogNet implementation the media host was an elected role, mediahost, gated on ffmpeg with libvpx. The current Communicator\u2019s media server, comms-media on port 8994, is started on a node by fdf-comms.sh (or runs on the droplet) and says where it is in the pond\u2019s where cell; every Communicator finds it there, with nothing typed. Nobody points at it by address; it is not elected and it does not dissolve between calls. It fans each participant\u2019s audio and video to every other participant, never back to the sender, transcoding a source once for each smaller size a viewer needs; each receiver mixes the voices it gets.") ]),''')

# 24. Failure paragraph
sub(r'''SotF has no failure floor, because the floor is presence. The same call walks itself from high-definition video down to a grey murmur of voice, down to text, down to a one-byte \"I am still here,\" and back up the moment the link allows''',
    r'''SotF's floor is the voice. The same call walks itself from high-definition video down to a gray thumbnail and then to voice alone, and back up the moment the link allows''')
sub(r'''and a call can fall to a heartbeat instead of dying.''',
    r'''and a call can fall to voice instead of dying.''')

# comparison codeBlock rows (items 15, 16, 22, 24)
sub(r'''"Adaptation    continuous bitrate       discrete ladder;", "              knob / variants /        framerate-first then", "              layered + SFU            resolution; per-leg", "", "Server        fixed SFU/MCU/TURN       elected mediahost role,", "              you point at             floats per-LAN, mixes", "                                       minus-self", ''',
    r'''"Adaptation    continuous bitrate       discrete ladder;", "              knob / variants /        each step a smaller", "              layered + SFU            picture; per-leg", "", "Server        fixed SFU/MCU/TURN       comms-media, found in", "              you point at             the where cell; fans", "                                       minus-self", ''')
sub(r'''"Failure       freeze / drop, then      degrades to a presence", "              re-negotiate             bit, climbs back; never", "                                       dies"''',
    r'''"Failure       freeze / drop, then      degrades to voice alone,", "              re-negotiate             climbs back; never", "                                       dies"''')

# 17. transport
sub(r'''We did not invent a transport for this. The media channels are raised the same way the proxy and daemon raise theirs \u2014 the same channel bring-up FrogNet already uses everywhere else. Whatever we did with the data path, it would stand on machinery the network already trusted, not a parallel stack built beside it.''',
    r'''We did not invent a transport for this. The media planes are two ordinary TCP connections from the Communicator to its media server, each carrying length-prefixed media frames. No new transport was invented; it is TCP, made never to wait.''')
sub(r'''Take the connection we already raise for the proxy and daemon, refuse to ever let a send block on it,''',
    r'''Take an ordinary TCP connection, refuse to ever let a send block on it,''')

# 15 / 24. section 32
sub(r'''that thins to a single byte of presence and thickens back to high definition''',
    r'''that thins to voice alone and thickens back to high definition''')
sub(r'''A media server no one installed, that elects itself onto whatever node is fit and dissolves when the call ends.''',
    r'''A media server nobody points at by address, that every Communicator finds in the pond\u2019s where cell.''')

# 17. section 33 data plane
sub(r'''The data plane is the channel pair, raised the same way the proxy and daemon raise their connections, so the media inherits the network's existing transport instead of a new one bolted on the side. Across it''',
    r'''The data plane is two ordinary TCP connections from the Communicator to its media server, one for audio and one for video, each carrying length-prefixed media frames. No new transport was invented; it is TCP, made never to wait. Across it''')

# 18. non-blocking
sub(r''', because the naive move is wrong. Set the whole socket non-blocking and you do get a send that returns rather than stalls \u2014 and you also break the receive loop, which now raises instead of blocking the way the rest of the code expects. We learned this the loud way, on Windows, where it surfaced as a blocking-mode error on a socket we had quietly made non-blocking under everyone's feet. The fix is narrower than the reflex: leave the socket able to block on receive, and gate only the "), i("send"), t(" on writability. Before writing a frame, ask the socket whether it can take one right now; if it can, send it; if it cannot, drop it and reach for the next, newer frame. The send never blocks because we never call it when it would. One handler, two planes, and a single careful seam between a socket that must stay patient to listen and a send that must never wait to speak.") ]),''',
    r'''. Every socket is non-blocking, receive included; both directions ask the socket first (writable? readable?), and a frame that will not fit is dropped whole. Before writing a frame, the code asks the socket whether it can take one right now; if it can, it sends it; if it cannot, it drops it and reaches for the next, newer frame. The send never blocks because it is never called when it would. One handler, two planes, and neither one ever waits.") ]),''')

# 15 + 16. A role is which planes you hold
sub(r'''P([ t("There is no media server in the building. There is a "), i("role"), t(" \u2014 mediahost \u2014 and it floats, by the same election that places every other role in FrogNet, onto whichever node on the LAN is fit to carry it. It exists because a call needs it and dissolves when the call ends; nobody installs it, nobody points at it, and if the node holding it goes away the role simply re-forms somewhere else.") ]),''',
    r'''P([ t("In the FrogNet implementation the media host was an elected role, "), mono("mediahost"), t(", gated on ffmpeg with libvpx. The current Communicator\u2019s media server, "), mono("comms-media"), t(" on port 8994, is started on a node by "), mono("fdf-comms.sh"), t(" (or runs on the droplet) and says where it is in the pond\u2019s where cell; every Communicator finds it there, with nothing typed. Nobody points at it by address; it is not elected and it does not dissolve between calls.") ]),''')
sub(r'''Each participant sends one uplink. The host composes, for every recipient individually, a personalized mix: audio summed from all the "), i("other"), t(" participants, video laid out as a grid of the "), i("other"), t(" participants \u2014 minus your own image and your own voice, because nobody needs to be sent themselves.''',
    r'''Each participant sends one uplink. The server fans each participant\u2019s audio and video to every "), i("other"), t(" participant, never back to the sender, because nobody needs to be sent themselves; it transcodes a source once for each smaller size a viewer needs, and each receiver mixes the voices it gets.''')

# 19 + 20. ladder intro and table
sub(r'''it descends a ladder of named rungs, each one a smaller promise''',
    r'''it descends a ladder of rungs, each one a smaller promise''')
sub(r'''...codeBlock(["rung           carries                                 needs", "---------------------------------------------------------------------", "L8 BULLFROG    1920x1080 colour video + mono audio      camera", "L7 CHORUS      1280x720 colour video + mono audio       camera", "L6 ENSEMBLE    854x480 colour video + mono audio        camera", "L5 DUET        640x360 GRAYSCALE video + mono audio     camera", "L4 SOLO        mono audio (video shed)                  mic", "L3 VOICE       mono audio                               mic", "L2 WHISPER     plaintext text                           --", "L1 BEACON      token vocabulary                         --", "L0 PULSE       presence                                 --", "---------------------------------------------------------------------", "video target bitrates: 3 Mbps / 1.2 Mbps / 600 kbps / 300 kbps", "audio on every rung that carries it: 16 kHz mono s16le"]),''',
    r'''...codeBlock(["rung      carries                                   needs", "---------------------------------------------------------------------", "L8        1920x1080 colour video + audio  3 Mb/s     camera", "L7        1280x720  colour video + audio  1.2 Mb/s   camera", "L6        854x480   colour video + audio  600 kb/s   camera", "L5        640x360 gray, then 480x360 / 320x240 /", "          160x120 gray (4:3)  + audio   300/250/150/60 kb/s", "below L5  audio only                                  mic", "---------------------------------------------------------------------", "audio on every rung that carries it: Opus, 24 kb/s, 16 kHz mono"]), P([ t("The rungs once carried names, BULLFROG at L8 down to PULSE at L0, and a text floor; the running code has the video sizes and audio only.") ]),''')

# 21. ceiling
sub(r'''A camera and a mic ceiling at BULLFROG. A video rung needs a camera and only a camera: kill the audio and the ceiling stays at BULLFROG \u2014 the picture goes out''',
    r'''A camera ceilings at the largest rung its picture fills: L8 for a 1080p camera, L7 for 720p. With no camera the call sends audio only. A video rung needs a camera and only a camera: kill the audio and the ceiling stays where it was \u2014 the picture goes out''')
sub(r'''A microphone alone, or a deliberate no-video, ceils at SOLO, the top audio-only rung. Neither, and the ceiling is WHISPER. The bearer''',
    r'''The bearer''')
sub(r'''the richest thing we are both allowed and able to deliver. WHISPER, BEACON, and PULSE need no hardware at all, which is why the floor is never the floor of the network but the floor of presence: a call can always find a rung, even if the rung is a single byte.") ]),''',
    r'''the richest thing we are both allowed and able to deliver. Below the smallest gray picture the call is voice alone, and that is the floor.") ]),''')

# 23. up/down rule
sub(r'''Drops accumulating means step down; the link going quiet and accepting frames again means there is room to step back up.''',
    r'''Drops accumulating means step down. Up comes only after three clean seconds at full frame rate, and a rung that just failed is held off for 15 seconds, doubling to five minutes.''')

# 22. framerate
sub(r'''P([ t("The descent is framerate-first. Hold the resolution and thin the motion \u2014 24 to 15 to 7 frames a second \u2014 because a slightly stuttery sharp picture reads better than a smooth blurred one, and only when 7 fps still will not fit do we drop to the next resolution and reset the framerate to 24. Climbing back reverses the path. A band''',
    r'''P([ t("The frame rate is held at its target, 24 by default; falling below it is itself a reason to step down. Each step down is a smaller picture: 1080p, 720p, 480p, 360p gray, then 480x360, 320x240 and 160x120 gray, then audio only. Climbing back reverses the path. A band''')

# 24. why it never dies
sub(r'''the data plane is free to shed everything down to a single presence bit; and the ladder guarantees that between high definition and that one byte there is always another rung to stand on. So the call rides its link down \u2014 bullfrog to chorus to ensemble to a 360p murmur to voice to text to a beacon to the bare pulse of "), i("still here"), t(" \u2014 and rides it back up''',
    r'''the data plane is free to shed every picture down to voice alone; and the ladder guarantees that between high definition and voice there is always another rung to stand on. So the call rides its link down \u2014 1080p to 720p to 480p to a 360p gray picture, smaller still, then voice \u2014 and rides it back up''')

# 26. planes framing
sub(r'''two persistent sockets, each carrying FNWP, declared at connect.''',
    r'''two persistent sockets, each carrying length-prefixed media frames, the first frame on each declaring audio or video.''')

# 25. adaptation driver
sub(r'''bullet([ b("Adaptation is driven by keyframe queue pressure."), t(" Not by a bitrate table and not by an estimate of the link. If the relay is holding keyframes it cannot deliver, the rung does not fit \u2014 whatever the arithmetic says. Measured: on links where the table says video is impossible, the wire was carrying it.") ]),''',
    r'''bullet([ b("Adaptation is driven by what actually reached the viewer."), t(" Frames delivered against frames aimed, per two-second window, stepping down under 80% for two windows and up after three good windows and 45 settled seconds, plus any cap the viewer set. Measured: on links where the arithmetic says video is impossible, the wire was carrying it.") ]),''')

# 22. delete framerate-first sentence
sub(r'''P([ t("The framerate-first descent described above still holds within a rung. What changed is what decides the rung,''',
    r'''P([ t("What changed is what decides the rung,''')

# 28. security row
sub(r'''"Proximity + mesh membership; no per-service TLS on internal traffic."''',
    r'''"None forced: the inter-site tunnel is WireGuard; access control and further layers are the builder\u2019s to add."''')

done()
