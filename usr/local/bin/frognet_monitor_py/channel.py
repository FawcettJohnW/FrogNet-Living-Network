################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
################################################################
"""
frognet_monitor/channel.py -- [THE_MONITOR_SHOWS_THE_PROTOCOL_V1] this node's protocol, and its Roles' secrets by a hash.

John, 22 Nov 2026: "Have the monitor show the current protocol and a hash of the secret key, if present."

Both are read on this node, from files, each time a frame is drawn. Nothing is asked of the network and nothing here
is published.

  protocol   What the running daemon and proxy read from /etc/frognet/semantic_transport when they started: the
             node's word, then each memory named with its own word. They write it to
             /run/frognet/semantic_transport.read (core/homa_transport.py). When the file now says something else,
             the line says so: the watcher (frognet-transport-watch.path) is about to restart them, or could not.
  secret     For each Role with a secret on this node (/etc/frognet/channel_secrets/<role>): eight hex digits of
             HMAC-SHA256(secret, "frognet channel secret fingerprint"). The same secret gives the same eight digits
             on every node, so two nodes can be compared by eye. The digits say nothing of the secret or of any key,
             and it is not the computation any key is made by (core/channel_frame.py, which has the same function).
"""
import hashlib
import hmac
import os

TRANSPORT_FILE = os.environ.get("FROGNET_SEM_TRANSPORT_FILE", "/etc/frognet/semantic_transport")
TRANSPORT_READ = os.environ.get("FROGNET_SEM_TRANSPORT_READ", "/run/frognet/semantic_transport.read")
SECRETS = os.environ.get("FROGNET_CHANNEL_SECRETS", "/etc/frognet/channel_secrets")
FINGERPRINT = b"frognet channel secret fingerprint"


def _as_read(text: str) -> str:
    """A transport file's text as a process reads it (core/homa_transport.transport_file_text)."""
    return "\n".join(" ".join(l.split()) for l in text.lower().splitlines() if l.strip())


def _said(text: str) -> str:
    lines = text.splitlines()
    if not lines:
        return "(empty)"
    return lines[0] + "".join("   %s: %s" % tuple(l.split(None, 1)) if len(l.split()) == 2 else "   " + l for l in lines[1:])


def protocol_text() -> str:
    try:
        with open(TRANSPORT_FILE) as f:
            now = _as_read(f.read())
    except FileNotFoundError:
        now = "auto"
    except OSError as e:
        now = None
        why = str(e)
    try:
        with open(TRANSPORT_READ) as f:
            read = f.read().strip()
    except OSError:
        read = None
    if read is None:
        if now is None:
            return "protocol  %s cannot be read (%s)" % (TRANSPORT_FILE, why)
        return "protocol  %s   (the file; the daemon and proxy have not said what they read)" % _said(now)
    if now is not None and now != read:
        return "protocol  %s   (running; the file now says: %s)" % (_said(read), _said(now))
    return "protocol  %s" % _said(read)


STEPPED = b"FROGNET ROLE SECRET AT STEP "    # as in opt/frognet_semantic/core/channel_frame.py


def fingerprint(secret: bytes) -> str:
    return hmac.new(secret, FINGERPRINT, hashlib.sha256).hexdigest()[:8]


def secret_text() -> str:
    try:
        roles = sorted(n for n in os.listdir(SECRETS) if os.path.isfile(os.path.join(SECRETS, n)))
    except FileNotFoundError:
        return "secret    none on this node"
    except OSError as e:
        return "secret    %s cannot be read (%s)" % (SECRETS, e)
    if not roles:
        return "secret    none on this node"
    out = []
    for role in roles:
        if ".next." in role:
            continue                                  # a secret being written (core/channel_frame.keep_secret)
        try:
            with open(os.path.join(SECRETS, role), "rb") as f:
                raw = f.read()
        except OSError:
            out.append("%s (cannot be read)" % role)
            continue
        # A Role's own secret moves forward a step at each key switch (opt/frognet_semantic/core/channel_frame.py,
        # [A_ROLES_OWN_SECRET_MOVES_FORWARD_V1]): its file then begins with STEPPED and the step. Two nodes hold the
        # same secret when they show the same hash AT THE SAME STEP. The network's secret, `default`, never moves.
        step = ""
        if raw.startswith(STEPPED):
            line, _, raw = raw.partition(b"\n")
            step = " step %s" % line[len(STEPPED):].decode("ascii", "replace")
        out.append("%s %s%s" % (role, fingerprint(raw), step))
    return "secret    " + "   ".join(out)


def channel_texts():
    """The two lines, as text."""
    return ["  " + protocol_text(), "  " + secret_text()]
