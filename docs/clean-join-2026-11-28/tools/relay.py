#!/usr/bin/env python3
# relay.py LISTEN_IP PORT -- sandbox only: lets containers reach the workspace's HTTPS proxy on 127.0.0.1:PORT
import socket, sys, threading
lip, port = sys.argv[1], int(sys.argv[2])
def pump(a, b):
    try:
        while True:
            d = a.recv(65536)
            if not d: break
            b.sendall(d)
    except OSError: pass
    finally:
        for s in (a, b):
            try: s.shutdown(socket.SHUT_RDWR)
            except OSError: pass
            s.close()
srv = socket.socket(); srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); srv.bind((lip, port)); srv.listen(128)
while True:
    c, _ = srv.accept()
    try: u = socket.create_connection(("127.0.0.1", port))
    except OSError: c.close(); continue
    threading.Thread(target=pump, args=(c, u), daemon=True).start()
    threading.Thread(target=pump, args=(u, c), daemon=True).start()
