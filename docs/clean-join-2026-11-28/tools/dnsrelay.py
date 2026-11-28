#!/usr/bin/env python3
# dnsrelay.py LISTEN_IP -- sandbox only: a DNS server at the uplink network's gateway address (a real WAN has one),
# relaying each query to the workspace's resolver.
import socket, sys, threading
srv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); srv.bind((sys.argv[1], 53))
def one(q, addr):
    u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); u.settimeout(3)
    try:
        u.sendto(q, ("8.8.8.8", 53)); srv.sendto(u.recvfrom(4096)[0], addr)
    except OSError: pass
    finally: u.close()
while True:
    q, addr = srv.recvfrom(4096)
    threading.Thread(target=one, args=(q, addr), daemon=True).start()
