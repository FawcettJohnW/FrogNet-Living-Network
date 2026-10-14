#!/usr/bin/env python3
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
"""frogchat.py -- chat through shared memory.

    python3 frogchat.py --store HOST:PORT --me Dave [--to Bob]

There is no chat server and nobody sends anybody anything. What Dave types is written into each recipient's buffer
in the chat region; Dave's own buffer is held open by a read that wakes when anyone writes into it. Dave's presence is
one cell he refreshes. HOST:PORT is a chat-ram -- on the Internet, or on a machine on your own LAN with no Internet at
all; the program does not know or care which.

    hello                 to whoever you last addressed (or --to)
    /Bob hello            to Bob
    /Bob /Alice hello     to Bob and Alice
    /list                 who is here
    /quit                 leave

Leaving does not remove you from /list at once; your presence stops being refreshed and ages out within a minute.
No fallbacks: an unreachable or refusing memory is reported and the program stops.
"""
import argparse, os, sys, threading, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import chat

WAIT_S = 25.0
HERE_EVERY_S = 20.0     # presence is believed for 60 s (chat::Client::PRESENT_S)

def parse(line, last):
    """(recipients, text) for a typed line; recipients is `last` when the line names nobody."""
    to, words = [], line.split(" ")
    while words and words[0].startswith("/") and len(words[0]) > 1:
        to.append(words.pop(0)[1:])
    return (to or last), " ".join(words).strip()

def main(argv=None):
    ap = argparse.ArgumentParser(description="chat through shared memory")
    ap.add_argument("--store", required=True, help="HOST:PORT of a chat-ram")
    ap.add_argument("--me", required=True)
    ap.add_argument("--to", default="", help="who a line goes to when it names nobody")
    a = ap.parse_args(argv)
    host, port = a.store.rsplit(":", 1)
    try:
        c = chat.Client(host, int(port)); c.here(a.me); after = c.position(a.me)
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
    def presence():
        try:
            while not fatal:
                time.sleep(HERE_EVERY_S); c.here(a.me)
        except chat.ChatError as e:
            fatal.append(e)
    threading.Thread(target=reader, daemon=True).start()
    threading.Thread(target=presence, daemon=True).start()
    last = [a.to] if a.to else []
    print("%s via %s.  /list  /Name text  /quit" % (a.me, a.store), flush=True)
    try:
        for line in sys.stdin:
            if fatal: break
            line = line.strip()
            if not line: continue
            if line == "/quit": break
            if line == "/list":
                print("here: " + ", ".join(c.list()), flush=True); continue
            to, text = parse(line, last)
            if not to:
                print("to whom?  /Name text", flush=True); continue
            last = to
            if text:
                for who in to: c.send(who, a.me, text)
    except (KeyboardInterrupt, chat.ChatError) as e:
        if isinstance(e, chat.ChatError): fatal.append(e)
    # no close: the reader may be inside a held read on this handle; presence ages out in 60 s
    if fatal:
        print("the chat memory failed: %s" % fatal[0], file=sys.stderr); return 1
    return 0

if __name__ == "__main__":
    sys.exit(main())
