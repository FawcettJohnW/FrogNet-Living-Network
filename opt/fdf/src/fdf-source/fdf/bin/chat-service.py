#!/usr/bin/env python3
"""chat-service.py -- [FDF_CHAT_V1] (plan item 7) the chat app's service, in plain Ribbit over this machine's FNWP
connection (the `ram` CLI): it holds a read on the queue chat.inbox (every submitter) after the last cell it handled,
and appends each new message to the room, chat.room.<n>, as {from, text, at}. A held read that comes back empty at
Ribbit's maximum wait is simply held again (Ribbit's own bound; the service has no timer of its own).
Needs RINA_RAM (or RINA_AREA + RINA_MAP_RESOLVER) and FDF_BIN in its environment."""
import json, os, subprocess, sys
RAM = os.path.join(os.environ.get("FDF_BIN", "/opt/fdf/bin"), "ram")
def ram(*a):
    r = subprocess.run([RAM, *a], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if r.returncode != 0: sys.exit(f"chat-service: ram {' '.join(a[:3])} failed: {r.stdout.strip()}")
    return json.loads(r.stdout)
after, n = 0, 0
print("chat-service: holding a read on chat.inbox", flush=True)
while True:
    rows = sorted(ram("read", "chat", "inbox", "", str(after), "25")["rows"], key=lambda c: c["id"])
    for c in rows:
        after = max(after, c["id"]); n += 1
        ram("write", "chat", "room", str(n), json.dumps({"from": c["instance"], "text": c["bag"].get("text", ""), "at": c["bag"].get("at", 0)}))
        print(f"chat-service: #{n} from {c['instance']}: {c['bag'].get('text', '')[:60]}", flush=True)
