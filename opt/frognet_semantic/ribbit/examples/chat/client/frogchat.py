#!/usr/bin/env python3
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
"""frogchat.py -- person-to-person chat through shared memory.

    python3 frogchat.py --store HOST:PORT --me Dave --to Bob

There is no chat server and nobody sends anybody anything. What Dave types is written into Bob's buffer in the chat
region (ChatServer.Bob.Dave); Dave's own buffer (ChatServer.Dave.*) is held open by a read that wakes when anyone
writes into it. Two threads: one reads the terminal and writes, one waits on the buffer and prints. HOST:PORT is a
chat-ram. No fallbacks: an unreachable or refusing memory is reported and the program stops.
"""
import argparse, os, sys, threading, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import chat

WAIT_S = 25.0

def main(argv=None):
    ap = argparse.ArgumentParser(description="person-to-person chat through shared memory")
    ap.add_argument("--store", required=True, help="HOST:PORT of a chat-ram")
    ap.add_argument("--me", required=True); ap.add_argument("--to", required=True)
    a = ap.parse_args(argv)
    host, port = a.store.rsplit(":", 1)
    try:
        c = chat.Client(host, int(port)); after = c.position(a.me)
    except chat.ChatError as e:
        print("cannot use the chat memory at %s: %s" % (a.store, e), file=sys.stderr); return 2
    fatal = []
    def reader():
        nonlocal after
        try:
            while True:
                msgs, after = c.receive(a.me, after, WAIT_S)
                for m in msgs:
                    print("[%s %s] %s" % (m["from"], time.strftime("%H:%M:%S", time.localtime(m["ts"])), m["text"]), flush=True)
        except chat.ChatError as e:
            fatal.append(e)
    threading.Thread(target=reader, daemon=True).start()
    print("%s -> %s via %s.  /quit to leave" % (a.me, a.to, a.store), flush=True)
    try:
        for line in sys.stdin:
            if fatal: break
            line = line.strip()
            if line == "/quit": break
            if line: c.send(a.to, a.me, line)
    except (KeyboardInterrupt, chat.ChatError) as e:
        if isinstance(e, chat.ChatError): fatal.append(e)
    if fatal:
        print("the chat memory failed: %s" % fatal[0], file=sys.stderr); return 1
    return 0

if __name__ == "__main__":
    sys.exit(main())
