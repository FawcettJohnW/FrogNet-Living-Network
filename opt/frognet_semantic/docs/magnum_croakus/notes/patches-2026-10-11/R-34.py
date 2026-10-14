import sys; sys.path.insert(0,'/tmp/claude-0/verify1011'); from patchlib import *

# ======================= P3 =======================
# P3-1 /game origin removed 2026-09-25, boardgame role 2026-09-29 (two places)
sub(r'an early /game origin and a boardgame role were built and were removed on 25 September 2026, and FrogNet ships no games.',
    r'an early /game origin and a boardgame role were built; the origin was removed on 25 September 2026 and the role on 29 September 2026, and FrogNet ships no games.')
sub(r'origin and a boardgame role were built and were removed on 25 September 2026; FrogNet ships no games.") ]),',
    r'origin and a boardgame role were built; the origin was removed on 25 September 2026 and the role on 29 September 2026. FrogNet ships no games.") ]),')

# P3-2 no text rung
sub(r'an adaptive ladder that steps quality down from full video toward audio and then text as the link degrades,',
    r'an adaptive ladder that steps the picture down, size by size, to audio alone as the link degrades,')

# P3-3 the reconcile is RolePublishReconcile -> publish_all -> advertise()
sub(r'Every role-bearing handler on every node in the island runs its lifecycle reconcile \u2014 "), b("hostReset"), t(" \u2014 and each re-advertises its capability into the new space.',
    r'On every node in the island the reconcile re-publishes every role\u2019s capability: one probe, then each role handler\u2019s "), mono("advertise()"), t(" into the new space.')

# P3-4 region mode: the reconcile writes nothing
sub(r'The proxy publishes once at boot; the discovery merge publishes for its own node on each merge.") ]),',
    r'The proxy publishes once at boot; the discovery merge publishes for its own node on each merge. On an FD&F region the watcher still ticks, but the reconcile writes nothing: capability is the tunnel daemon\u2019s fact in the region, and the elections read it from there.") ]),')

# P3-5 no games
sub(r'Discovery does it. The games do it. The floating database does it.',
    r'Discovery does it. Presence does it. The floating database does it.')

# P3-6 two planes are audio and video, each both ways
sub(r'one carrying our frames out to the mediahost, one carrying the call back',
    r'one carrying audio and one carrying video, each both ways, between us and the media server')

# P3-7 control state is comms-ram
sub(r'P([ t("The first is the FrogNet-hosted Ribbit Region, historically called FrogNet Memory, reached through the same shared-state API as the rest of the application control state.',
    r'P([ t("The first is the Communicator\u2019s shared memory, "), mono("comms-ram"), t(" \u2014 a Ribbit RAM host \u2014 reached through the same shared-state API as the rest of the application\u2019s control state.')
sub(r'P([ t("The control plane is shared state in the FrogNet-hosted Ribbit Region, historically FrogNet Memory. Through it',
    r'P([ t("The control plane is shared state in "), mono("comms-ram"), t(", the Communicator\u2019s own Ribbit memory (port 8800, found through the region\u2019s where cell). Through it')

# P3-8 send-or-drop with retries and the 250 ms finish (section 33, and the same claim in section 31)
sub(r'if it cannot, it drops it and reaches for the next, newer frame. The send never blocks because it is never called when it would.',
    r'if it cannot, an ordinary video frame is dropped and the next, newer one takes its place; audio gets two more tries 8 ms apart and a keyframe three, and a frame already started is finished within 250 ms or closed with padding. Nothing ever waits longer than that.')
sub(r'if the socket is not ready this instant, the frame is dropped, not queued,',
    r'if the socket has no room for the whole frame, the frame is dropped, not queued (audio and keyframes get a few more tries, milliseconds apart),')

# P3-9 held keyframe at most 3 s; KEYREQ
sub(r'holds the newest keyframe until it can be sent, the newer overwriting the older.") ]),',
    r'holds the newest keyframe, for at most three seconds, until it can be sent, the newer overwriting the older; a viewer left without one asks the sender for a fresh keyframe.") ]),')

# P3-10 own=True cleanup is atexit only
sub(r' belongs to the writing process and is removed when that process exits, so a departed writer leaves nothing behind.',
    r' belongs to the writing process and is deleted when that process exits normally; a writer that crashes or is killed leaves its tuple until readers age it out by its timestamp.')

# P3-11 section 61 pointer
sub(r'The rest of that list is where the memory model is going, and section 61 carries it with the other honest edges.',
    r'The rest of that list is where the memory model is going; section 61 records what the later Ribbit work has already shown (held reads, ownership, expiration) and what remains open.')

# P3-12 api path is a default
sub(r'on one path ("), mono("/<vendor>-api"), t("), and only participants',
    r'on one path ("), mono("/<vendor>-api"), t(" by default; the FD&F RAM manager\u2019s is "), mono("/FawcettInnovations.RINA.ram_interface.php"), t("), and only participants')

# P3-13 what else faces the internet on the droplet
sub(r'P([ t("Two things on the droplet face the internet. One is the FrogNet "), b("broker")',
    r'P([ t("Two FrogNet things on the droplet always face the internet. One is the FrogNet "), b("broker")')
sub(r"""and its hub's WireGuard on UDP "), mono("50889"), t(". The broker has two touchpoints""",
    r"""and its hub's WireGuard on UDP "), mono("50889"), t(". When "), mono("fdf-services.sh"), t(" runs there, the services it starts face it too: the Communicator\u2019s "), mono("comms-ram"), t(" on "), mono("8800"), t(" and "), mono("comms-media"), t(" on "), mono("8994"), t(", and the kart and fdf memories on "), mono("8799"), t(" and "), mono("8840"), t(". Apache on 80 and 443 serves the website. The broker has two touchpoints""")

# P3-14 region mode: the hub, not the broker
sub(r'and it opens no tunnel port: it dials the broker. Put a node',
    r"""and it opens no tunnel port: it dials the broker, or on an FD&F region the region's hub. Put a node""")
sub(r'Its tunnel daemon then polls "), mono("my-channels"), t(". "), b("There is no web form."',
    r'Its tunnel daemon then polls "), mono("my-channels"), t(" (on an FD&F region it reads the hub from the region instead, on each region event). "), b("There is no web form."')

# P3-15 18257 is the installer default
sub(r'The port number itself was chosen by the operator and handed to each node in its membership card; it is not a registered service and nothing about it is guessable.',
    r'The port number is set when the broker is installed and handed to each node in its membership card; it is not a registered service; "), mono("18257"), t(" is the installer\u2019s default, and an operator who wants it unguessable passes another "), mono("--port"), t(".')

# P3-16 private keys touch a temp file
sub(r'Its tunnel private keys live only in its kernel interfaces and are never written to disk.',
    r'Its tunnel private keys are kept in its kernel interfaces; each touches a temporary file only for the moment "), mono("wg set"), t(" reads it, and the database stores none.')

# P3-17 the RAM manager on the same host
sub(r'The broker has nothing to surrender because it kept nothing.") ]),',
    r'The broker process has nothing to surrender because it kept nothing. The FD&F RAM manager is another matter: on a host that carries one, root on the host holds the region\u2019s memory, and that is an endpoint compromise in the terms of ORACLE 2.") ]),')

# P3-18 the hub on a region
sub(r"""Between sites, each site's hop to the broker is WireGuard. The broker decrypts, forwards in the kernel and re-encrypts,""",
    r"""Between sites, each site's hop to the broker \u2014 or, on an FD&F region, to the region's hub \u2014 is WireGuard. That machine decrypts, forwards in the kernel and re-encrypts,""")

# P3-19 most tags survive only here
sub(r't(") and the rest are recorded in "), mono("docs/FNAV-SPEC.md"), t(". The rules are kept here',
    r't("), some are recorded in the Communicator\u2019s "), mono("docs/FNAV-SPEC.md"), t(", and the rest survive only here. The rules are kept here')

# ======================= P4 =======================
# P4-1 Python shares the client library
sub(r'It shares nothing with the C++ but the rows in "), mono("comms-ram"),',
    r'It shares with the C++ only the platform\u2019s client library ("), mono("libcomms_tuples"), t(", built by the C++ build), the rows in "), mono("comms-ram"),')

# P4-2 which programs print a build
sub(r'P([ t("Every program says what it is first: "), mono("comms-app"), t(" prints "), mono("[app] build comms-app-2026-10-10-r3"), t(" and carries it in the window title; the phone logs "),',
    r'P([ mono("comms-app"), t(" and "), mono("comms-feed"), t(" print their build first ("), mono("[app] build comms-app-2026-10-10-r3"), t(", "), mono("[feed] build comms-app-2026-10-10-r3"), t("), and the "), mono("comms-app"), t(" window title carries it. "), mono("comms-ram"), t(" logs its version ("), mono("communicator 2.2"), t("). "), mono("comms-media"), t(" prints no build. The phone logs "),')

# P4-3 libraries
sub(r'The libraries are FFmpeg (libavcodec, libswscale, libavdevice), libvpx, libopus, PortAudio and Qt 6.',
    r"""The libraries are FFmpeg (libavcodec, libavformat, libavutil, libswscale, libavdevice), libvpx, libopus, PortAudio and Qt 6, plus LZ4 and OpenSSL's libcrypto for the Ribbit platform.""")

# P4-4 presence is own=true
sub(r'There is no logout and no delete: stale is gone.',
    r'There is no logout. A participant that closes removes its own presence row. One that vanishes stops rewriting it, and the row is stale after 15 seconds.')

# P4-5 idle request rate
sub(r'an idle room makes a handful of requests a minute.',
    r'an idle room makes 8 to 20 requests in 21 seconds (it made 4 reads a second per window when it polled).')

# P4-6 audio is not protected against video
sub(r'Audio is never what is given up while video is still going to that viewer.',
    r'Audio is never shed by choice: the server\u2019s sizing and shedding run only on video planes. An audio block that a viewer\u2019s socket will not take after three tries is dropped, like any other frame.')

# P4-7 segments: deltas follow; audio on its own socket
sub(r'Between the segments go the deltas, which keep the picture current, and the audio, which keeps the conversation alive.',
    r'Between the segments the link drains, and the audio, on its own socket, is serviced in the gaps. The deltas follow the keyframe.')
sub(r'"  interleaved    [kf][a][d][kf][a][d][kf][a][kf] -> display"',
    r'"  segmented      [kf][a][kf][a][kf][a][kf] -> display   then [d][d]..."')

# P4-8 simulator is Part XIII
sub(r'which is what the simulator in the next part is for.',
    r'which is what the simulator in Part XIII is for.')

# P4-9 only desktop senders stamp
sub(r'P([ t("Each sender burns its frame count and its wall clock at capture into the bottom-left of the picture,',
    r'P([ t("Each desktop sender ("), mono("comms-app"), t(", "), mono("comms-feed"), t(") burns its frame count and its wall clock at capture into the bottom-left of the picture (the phone\u2019s sender does not),')

# P4-10 transport tier has loss/latency/jitter on one link
sub(r'The wire has no loss, no latency and no jitter.',
    r'No topology edge carries loss, latency or jitter. The transport tier models latency, jitter, bandwidth and outage over a single link only, and the two are not yet joined.')

# P4-11 monitor size
sub(r'The Monitor is perhaps two thousand lines,',
    r'The Monitor is about two and a half thousand lines,')

done()
