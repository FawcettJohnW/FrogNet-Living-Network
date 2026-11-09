#!/usr/bin/env python3
"""sockhold.py ADDR:PORT FIFO LOG -- [LISTENING_SOCKET_OUTLIVES_THE_HOST_V1] what a systemd .socket unit does for a RAM
manager, for the netns tests: the listening socket is bound once and kept; each line "start" read from FIFO starts the
RAM manager on it (RIBBIT_LISTEN_FD), with RINA_* from this process's environment and the program in $SOCKHOLD_EXE. A
test's hostdown kills the RAM manager, not this: a client's connect meanwhile waits in the backlog, as on a node."""
import os, socket, subprocess, sys
addr, fifo, logf = sys.argv[1], sys.argv[2], sys.argv[3]
h, p = addr.rsplit(":", 1)
s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.setsockopt(socket.SOL_IP, 15, 1)               # IP_FREEBIND: the address may not be up yet (FreeBind=yes)
s.bind((h, int(p))); s.listen(1024)
exe = os.environ["SOCKHOLD_EXE"]; api = os.environ.get("SOCKHOLD_API", "/FawcettInnovations.RINA.ram_interface.php")
if not os.path.exists(fifo): os.mkfifo(fifo)
while True:
    with open(fifo) as f:
        for line in f:
            if line.strip() == "start":
                subprocess.Popen([exe, "--listen", addr, "--api", api], env=dict(os.environ, RIBBIT_LISTEN_FD=str(s.fileno())),
                                 pass_fds=(s.fileno(),), stdout=open(logf, "a"), stderr=subprocess.STDOUT)
