#!/usr/bin/env python3
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
"""
communicator_app.py -- the FrogNet Communicator (desktop shell, standalone Tk).

THIS is the app. It is not a chat bolted onto the mesh; it is the thing the user
opens to reach their people, and the mesh is what makes it work. The family roster,
games, and calendar are BUNDLES that plug in underneath -- this shell hosts them.

Four parts, each bound to a real surface that already exists on the Host:

  bring-up / identity  -> who am I + which Host (persisted ~/.frognet_communicator)
  presence / roster    -> GET/POST/DELETE http://<host>:8780/registeredUsers
                          (frognet_roster_server.py; swap for the tuple space later)
  launcher / bundles   -> launcher.grouped(LocalFileBeacons(<bundles_root>))
                          each bundle launches as its own standalone app (--connect/--who)
  Call (A/V)           -> frognet_communicator.py client against <host>:9000
                          (the proven A/V engine; audio is the gate, the first codex)

The shell drives the engines; the user never types --stream or a port. Run:

    python3 communicator_app.py
    python3 communicator_app.py --host 10.250.250.1 --name john

stdlib only (tkinter + urllib + subprocess). The A/V Call needs ffmpeg on PATH
for --display; bundles and roster do not.
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
BUNDLES_ROOT = os.environ.get("FROGNET_BUNDLES_ROOT") or os.path.join(HERE, "bundles")  # self-contained: bundles live INSIDE the app dir
ENGINE = os.path.join(HERE, "frognet_communicator.py")    # the A/V Call engine
CONFIG = os.environ.get("FROGNET_CONFIG") or os.path.join(HERE, "shell.json")  # per-instance: each app dir is its own identity

# ROSTER_PORT removed: presence is now a transient tuple, no roster server
AV_PORT = 9000         # frognet_communicator.py --serve
DBHOST = "databasehost_control.frognet"   # SD: presence/call/chat coordination -> control; resolve each poll
SENSOR_TYPE = "installed_family_plugin"
BEACON_FRESH_S = 12 * 60          # drop beacons not re-planted within ~2-3 heartbeats

# palette shared with the family bundle so shell + bundles read as one product
BG = "#12161b"; CARD = "#1b222b"; INK = "#e8edf2"; MUTED = "#8a96a3"
ACCENT = "#3fb68b"; LINE = "#26303a"; ALERT = "#e0533d"; DARK = "#404a55"

# the launcher's grouping is the bundle's real headless core; the SOURCE of beacons
# is the transient DB (the design), not a folder scan.
from launcher import grouped, BeaconSource          # noqa: E402
import frognet_tuples as T                           # noqa: E402  the UnREST tuple substrate
import sotf_ladder as L                              # noqa: E402  ladder + bandwidth cadence

SERVICE = "communicator"

# Call-poll cadence by bearer bandwidth (NOT by send level / user override).
# Richer link -> poll more often. Text-and-below: no poll -- the call tuple is a
# direct transient read by whoever looks. Returns seconds, or None = don't poll.
POLL_CADENCE = {7: 10, 6: 20, 5: 30, 4: 15, 3: 30}   # L2/L1/L0 -> None
DEFAULT_BEARER = 7                                    # assume healthy until measured


def call_poll_secs(bearer_idx: int):
    return POLL_CADENCE.get(bearer_idx)               # None below L3


# ---------------------------------------------------------------------------
# In-call chat -- control-plane text, through the transient (rendezvous, never
# peer-to-peer). Each party writes ONLY its own transcript tuple
# (SD:chat.session:<sid>:<who>), so two writers never clobber one blob; readers
# merge every party's tuple by ts into the full conversation. SAME/DIFF means a
# reader behind gets just the new lines, a cold reader the whole thread -- and the
# merged, ts-ordered blob is also the playback record. Separate from video: a
# blocked send retries here without ever touching the video path.
# ---------------------------------------------------------------------------
def _chat_scope(sid: str, who: str) -> str:
    return f"session:{sid}:{who}"


def chat_send(sid: str, me: str, msg: dict, dbhost: str, retries: int = 4) -> bool:
    """Append MY prebuilt message to my own transcript tuple and re-assert it.
    The caller passes the SAME msg it locally echoed (same ts), so the poll won't
    render a duplicate. Retries on failure (text is lossless) without blocking."""
    scope = _chat_scope(sid, me)
    for _ in range(retries):
        mine = [r["value"] for r in T.get(SERVICE, "chat", dbhost=dbhost)
                if r["scope"] == scope]
        msgs = (mine[0].get("msgs", []) if mine else [])
        if not any(m.get("ts") == msg.get("ts") for m in msgs):
            msgs.append(msg)
        if T.put(SERVICE, "chat", scope, {"msgs": msgs}, dbhost=dbhost):
            return True
        time.sleep(0.4)
    return False


def chat_read(sid: str, dbhost: str) -> list:
    """Merge every party's transcript tuple for this session into one ts-ordered
    conversation."""
    out = []
    for r in T.get(SERVICE, "chat", dbhost=dbhost):
        if r["scope"].startswith(f"session:{sid}:"):
            out.extend(r["value"].get("msgs", []))
    out.sort(key=lambda m: m.get("ts", 0))
    return out


class ApiBeacons(BeaconSource):
    """Discover bundles by associative read on the transient DB (the design).

    Queries api.php sensors/values for SensorType=installed_family_plugin against the
    CURRENT databasehost.frognet (re-resolved every call, so it follows the float),
    and drops beacons whose heartbeat ts is stale -- a service that stopped re-planting
    disappears on its own, no registry teardown.
    """
    def __init__(self, dbhost: str, fresh_s: int = BEACON_FRESH_S, timeout: float = 4.0):
        self.dbhost = dbhost
        self.fresh_s = fresh_s
        self.timeout = timeout

    def beacons(self):
        url = (f"http://{self.dbhost}/api.php?entity=sensors&action=values"
               f"&SensorType={SENSOR_TYPE}&parse=1")
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            rows = json.loads(r.read().decode()).get("rows", [])
        now = int(time.time())
        out = []
        for row in rows:
            data = row.get("data")
            if not isinstance(data, dict):
                raw = row.get("jsonData")
                try:
                    data = json.loads(raw) if isinstance(raw, str) else None
                except Exception:
                    data = None
            if not isinstance(data, dict) or not data.get("app"):
                continue
            ts = int(data.get("ts", 0) or 0)
            if self.fresh_s and ts and (now - ts) > self.fresh_s:
                continue                          # stale heartbeat -- service is gone
            out.append(data)
        return out


# ---------------------------------------------------------------------------
# config (bring-up identity): who am I, which Host. Persisted across launches.
# ---------------------------------------------------------------------------
def load_config() -> dict:
    try:
        with open(CONFIG) as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(cfg: dict) -> None:
    os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
    tmp = CONFIG + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f)
    os.replace(tmp, CONFIG)


# ---------------------------------------------------------------------------
# PRESENCE -- availability as a TRANSIENT TUPLE (replaces the roster server).
# Identity/availability is published into the floating transient DB and must be
# RE-ASSERTED periodically (on the database-changed callback / poll). Nothing here is
# durable: a presence tuple ages out by its ts, so "online" is a continuously refreshed
# fact and "offline" is simply the absence of a fresh refresh -- exactly like beacons and
# capability tuples. No roster server, no port, no DELETE: stale == gone, by freshness.
# Memory, not messages: you write your own presence; everyone reads each other's.
# ---------------------------------------------------------------------------
PRESENCE_FRESH_S = 90        # drop identities not re-asserted within this window (~3 polls)

def _presence_scope(me_id: str) -> str:
    # one tuple per identity (own=False refresh; SensorName must be identity-distinct)
    return T.role_scope(f"presence:{me_id}")

def presence_register(me_id: str, name: str, status: str = "online",
                      dbhost: str = DBHOST) -> bool:
    """Write/re-assert MY presence tuple into the transient DB. Call once at startup and
    again on every database-changed callback / poll tick to stay 'online'. own=False so the
    tuple outlives this write and ages out on its own when refreshes stop."""
    blob = {"id": me_id, "name": name, "status": status, "ts": int(time.time())}
    try:
        return T.put(SERVICE, "presence", _presence_scope(me_id), blob,
                     dbhost=dbhost, own=False)
    except Exception:
        return False

def presence_list(dbhost: str = DBHOST, fresh_s: int = PRESENCE_FRESH_S) -> list:
    """Everyone currently online: read all presence tuples, freshness-filtered so identities
    that stopped re-asserting are dropped automatically (fresh_s ages them out server-side).
    Returns [{id,name,status,ts}], de-duped by id (latest ts wins), name-sorted."""
    by_id = {}
    try:
        for r in T.get(SERVICE, "presence", dbhost=dbhost, fresh_s=fresh_s):
            v = r.get("value") or {}
            pid = v.get("id")
            if not pid or v.get("status") == "offline":
                continue
            if pid not in by_id or int(v.get("ts", 0)) >= int(by_id[pid].get("ts", 0)):
                by_id[pid] = v
    except Exception:
        return []
    return sorted(by_id.values(), key=lambda u: (u.get("name") or u.get("id") or "").lower())

def presence_offline(me_id: str, name: str = "", dbhost: str = DBHOST) -> None:
    """Optional fast disappear: assert status=offline once so peers drop me promptly instead
    of waiting for the freshness window. (Not required -- ceasing to refresh also works.)"""
    try:
        T.put(SERVICE, "presence", _presence_scope(me_id),
              {"id": me_id, "name": name, "status": "offline", "ts": int(time.time())},
              dbhost=dbhost, own=False)
    except Exception:
        pass

# --- back-compat shims: the shell calls roster_register/list/deregister(host, ...). Host is
# now irrelevant (presence lives in the floating DB, resolved via DBHOST); keep the names so
# communicator.py needs no change, drop the host arg's meaning. ---
def roster_register(host, me_id: str, name: str, status: str = "online", timeout: float = 4.0):
    presence_register(me_id, name, status); return {"ok": True}

def roster_list(host=None, timeout: float = 4.0) -> list:
    return presence_list()

def roster_deregister(host=None, me_id: str = "", timeout: float = 3.0) -> None:
    if me_id:
        presence_offline(me_id)

def call_commands(host: str, me_id: str, peer_id: str, display: bool = True) -> list:
    avhost = host.split(":")[0]
    sender = [sys.executable, ENGINE, "--connect", f"{avhost}:{AV_PORT}",
              "--role", "sender", "--stream", me_id]
    viewer = [sys.executable, ENGINE, "--connect", f"{avhost}:{AV_PORT}",
              "--role", "viewer", "--stream", peer_id]
    viewer += ["--display"] if display else ["--save", f"call_{peer_id}.ivf"]
    return [sender, viewer]


def _local_bundle_index() -> dict:
    """Scan installed bundles under BUNDLES_ROOT and index each by the IDENTIFIERS it
    declares in its own bundle.json (id, name) AND its folder name. The map value is
    (folder_path, app_relpath). This is the local truth -- we match a network beacon to a
    local bundle by its stable id, not by guessing the folder name from beacon keys."""
    idx = {}
    try:
        for folder in os.listdir(BUNDLES_ROOT):
            bdir = os.path.join(BUNDLES_ROOT, folder)
            if not os.path.isdir(bdir):
                continue
            app = None
            meta = os.path.join(bdir, "bundle.json")
            ids = {folder}
            if os.path.isfile(meta):
                try:
                    d = json.load(open(meta))
                    app = d.get("app") or app
                    for k in ("id", "name", "module"):
                        if d.get(k):
                            ids.add(d[k])
                except Exception:
                    pass
            for key in ids:
                idx[key] = (bdir, app)
    except Exception:
        pass
    return idx


def bundle_local_app(beacon: dict) -> str | None:
    """A discovered beacon names the bundle; to LAUNCH it the bundle must be installed
    locally. Match the beacon to an installed bundle by any identifier it shares with the
    local bundle.json (id/name/module) or the folder name; use the beacon's app if given,
    else the LOCAL bundle.json's app. Returns the absolute app path, or None if not present.
    Robust to the beacon's id/module not equalling the on-disk folder name."""
    idx = _local_bundle_index()
    for key in (beacon.get("id"), beacon.get("name"), beacon.get("module"),
                beacon.get("dir")):
        if key and key in idx:
            bdir, local_app = idx[key]
            app = beacon.get("app") or local_app
            if not app:
                return None
            path = os.path.join(bdir, app)
            return path if os.path.isfile(path) else None
    # last resort: the old direct-join behavior (beacon carries dir+app matching the folder)
    app = beacon.get("app")
    if app:
        for key in (beacon.get("dir"), beacon.get("id"), beacon.get("module")):
            if key:
                path = os.path.join(BUNDLES_ROOT, key, app)
                if os.path.isfile(path):
                    return path
    return None


def bundle_command(beacon: dict, host: str, me_name: str) -> list | None:
    path = bundle_local_app(beacon)
    if not path:
        return None                                # discovered but not installed here
    avhost = host.split(":")[0]                     # bundles speak to the fabric (Apache)
    return [sys.executable, path, "--connect", avhost, "--who", me_name]


# ===========================================================================
# Tk shell
# ===========================================================================
def run_shell(cfg: dict):
    import tkinter as tk

    me_id = cfg["id"]; me_name = cfg["name"]; host = cfg["host"]
    dbhost = cfg.get("dbhost", DBHOST)             # transient role; discovery follows the float
    procs: list = []                               # child apps/calls we spawned
    calls: dict = {}                               # peer_id -> [procs] for Hang Up (live media)
    sessions: dict = {}                            # peer_id -> session_id for active/ringing calls
    seen_calls: dict = {}                          # session_id -> last state we acted on
    chat_sids: set = set()                         # session_ids with an open chat window (polled)
    chat_wins: dict = {}                           # session_id -> (Toplevel, Text widget)
    chat_shown: dict = {}                          # session_id -> set of (from,ts) already rendered
    bearer = [DEFAULT_BEARER]                      # current perceived bandwidth (drives poll cadence)

    roster_q: "queue.Queue" = queue.Queue()
    stop = threading.Event()

    # [HOSTRESET_WIRE_V1] Watch for a databasehost(_control) float and reconcile this
    # process's modules on a delta. The shell's reconcilable is presence: on a float it
    # re-registers to the new host's roster (re-asserts its presence) and re-renders the
    # roster (the living-network refresh). Bundle apps tick their own watcher over their
    # codex. Detection is the resolved-IP delta -- memory, not messages.
    import socket as _sock
    from frognet_host_reset import HostResetWatcher

    class _PresenceReconcile:
        def hostReset(self):
            try:
                roster_register(host, me_id, me_name)      # re-assert presence on the new host
            except Exception:
                pass
            return {"module": "presence", "reset": [], "written": ["roster"]}

    def _resolve_host_ip():
        # [HOSTS_ONLY_V1] /etc/hosts, never the resolver. This detects a databasehost
        # FLOAT, and a resolver answer that differs from the file makes the detector
        # fire on a change nobody made -- or miss one that happened.
        try:
            from core.hosts_only import try_resolve
            return try_resolve(host)
        except Exception:
            return None

    # [PERF_PUBLISH_WIRE_V1] This node's capability/perf publisher participates in the
    # reconcile. On a databasehost(_control) float it RE-PUBLISHES <role>/capability to
    # BOTH the control DB (authoritative election input) and the data DB (mirror), so the
    # new host carries this node's current capability for the next election round.
    from frognet_perf_publisher import make_publisher
    perf_pub = make_publisher(logger=lambda s: None)

    host_watch = HostResetWatcher(
        resolve_ip=_resolve_host_ip,
        get_modules=lambda: [_PresenceReconcile(), perf_pub],
        read_shared_vector=lambda: roster_list(host),
        render_ui=lambda vec: roster_q.put(("users", vec)),    # re-render the living network
        logger=lambda s: None)

    def _new_session(peer):
        return f"{me_id}-{peer}-{int(time.time())}"

    def poll_loop():
        # register once, then poll roster + bundle discovery + incoming calls.
        try:
            roster_register(host, me_id, me_name)
        except Exception as e:
            roster_q.put(("error", str(e)))
        try:
            perf_pub.publish()      # [PERF_PUBLISH_BOOT_V1] dual-write capability at boot
        except Exception:           #   -> seeds the very first election on this node
            pass
        last_call_poll = 0.0
        last_chat_poll = 0.0
        while not stop.is_set():
            try:
                host_watch.tick()                          # float? -> reconcile this process
            except Exception:
                pass
            try:
                roster_q.put(("users", roster_list(host)))
            except Exception as e:
                roster_q.put(("error", str(e)))
            try:
                roster_q.put(("bundles", grouped(ApiBeacons(dbhost))))
            except Exception as e:
                roster_q.put(("bundles_err", str(e)))
            # --- refresh perceived bandwidth (drives the cadence). Reads an
            # optional SD:bearer.<host> tuple if something publishes one; absent
            # that, stays at DEFAULT_BEARER. No fabricated estimate. ---
            try:
                br = T.get(SERVICE, "bearer", dbhost=dbhost, fresh_s=120)
                if br:
                    lv = int(br[0]["value"].get("level", DEFAULT_BEARER))
                    bearer[0] = max(L.MIN_IDX, min(L.MAX_IDX, lv))
            except Exception:
                pass
            # --- call signaling: read call tuples at the BANDWIDTH cadence ---
            cad = call_poll_secs(bearer[0])
            now = time.monotonic()
            if cad is not None and (now - last_call_poll) >= cad:
                last_call_poll = now
                try:
                    rows = T.get(SERVICE, "call", dbhost=dbhost, fresh_s=180)
                    roster_q.put(("calls", rows))
                except Exception as e:
                    roster_q.put(("calls_err", str(e)))
            # in-call chat polls FASTER than the call cadence -- a cheap SAME/DIFF
            # read, but text wants to feel live, not lag the 10-30s call cadence.
            if chat_sids and (now - last_chat_poll) >= 2.0:
                last_chat_poll = now
                for sid in list(chat_sids):
                    try:
                        roster_q.put(("chat", (sid, chat_read(sid, dbhost))))
                    except Exception:
                        pass
            stop.wait(2.0)

    root = tk.Tk()
    root.title("FrogNet Communicator")
    root.configure(bg=BG)
    root.geometry("520x720")

    head = tk.Frame(root, bg=BG); head.pack(fill="x", padx=20, pady=(16, 6))
    tk.Label(head, text="FrogNet Communicator", fg=INK, bg=BG,
             font=("Georgia", 19, "bold")).pack(side="left")
    who = tk.Label(head, text=f"{me_name} \u00b7 {host}", fg=MUTED, bg=BG,
                   font=("Helvetica", 10))
    who.pack(side="right")

    status = tk.Label(root, text="connecting\u2026", fg=MUTED, bg=BG,
                      font=("Helvetica", 10), anchor="w")
    status.pack(fill="x", padx=22)

    body = tk.Frame(root, bg=BG); body.pack(fill="both", expand=True, padx=18, pady=8)

    def section(parent, title):
        tk.Label(parent, text=title, fg=MUTED, bg=BG,
                 font=("Helvetica", 10, "bold"), anchor="w").pack(fill="x", pady=(10, 4))

    # -- people (presence) ---------------------------------------------------
    people_box = tk.Frame(body, bg=BG); people_box.pack(fill="x")
    # -- bundles (discovered on the transient DB) ----------------------------
    bundles_box = tk.Frame(body, bg=BG); bundles_box.pack(fill="x")

    def render_people(users):
        for w in people_box.winfo_children():
            w.destroy()
        section(people_box, "People")
        others = [u for u in users if u.get("id") != me_id]
        if not others:
            tk.Label(people_box, text="no one else here yet", fg=MUTED, bg=BG,
                     font=("Helvetica", 10), anchor="w").pack(fill="x", padx=2, pady=4)
            return
        for u in others:
            online = u.get("status", "online") == "online"
            pid = u.get("id", u.get("name", "")).strip().lower()
            in_call = pid in calls
            card = tk.Frame(people_box, bg=CARD); card.pack(fill="x", pady=3)
            tk.Label(card, text="\u25cf", fg=(ACCENT if online else DARK), bg=CARD,
                     font=("Helvetica", 14)).pack(side="left", padx=(12, 6), pady=10)
            col = tk.Frame(card, bg=CARD); col.pack(side="left", fill="x", expand=True)
            tk.Label(col, text=u.get("name", u.get("id", "?")), fg=INK, bg=CARD,
                     font=("Helvetica", 13, "bold"), anchor="w").pack(fill="x")
            tk.Label(col, text=("in call" if in_call else
                                ("online" if online else u.get("status", "offline"))),
                     fg=(ACCENT if in_call else MUTED), bg=CARD,
                     font=("Helvetica", 9), anchor="w").pack(fill="x")
            if in_call:
                tk.Button(card, text="Hang Up", command=lambda uu=u: hang_up(uu),
                          font=("Helvetica", 10, "bold"), fg="#fff", bg=ALERT,
                          bd=0, padx=12, pady=6, cursor="hand2").pack(side="right",
                                                                      padx=(4, 12))
            else:
                tk.Button(card, text="Call", command=lambda uu=u: start_call(uu),
                          font=("Helvetica", 10, "bold"), fg="#06231a", bg=ACCENT,
                          bd=0, padx=16, pady=6, cursor="hand2").pack(side="right",
                                                                      padx=(4, 12))

    # -- bundles (rendered from transient-DB discovery) ----------------------
    def render_bundles(by_hub, err=None):
        for w in bundles_box.winfo_children():
            w.destroy()
        section(bundles_box, "Apps")
        if err:
            tk.Label(bundles_box, text=f"discovery: {err}", fg=ALERT, bg=BG,
                     font=("Helvetica", 9), anchor="w").pack(fill="x", padx=2)
            return
        any_shown = False
        for hub, items in (by_hub or {}).items():
            for b in items:
                any_shown = True
                installed = bundle_local_app(b) is not None
                card = tk.Frame(bundles_box, bg=CARD); card.pack(fill="x", pady=3)
                col = tk.Frame(card, bg=CARD); col.pack(side="left", fill="x",
                                                        expand=True, padx=12, pady=8)
                tk.Label(col, text=b.get("title", b.get("module", "?")), fg=INK, bg=CARD,
                         font=("Helvetica", 12, "bold"), anchor="w").pack(fill="x")
                tk.Label(col, text=hub + ("" if installed else "  \u00b7 not installed here"),
                         fg=MUTED, bg=CARD, font=("Helvetica", 9), anchor="w").pack(fill="x")
                if installed:
                    tk.Button(card, text="Open", command=lambda bb=b: open_bundle(bb),
                              font=("Helvetica", 10, "bold"), fg=INK, bg=LINE,
                              bd=0, padx=16, pady=6, cursor="hand2").pack(side="right",
                                                                          padx=(4, 12))
                else:
                    tk.Label(card, text="\u2014", fg=DARK, bg=CARD,
                             font=("Helvetica", 12)).pack(side="right", padx=(4, 16))
        if not any_shown:
            tk.Label(bundles_box, text="no bundles announced on the network yet",
                     fg=MUTED, bg=BG, font=("Helvetica", 10), anchor="w").pack(
                         fill="x", padx=2)

    def _spawn(cmd):
        return subprocess.Popen(cmd)

    def _kill(p):
        """Kill the engine subprocess AND its ffmpeg/ffplay children. On Windows,
        terminating the Python parent leaves ffmpeg holding the camera -- so kill
        the whole tree by PID. That's the stuck-camera fix."""
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)],
                               capture_output=True)
            else:
                p.terminate()
        except Exception:
            try: p.terminate()
            except Exception: pass

    def spawn_media(peer, session_id):
        """Two-way call: publish me, self-preview, watch them. Camera opens HERE --
        only on accept, never before."""
        if peer in calls:
            return
        avhost = host.split(":")[0]
        cam = cfg.get("camera"); cfps = cfg.get("fps", 30)
        cbr = cfg.get("bitrate_kbps", 800); cov = cfg.get("overlay")
        cmic = cfg.get("mic")
        # Streams are SESSION-scoped, not user-scoped: each call leg is its own hub
        # on the server. me_id alone collides when the same person is in two calls,
        # or when test identities reuse names across machines -- that's the "everyone
        # sees one camera" bug. <sid>:<publisher> is globally unique per call leg.
        my_stream = f"{session_id}:{me_id}"
        peer_stream = f"{session_id}:{peer}"
        sender = [sys.executable, ENGINE, "--connect", f"{avhost}:{AV_PORT}",
                  "--role", "sender", "--stream", my_stream, "--source", "live",
                  "--fps", str(cfps), "--bitrate-kbps", str(cbr)]
        if cam:
            sender += ["--camera", cam]
        if cmic:
            sender += ["--mic", cmic]
        self_prev = [sys.executable, ENGINE, "--connect", f"{avhost}:{AV_PORT}",
                     "--role", "viewer", "--stream", my_stream, "--display",
                     "--fps", str(cfps)]
        peer_view = [sys.executable, ENGINE, "--connect", f"{avhost}:{AV_PORT}",
                     "--role", "viewer", "--stream", peer_stream, "--display",
                     "--fps", str(cfps)]
        if cov:                                    # sensor overlay on the other person
            peer_view += ["--overlay", cov, "--overlay-dbhost", dbhost]
        cmds = [sender, self_prev, peer_view]
        try:
            calls[peer] = [_spawn(c) for c in cmds]
            procs.extend(calls[peer])
            sessions[peer] = session_id
            status.config(text=f"in call with {peer}", fg=ACCENT)
            open_chat(session_id, peer)            # text channel up with the call
        except Exception as e:
            status.config(text=f"media failed: {e}", fg=ALERT)
        render_people(last_users[0])

    def open_chat(sid, peer):
        if sid in chat_wins:
            return
        win = tk.Toplevel(root); win.title(f"Chat \u2014 {peer}"); win.configure(bg=BG)
        win.geometry("440x560"); win.minsize(340, 360)
        # Input bar FIRST, packed to the bottom with a fixed height, so the
        # expanding transcript above can never squeeze it to zero (Tk will shrink
        # whatever is packed last/expand=True, not this).
        bar = tk.Frame(win, bg=BG, height=52)
        bar.pack(side="bottom", fill="x", padx=10, pady=10)
        bar.pack_propagate(False)
        entry = tk.Entry(bar, bg="#ffffff", fg="#111", bd=0, font=("Helvetica", 12),
                         insertbackground="#111")
        entry.pack(side="left", fill="both", expand=True, padx=(0, 6))
        txt = tk.Text(win, bg=CARD, fg=INK, bd=0, font=("Helvetica", 11),
                      wrap="word", state="disabled", padx=10, pady=8)
        txt.pack(side="top", fill="both", expand=True, padx=10, pady=(10, 0))

        def _send(_evt=None):
            t = entry.get().strip()
            if not t:
                return
            entry.delete(0, "end")
            msg = {"from": me_id, "text": t, "ts": int(time.time() * 1000)}
            _append_chat(sid, [msg])                               # local echo now
            threading.Thread(target=chat_send, args=(sid, me_id, msg, dbhost),
                             daemon=True).start()                  # persists SAME msg (no dup)

        entry.bind("<Return>", _send)
        entry.focus_set()
        tk.Button(bar, text="Send", command=_send, font=("Helvetica", 10, "bold"),
                  fg="#06231a", bg=ACCENT, bd=0, padx=14,
                  cursor="hand2").pack(side="right", fill="y")
        chat_wins[sid] = (win, txt)
        chat_shown[sid] = set()
        chat_sids.add(sid)
        win.protocol("WM_DELETE_WINDOW", lambda: close_chat(sid))
        win.update_idletasks(); win.lift()         # force layout so the input shows now

    def _append_chat(sid, msgs):
        """Render new messages into the transcript, de-duped by (from,ts)."""
        w = chat_wins.get(sid)
        if not w:
            return
        _, txt = w
        shown = chat_shown.setdefault(sid, set())
        txt.config(state="normal")
        for m in msgs:
            key = (m.get("from"), m.get("ts"))
            if key in shown:
                continue
            shown.add(key)
            who = "you" if m.get("from") == me_id else m.get("from", "?")
            txt.insert("end", f"{who}: {m.get('text','')}\n")
        txt.config(state="disabled"); txt.see("end")

    def close_chat(sid):
        chat_sids.discard(sid)
        w = chat_wins.pop(sid, None)
        chat_shown.pop(sid, None)
        if w:
            try: w[0].destroy()
            except Exception: pass

    def start_call(u):
        """Press Call -> write a ringing tuple. NO camera, NO media yet. Media is
        born only when the callee writes state=accepted (memory, not messages)."""
        peer = u.get("id", u.get("name", "")).strip().lower()
        if peer in calls or peer in sessions:
            return
        sid = _new_session(peer)
        sessions[peer] = sid
        T.put(SERVICE, "call", T.session_scope(sid),
              {"from": me_id, "to": peer, "state": "ringing", "name": me_name},
              dbhost=dbhost)
        seen_calls[sid] = "ringing"
        status.config(text=f"calling {u.get('name', peer)}\u2026 (ringing)", fg=ACCENT)
        render_people(last_users[0])

    def accept_call(sid, frm):
        """Callee accepts: write state=accepted to the SAME tuple, then both sides
        (seeing the converged state) spawn media."""
        T.put(SERVICE, "call", T.session_scope(sid),
              {"from": frm, "to": me_id, "state": "accepted", "name": me_name},
              dbhost=dbhost)
        seen_calls[sid] = "accepted"
        spawn_media(frm, sid)

    def decline_call(sid, frm):
        T.put(SERVICE, "call", T.session_scope(sid),
              {"from": frm, "to": me_id, "state": "declined"}, dbhost=dbhost)
        seen_calls[sid] = "declined"
        status.config(text=f"declined {frm}", fg=MUTED)

    def hang_up(u):
        peer = u.get("id", u.get("name", "")).strip().lower()
        for p in calls.pop(peer, []):
            _kill(p)
        sid = sessions.pop(peer, None)
        if sid:                                    # mark the call ended in shared memory
            T.put(SERVICE, "call", T.session_scope(sid),
                  {"from": me_id, "to": peer, "state": "ended"}, dbhost=dbhost)
            seen_calls[sid] = "ended"
            close_chat(sid)
        status.config(text=f"hung up {peer}", fg=MUTED)
        render_people(last_users[0])

    def open_bundle(b):
        cmd = bundle_command(b, host, me_name)
        if not cmd:
            status.config(text=f"{b.get('title','app')} not installed here", fg=MUTED)
            return
        try:
            procs.append(_spawn(cmd))
            status.config(text=f"opened {b.get('title','app')}", fg=ACCENT)
        except Exception as e:
            status.config(text=f"open failed: {e}", fg=ALERT)

    def _ring_dialog(sid, frm, frm_name):
        win = tk.Toplevel(root); win.title("Incoming call"); win.configure(bg=BG)
        win.geometry("320x160")
        tk.Label(win, text=f"{frm_name or frm}", fg=INK, bg=BG,
                 font=("Georgia", 16, "bold")).pack(pady=(24, 2))
        tk.Label(win, text="is calling\u2026", fg=MUTED, bg=BG,
                 font=("Helvetica", 11)).pack()
        row = tk.Frame(win, bg=BG); row.pack(pady=20)

        def _accept():
            win.destroy(); accept_call(sid, frm)

        def _decline():
            win.destroy(); decline_call(sid, frm)

        tk.Button(row, text="Decline", command=_decline, font=("Helvetica", 11, "bold"),
                  fg="#fff", bg=ALERT, bd=0, padx=18, pady=8,
                  cursor="hand2").pack(side="left", padx=8)
        tk.Button(row, text="Accept", command=_accept, font=("Helvetica", 11, "bold"),
                  fg="#06231a", bg=ACCENT, bd=0, padx=18, pady=8,
                  cursor="hand2").pack(side="left", padx=8)
        win.protocol("WM_DELETE_WINDOW", _decline)

    def handle_calls(rows):
        """React to converged call tuples. Memory, not messages: we read the current
        state and act on transitions we haven't acted on yet."""
        for r in rows:
            v = r.get("value", {})
            sid = r.get("scope", "").replace("session:", "")
            frm = v.get("to") and v.get("from")
            to = v.get("to"); state = v.get("state")
            if not sid or not state:
                continue
            if seen_calls.get(sid) == state:
                continue                            # already acted on this state
            # Incoming ring addressed to me -> show Accept/Decline
            if to == me_id and state == "ringing" and v.get("from") != me_id:
                seen_calls[sid] = "ringing"
                _ring_dialog(sid, v.get("from"), v.get("name"))
            # My outgoing call was accepted by the callee -> spawn my media now
            elif v.get("from") == me_id and state == "accepted":
                seen_calls[sid] = "accepted"
                spawn_media(to, sid)
            elif state in ("declined", "ended"):
                seen_calls[sid] = state
                peer = to if v.get("from") == me_id else v.get("from")
                if peer in calls:                   # tear down my media if up
                    for p in calls.pop(peer, []):
                        _kill(p)
                sessions.pop(peer, None)
                close_chat(sid)
                if state == "declined" and v.get("from") == me_id:
                    status.config(text=f"{peer} declined", fg=MUTED)

    last_users = [[]]                              # remember roster for re-render on call state
    render_people([])
    render_bundles({})

    # -- pump the queue into the UI on the Tk thread -------------------------
    def drain():
        try:
            while True:
                kind, payload = roster_q.get_nowait()
                if kind == "users":
                    status.config(text="connected", fg=ACCENT)
                    last_users[0] = payload
                    render_people(payload)
                elif kind == "bundles":
                    render_bundles(payload)
                elif kind == "bundles_err":
                    render_bundles({}, err=payload)
                elif kind == "calls":
                    handle_calls(payload)
                elif kind == "chat":
                    sid, msgs = payload
                    _append_chat(sid, msgs)
                elif kind == "calls_err":
                    pass                            # transient unreachable; try next cycle
                else:
                    status.config(text=f"roster: {payload}", fg=ALERT)
        except queue.Empty:
            pass
        root.after(500, drain)

    def on_close():
        stop.set()
        roster_deregister(host, me_id)
        for p in procs:
            _kill(p)
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    threading.Thread(target=poll_loop, daemon=True).start()
    root.after(500, drain)
    root.mainloop()


# ---------------------------------------------------------------------------
# first-run identity prompt (only if config/args don't supply name+host)
# ---------------------------------------------------------------------------

def _derive_name() -> str:
    """This machine's identity, no prompt. Prefer the FrogNet hostname, then the OS
    login name, then a stable fallback. Lowercased id is derived from it by the shell."""
    import socket, getpass
    for fn in (lambda: socket.gethostname().split(".")[0],
               lambda: getpass.getuser()):
        try:
            v = (fn() or "").strip()
            if v and v.lower() not in ("localhost", "frognethost"):
                return v
        except Exception:
            pass
    # last resort: the node's 10/8 octet, so two boxes still differ
    try:
        return "node-" + T.my_ip().split(".")[-1]
    except Exception:
        return "frognet-user"


def _derive_host() -> str:
    """The FrogNet Host this client talks to, BY CONVENTION -- never asked. It is the .1 of
    the local FrogNet /24 (the served-subnet gateway that runs roster/A/V/control). Derived
    from this node's own 10/8 address. The shared-DB plane is resolved separately by NAME
    (DBHOST = databasehost_control.frognet); this .1 is only the A/V / bundle-launch host."""
    try:
        ip = T.my_ip()                       # e.g. 10.250.250.37  -> host is 10.250.250.1
        if ip and ip.startswith("10."):
            a, b, c, _ = ip.split(".")
            return f"{a}.{b}.{c}.1"
    except Exception:
        pass
    return "databasehost.frognet"            # resolvable-by-name fallback


def prompt_name(default_name: str = "") -> str:
    """Ask ONLY for a display name (host is always derived, never asked). Pre-filled with
    the derived machine name so the user can just hit Start. Returns the chosen name, or the
    default if they cancel/clear it -- we never block entry into the session on identity."""
    try:
        import tkinter as tk
    except Exception:
        return default_name
    out = {"name": default_name}
    win = tk.Tk(); win.title("FrogNet Communicator"); win.configure(bg=BG)
    win.geometry("380x180")
    tk.Label(win, text="What should we call you?", fg=INK, bg=BG,
             font=("Georgia", 15, "bold")).pack(pady=(24, 4))
    tk.Label(win, text="Your display name on the pond.", fg=MUTED, bg=BG,
             font=("Helvetica", 10)).pack(pady=(0, 12))
    row = tk.Frame(win, bg=BG); row.pack(fill="x", padx=32)
    e = tk.Entry(row, bg=CARD, fg=INK, insertbackground=INK, bd=0, font=("Helvetica", 12))
    e.insert(0, default_name); e.pack(fill="x", ipady=6, ipadx=6); e.focus_set()
    e.select_range(0, "end")
    def go():
        v = e.get().strip()
        out["name"] = v or default_name
        win.destroy()
    e.bind("<Return>", lambda _e: go())
    tk.Button(win, text="Start", command=go, font=("Helvetica", 11, "bold"),
              fg="#06231a", bg=ACCENT, bd=0, padx=22, pady=7, cursor="hand2").pack(pady=18)
    win.mainloop()
    return out["name"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="communicator_app")
    ap.add_argument("--host", help="FrogNet Host (roster :8780 / A/V :9000)")
    ap.add_argument("--dbhost", help="transient DB host for discovery (default databasehost.frognet)")
    ap.add_argument("--name", help="your display name")
    ap.add_argument("--camera", help="capture device for calls, e.g. 'video=HD Pro Webcam C920'")
    ap.add_argument("--mic", help="microphone for calls, e.g. 'audio=Microphone (HD Pro Webcam C920)' or hw:1,0")
    ap.add_argument("--fps", type=int, help="call frame rate (default 30)")
    ap.add_argument("--bitrate-kbps", type=int, help="call video bitrate (default 800)")
    ap.add_argument("--overlay", help="sensor overlay for the call view (max 5), "
                                      "e.g. 'GPS:GPS-1,DHT:Temp-3'")
    a = ap.parse_args(argv)

    cfg = load_config()
    if a.host:
        cfg["host"] = a.host
    if a.dbhost:
        cfg["dbhost"] = a.dbhost
    if a.name:
        cfg["name"] = a.name; cfg["id"] = a.name.lower()

    if not cfg.get("name") or not cfg.get("host"):
        cfg = prompt_identity(cfg)
        if not cfg.get("name") or not cfg.get("host"):
            print("setup cancelled"); return 1
    cfg.setdefault("id", cfg["name"].lower())
    save_config(cfg)                               # persist IDENTITY only

    # Call settings are RUNTIME-ONLY -- never persisted, so two identities on one
    # box don't inherit each other's camera. What you pass is what you get; absent
    # a flag, no camera is sent and the engine picks its default.
    for k in ("camera", "mic", "fps", "bitrate_kbps", "overlay"):
        cfg.pop(k, None)
    if a.camera:
        cfg["camera"] = a.camera
    if a.mic:
        cfg["mic"] = a.mic
    if a.fps:
        cfg["fps"] = a.fps
    if a.bitrate_kbps:
        cfg["bitrate_kbps"] = a.bitrate_kbps
    if a.overlay:
        cfg["overlay"] = a.overlay

    run_shell(cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
