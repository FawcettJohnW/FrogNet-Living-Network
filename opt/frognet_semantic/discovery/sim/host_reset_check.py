"""
sim/host_reset_check.py - proves hostReset is the one reconcile path (memory, not
messages) across the REAL modules. Simulates a databasehost float by swapping each
module's transient for a fresh, cold one (the new host) and asserts every module
rebuilds the new transient from its perm authority - the smooth host change - plus
consistency-first ordering, idempotency, channel re-FULL, and the dispatcher's
shared-vector read culminating in UI.
"""
from __future__ import annotations
import os, sys

_HERE = os.path.abspath(__file__)
_ROOT = _HERE
for _ in range(3):
    _ROOT = os.path.dirname(_ROOT)                 # .../opt/frognet_semantic
_WORK = os.path.dirname(os.path.dirname(_ROOT))    # .../work
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_WORK, "opt", "frognet_semantic", "ribbit", "examples", "communicator"))

FAILS = []
def check(label, problems):
    if problems:
        FAILS.extend(problems); print(f"  [FAIL] {label}")
        for p in problems: print(f"         - {p}")
    else:
        print(f"  [PASS] {label}")

from working_memory import InMemoryTransient, InMemoryPerm
from substrate import Codex, Channel, Freshness

# ---------------------------------------------------------------------------
# Test fixtures: two modules that are each the perm authority for one element --
# a lossless event list and a move log with a latest-only position. hostReset is
# proven against them. (They were the calendar and backgammon bundles' codices;
# those applications were dropped with /etc/frognet_bundles.)
# ---------------------------------------------------------------------------
from typing import Any, Dict, List, Tuple
from working_memory import WorkingMemory, TransientStore, PermStore

EVENTS_ELEMENT = "fixture.events"
_EVENTS_FIELDS = ["name", "version", "event"]
_EVENTS_FRESHNESS = {
    "name":    Freshness.RESIDENT_ONCE,
    "version": Freshness.LATEST_ONLY,
    "event":   Freshness.LOSSLESS_EVENTUAL,     # every event must land, may be late
}


class EventsModule(WorkingMemory):
    """A shared list of events (test fixture). add_event commits perm-first then transient,
    and offers the event (lossless) + bumped version (latest)."""

    def __init__(self, transient: TransientStore, perm: PermStore):
        super().__init__(transient, perm)
        self.codex = Codex(None, "POST", "fixture/events",
                           _EVENTS_FIELDS, _EVENTS_FRESHNESS, mode="json",
                           resident={"name": EVENTS_ELEMENT})
        self.codex.learn()
        self._channels: Dict[str, Channel] = {}
        self._commit(EVENTS_ELEMENT, {"name": EVENTS_ELEMENT, "version": 0,
                                   "events": [], "ts": self.now_ms()})

    def add_event(self, ev: Dict[str, Any]) -> Dict[str, Any]:
        st = self._live(EVENTS_ELEMENT)
        st["events"] = [e for e in st["events"] if e.get("uid") != ev.get("uid")] + [ev]
        st["version"] = int(st["version"]) + 1
        self._commit(EVENTS_ELEMENT, st)
        for ch in self._channels.values():
            ch.offer("event", ev)                    # lossless: queued, never dropped
            ch.offer("version", st["version"])
        return ev

    def emit_to(self, peer_id: str) -> List[Tuple[bytes, str]]:
        ch = self._channels.setdefault(peer_id, Channel(self.codex))
        return ch.flush()

    def events(self) -> List[Dict[str, Any]]:
        return list(self._live(EVENTS_ELEMENT)["events"])

    # hostReset hooks: I am the perm authority for the events element.
    def _owned_keys(self):
        return [EVENTS_ELEMENT]

    def _consistency_check(self):
        """One event per uid (add_event's invariant). On reconcile after a network
        join, two islands' perms may both hold the uid - keep the latest by ts, so the
        re-asserted memory is coherent before it is published."""
        st = self.p.load(EVENTS_ELEMENT)
        if not st:
            return
        by_uid = {}
        for e in st.get("events", []):
            u = e.get("uid")
            if u not in by_uid or int(e.get("ts", 0)) >= int(by_uid[u].get("ts", 0)):
                by_uid[u] = e
        deduped = list(by_uid.values())
        if len(deduped) != len(st.get("events", [])):
            st["events"] = deduped
            self.p.save(EVENTS_ELEMENT, st)


class MovesModule(WorkingMemory):
    """A move log with a latest position (test fixture). play_move commits the new
    position and offers a bounded move-tip (lossless) + the position (latest-only)."""

    def __init__(self, transient: TransientStore, perm: PermStore, gid: str,
                 white: str = "white", black: str = "black"):
        super().__init__(transient, perm)
        self.gid = gid
        self.element = f"fixture.moves.{gid}"
        fields = ["gid", "position", "move"]
        freshness = {
            "gid":      Freshness.RESIDENT_ONCE,
            "position": Freshness.LATEST_ONLY,        # stale board coalesces
            "move":     Freshness.LOSSLESS_EVENTUAL,  # every move lands, in order
        }
        self.codex = Codex(None, "POST", f"fixture/moves/{gid}",
                           fields, freshness, mode="json", resident={"gid": gid})
        self.codex.learn()
        self._channels: Dict[str, Channel] = {}
        self._commit(self.element, {"gid": gid, "white": white, "black": black,
                                    "position": None, "moves": [], "ts": self.now_ms()})

    def play_move(self, move: Dict[str, Any], position: Any) -> None:
        st = self._live(self.element)
        st["moves"].append(move)                     # history is resident memory...
        st["position"] = position
        self._commit(self.element, st)
        for ch in self._channels.values():
            ch.offer("move", move)                   # ...the wire carries the bounded tip
            ch.offer("position", position)

    def emit_to(self, peer_id: str) -> List[Tuple[bytes, str]]:
        ch = self._channels.setdefault(peer_id, Channel(self.codex))
        return ch.flush()

    def moves(self) -> List[Dict[str, Any]]:
        return list(self._live(self.element)["moves"])

    # hostReset hooks: I am the perm authority for this element.
    def _owned_keys(self):
        return [self.element]

    def _consistency_check(self):
        """The position must reflect the last move that landed (latest-only position
        can coalesce stale, but the lossless move log is authoritative). If a position
        was published with no move behind it, fall back to no-position so peers
        re-derive from the move log rather than trust an orphan board."""
        st = self.p.load(self.element)
        if not st:
            return
        if st.get("position") is not None and not st.get("moves"):
            st["position"] = None
            self.p.save(self.element, st)


from frognet_host_reset import host_reset_all

def run():
    perm = InMemoryPerm()
    old_t = InMemoryTransient()
    cal = EventsModule(old_t, perm)
    bg = MovesModule(old_t, perm, gid="g1")
    cal.add_event({"uid": "e1", "title": "soccer", "ts": 10})
    cal.add_event({"uid": "e2", "title": "dentist", "ts": 20})
    bg.play_move({"n": 1, "from": 24, "to": 23}, position="p1")

    # --- simulate a databasehost FLOAT: each module now points at a fresh, COLD host ---
    new_t = InMemoryTransient()
    cal.t = new_t
    bg.t = new_t
    probs = []
    if new_t.get(EVENTS_ELEMENT) is not None:
        probs.append("precondition: new transient should start cold")
    check("[FLOAT] new databasehost transient starts cold (nothing migrated yet)", probs)

    # shared-vector read + UI culmination recorders (the dispatcher's job)
    rendered = {}
    def read_vector():
        # read shared memory BY TUPLE VECTOR from the (now re-asserted) new transient
        return [k for k in (EVENTS_ELEMENT, bg.element) if new_t.get(k) is not None]
    def render_ui(vec):
        rendered["vec"] = list(vec)

    out = host_reset_all([cal, bg], read_shared_vector=read_vector,
                         render_ui=render_ui)

    # --- 1. SMOOTH MOVE: new transient rebuilt from perm by re-assertion ---
    probs = []
    if new_t.get(EVENTS_ELEMENT) is None or new_t.get(bg.element) is None:
        probs.append("new transient was NOT repopulated from perm on hostReset")
    if cal.events() != [{"uid": "e1", "title": "soccer", "ts": 10},
                        {"uid": "e2", "title": "dentist", "ts": 20}]:
        probs.append(f"event list lost across the float: {cal.events()}")
    if bg.moves() != [{"n": 1, "from": 24, "to": 23}]:
        probs.append(f"move log lost across the float: {bg.moves()}")
    check("[SMOOTH] every module rebuilt the new transient from perm (memory, not migration)", probs)

    # --- 2. UI culmination over the re-asserted shared vector ---
    probs = []
    if rendered.get("vec") != [EVENTS_ELEMENT, bg.element]:
        probs.append(f"UI not rendered over the full re-asserted vector: {rendered.get('vec')}")
    if not out["ui_rendered"]:
        probs.append("dispatcher did not render UI")
    check("[UI] shared vector read AFTER all re-assert; Communicator UI rendered (living network)", probs)

    # --- 3. CONSISTENCY-FIRST: a dup uid in perm is repaired before re-assertion ---
    probs = []
    st = perm.load(EVENTS_ELEMENT)
    st["events"] = [{"uid": "e1", "ts": 10}, {"uid": "e1", "ts": 99}, {"uid": "e2", "ts": 20}]
    perm.save(EVENTS_ELEMENT, st)
    cal.t = InMemoryTransient()
    cal.hostReset()
    ev = cal.events()
    uids = sorted(e["uid"] for e in ev)
    e1 = next((e for e in ev if e["uid"] == "e1"), None)
    if uids != ["e1", "e2"]:
        probs.append(f"consistency did not dedup uid before write: {ev}")
    if not (e1 and e1["ts"] == 99):
        probs.append(f"consistency kept the stale e1 (want ts=99): {e1}")
    check("[CONSISTENCY] my world made coherent (dedup) BEFORE my memory is written", probs)

    # --- 4. IDEMPOTENT: re-running changes nothing (upsert, no version churn) ---
    probs = []
    cal.t = InMemoryTransient()
    r1 = cal.hostReset()
    v1 = cal.t.get(EVENTS_ELEMENT)["version"]
    r2 = cal.hostReset()
    v2 = cal.t.get(EVENTS_ELEMENT)["version"]
    if v1 != v2:
        probs.append(f"hostReset bumped version (not idempotent): {v1} -> {v2}")
    if r1["written"] != r2["written"]:
        probs.append("hostReset not idempotent across runs")
    check("[IDEMPOTENT] re-asserting the same authority does not churn version", probs)

    # --- 5. CHANNEL re-FULL: a converged reference is dropped so next emit is FULL ---
    probs = []
    cx = Codex(None, "POST", "p/x", ["a", "b"], {"a": Freshness.LATEST_ONLY,
                                                 "b": Freshness.LATEST_ONLY})
    ch = Channel(cx)
    _f, ref, kind = cx.encode({"a": 1, "b": 2}, ch.reference); ch.reference = ref
    _f2, _r2, kind2 = cx.encode({"a": 1, "b": 2}, ch.reference)   # would be SAME now
    ch.hostReset()
    _f3, _r3, kind3 = cx.encode({"a": 1, "b": 2}, ch.reference)   # reference dropped -> FULL
    if kind != "full" or kind2 != "same" or kind3 != "full":
        probs.append(f"channel reset did not force re-FULL: {kind}/{kind2}/{kind3}")
    check("[REFULL] hostReset drops the stale convergence reference; next emit is FULL", probs)

    # --- 6. SEE 1: trigger-agnostic - any reason takes the identical path ---
    probs = []
    cal.t = InMemoryTransient(); a = host_reset_all([cal], read_shared_vector=lambda: ["x"])
    cal.t = InMemoryTransient(); b = host_reset_all([cal], read_shared_vector=lambda: ["x"])
    if [r["written"] for r in a["modules"]] != [r["written"] for r in b["modules"]]:
        probs.append("two triggers took different paths - must be 'see 1'")
    check("[SEE1] one reconcile path regardless of what triggered it", probs)

    # --- 7. SotF / ffmpeg: the CALL reconciles (envelope survives, droppable frame shed) ---
    from media_codex import MediaCodex, SESSION_FIELDS
    mperm = InMemoryPerm(); mt = InMemoryTransient()
    call = MediaCodex(mt, mperm, session_id="call-1", codec="opus", level_idx=4)
    call.frame(seq=7, audio=b"\xaa\xbb", video=b"\xcc\xdd")   # an in-flight frame
    new_mt = InMemoryTransient(); call.t = new_mt              # mediahost/db float
    # converge a channel reference so we can prove re-FULL
    _f, _k = call.emit_to("peerX")[0][0], call.emit_to("peerX")
    rep = call.hostReset()
    probs = []
    s = new_mt.get("call-1")
    if s is None:
        probs.append("call session did NOT survive the float on the new host")
    else:
        if s.get("codec") != "opus" or s.get("level_idx") != 4:
            probs.append(f"session envelope (scaffold) lost: {s}")
        if s.get("seq") or s.get("audio") is not None or s.get("video") is not None:
            probs.append(f"droppable in-flight frame was REPLAYED on reset: {s}")
    # next emit after reset must be a FULL (reference dropped) carrying the envelope
    frames = call.emit_to("peerX")
    if not frames or frames[0][1] != "full":
        probs.append(f"media channel did not re-FULL after reset: {[k for _,k in frames]}")
    check("[SOTF] call envelope re-asserts, droppable frame shed, channel re-FULLs (ffmpeg returns)", probs)

    # --- 8. HostResetWatcher: a float is a resolved-IP delta; one reconcile per change ---
    from frognet_host_reset import HostResetWatcher
    class _M:
        def __init__(self): self.n = 0
        def hostReset(self): self.n += 1; return {"module": "m", "written": ["k"]}
    m = _M(); ip = ["10.250.250.1"]; ui = []
    w = HostResetWatcher(resolve_ip=lambda: ip[0], get_modules=lambda: [m],
                         read_shared_vector=lambda: ["v"], render_ui=lambda v: ui.append(v))
    r1 = w.tick()                                  # first: prime baseline, no reconcile
    r2 = w.tick()                                  # same IP: no-op
    ip[0] = "10.120.120.1"                          # control floats
    r3 = w.tick()                                  # delta: reconcile
    r4 = w.tick()                                  # settled again: no-op
    probs = []
    if r1 or r2 or r4:
        probs.append(f"watcher reconciled without a float: {(r1, r2, r4)}")
    if not r3 or m.n != 1:
        probs.append(f"watcher did not reconcile exactly once on the float (n={m.n})")
    if ui != [["v"]]:
        probs.append(f"living-network UI not rendered on the float: {ui}")
    check("[WATCH] float = resolved-IP delta -> exactly one reconcile (prime, then on change)", probs)

def main():
    print("=== hostReset: the one reconcile path (memory, not messages) across modules ===")
    run()
    print("\n" + ("ALL HOST-RESET CHECKS PASS" if not FAILS
                  else f"HOST-RESET CHECKS FAILED: {len(FAILS)}"))
    return 0 if not FAILS else 1

if __name__ == "__main__":
    sys.exit(main())
