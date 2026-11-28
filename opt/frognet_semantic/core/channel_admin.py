#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
################################################################
"""core/channel_admin.py -- the Admin's acts on a Role's channel, from a shell on a node that carries the Role.

It does what each <Role>Admin page's Channel panel will do, and nothing else: it writes the Role's own setting,
(channel, setting, <Role>) in the ControlRam, with one write, and holds a read on the ends' reports until every end
is running it. It never sends anything to an end and it never touches a connection.

    /opt/frognet_semantic/venv/bin/python3 /opt/frognet_semantic/core/channel_admin.py secret <role>
        Make the Role's secret on THIS node, 32 random bytes, in /etc/frognet/channel_secrets/<role> (mode 600), if
        there is none. The same file must be placed, offline, on every node that carries the Role's traffic: copy
        it there by whatever means you move files between your nodes. It is never sent by this software.

    /opt/frognet_semantic/venv/bin/python3 /opt/frognet_semantic/core/channel_admin.py show [<role>]
        Each Role's setting, and what every daemon and proxy says it holds and is sending under: the setting an end
        holds when it is not the ControlRam's, the step of the Role's own secret each end is on, and the setting an
        end IGNORED. Then the step each node is at, which node is at the highest, and whether this node is behind.

    /opt/frognet_semantic/venv/bin/python3 /opt/frognet_semantic/core/channel_admin.py set <role> --protocol tcp|chorus|openqasm
    /opt/frognet_semantic/venv/bin/python3 /opt/frognet_semantic/core/channel_admin.py set <role> --seal [--algorithm aes-256-gcm|chacha20-poly1305]
    /opt/frognet_semantic/venv/bin/python3 /opt/frognet_semantic/core/channel_admin.py set <role> --new-key
    /opt/frognet_semantic/venv/bin/python3 /opt/frognet_semantic/core/channel_admin.py set <role> --algorithm chacha20-poly1305
    /opt/frognet_semantic/venv/bin/python3 /opt/frognet_semantic/core/channel_admin.py set <role> --unseal
        One write. --seal, --new-key and --algorithm each put a new random number in the setting: every sending
        node computes its key for the Role from the Role's secret, its own FNID, the Role's name and that number
        ([THE_KEY_IS_COMPUTED_V1], core/channel_frame.py). A protocol and an encryption are not changed in one write.
        The command returns when every end that carries the Role reports it is sending the new way, or says which
        have not after --wait seconds (60).
        [A_SETTING_IS_NOT_WRITTEN_FROM_BEHIND_V1] Nothing is written from a node whose own secret for the Role is at
        a lower step than an end reports: those ends would refuse it. The command says which nodes are ahead.

A Role is the name before "Host" in its memory's name: unitsHost.exercise is the Role "units".
"""
import argparse
import json
import os
import secrets
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BUILD = "channel-admin-v7-20261128a"
PROTOCOLS = ("tcp", "chorus", "openqasm")


def _fits(run: dict, value: dict) -> bool:
    return (run.get("driver") == value.get("driver") and int(run.get("number") or 0) == int(value.get("number") or 0)
            and bool(run.get("sealed")) == bool(value.get("number"))
            and (not value.get("number") or run.get("protection") == (value.get("protection") or "aes-256-gcm"))
            # an end that holds the Role's own secret reports the step it is on; one that does not reports none
            and (run.get("step") is None or not value.get("step") or int(run["step"]) == int(value["step"])))


def _hosts() -> list:
    try:
        return [l.rstrip("\n") for l in open(os.environ.get("FROGNET_HOSTS") or os.environ.get("FDF_HOSTS") or "/etc/hosts")]
    except OSError:
        return []


def node_name(end: str, hosts: list = None) -> str:
    """A node's name, "Seattle7", from an end's name or an address; "" when this node's hosts file does not name it.
    Every node's hostname is FrogNetHost; its name is the hosts file's FrogNetHost.<name> on the line of its address."""
    ip = str(end).split(":", 1)[0]
    for l in (_hosts() if hosts is None else hosts):
        w = l.split()
        if w and w[0] == ip:
            for name in w[1:]:
                if name.startswith("FrogNetHost.") and len(name) > 12:
                    return name[12:]
    return ""


def node_of(end: str, hosts: list = None) -> str:
    """A node by its name and address, "Seattle7 (10.170.170.1)"; its address alone when it has no name here."""
    ip, name = str(end).split(":", 1)[0], node_name(end, hosts)
    return "%s (%s)" % (name, ip) if name else ip


def steps_reported(role: str, ends: dict = None) -> dict:
    """{a node's address: the highest step of the Role's own secret that any end on it reports}. An end that does not
    hold the Role's own secret reports no step and is not here."""
    out = {}
    for end, d in (reports() if ends is None else ends).items():
        step = ((d.get("rams") or {}).get(role.lower()) or {}).get("step")
        if step is not None:
            ip = str(end).split(":", 1)[0]
            out[ip] = max(out.get(ip, 0), int(step))
    return out


def ahead_of_here(role: str, ends: dict = None):
    """[A_SETTING_IS_NOT_WRITTEN_FROM_BEHIND_V1] (the step this node's own secret file for the Role is at, {address:
    step} of the nodes whose ends report a higher one). (None, {}) for a Role with no secret of its own here: such a
    Role has no steps.

    Found on John's network, 25 Nov 2026. Seattle7's secret for units was at step 110. A setting written from a node
    whose file was at step 106 carried step 107, and Seattle7 refused it: "a setting does not take a Role's secret
    back". Every end kept the last setting it could verify, the ends no longer agreed, and a sealed request between
    two of them was refused (HTTP 403). A node whose file is behind cannot write a setting the nodes ahead will take,
    so it does not write one. Nothing is tried in its place: the owner places the current secret file on this node."""
    from core import channel_frame as F
    try:
        _secret, at, own = F.read_secret(role.lower())
    except F.DoesNotOpen:
        return None, {}
    if not own:
        return None, {}
    return at, {ip: step for ip, step in steps_reported(role, ends).items() if step > at}


def with_step(role: str, value: dict, now: dict) -> dict:
    """[A_ROLES_OWN_SECRET_MOVES_FORWARD_V1] John, 22 Nov 2026 23:48: "Setting gains 'step', a count. At a key switch
    the owner's page draws a new number as always and raises step by one, in one write." Only for a Role with its own
    secret file on this machine; a Role on the network's default secret has no step, and the default never moves.

    `value`: the setting about to be written. `now`: the setting the ControlRam holds. A key switch is the number
    changing to another number. Any other write (a protocol, or sealing turned off) carries the step unchanged, so
    its check is still made with the secret every machine is on."""
    from core import channel_frame as F
    value = dict(value)
    value.pop("step", None)
    try:
        _secret, at, own = F.read_secret(role)
    except F.DoesNotOpen:
        return value                                   # no secret here: the setting carries no check either
    if not own:
        return value
    step = max(int(now.get("step") or 0), at)
    if value.get("number") and int(value["number"]) != int(now.get("number") or 0):
        step += 1
    if step:
        value["step"] = step
    return value


def write_and_wait(role: str, value: dict, ends: int = 1, wait_s: float = 60.0) -> dict:
    """The owner's one write of a Role's setting, then a held read on the ends' reports, (channel, at, *), until at
    least `ends` ends report, every one holds the setting, and every one that carries the Role is sending that way.
    Returns {ok, done, took, ends, hold, carry, run, behind}."""
    from core import channel_setting as C
    T, H = C._tuples()
    role = role.lower()
    at, ahead = ahead_of_here(role)
    if ahead:
        from core import channel_frame as F
        top = max(ahead.values())
        why = ("this node's secret for %s is at step %d and %s. A setting written here would be refused there (a "
               "setting does not take a Role's secret back), so it is NOT WRITTEN. Place %s from a node at step %d on "
               "this node, then write again."
               % (role, at, "; ".join("%s is at step %d" % (node_of(ip), step) for ip, step in sorted(ahead.items(), key=lambda x: (-x[1], x[0]))),
                  os.path.join(F.SECRETS, role), top))
        print("[CHANNEL-ADMIN] %s [A_SETTING_IS_NOT_WRITTEN_FROM_BEHIND_V1] %s" % (BUILD, why), flush=True)
        return {"ok": False, "done": False, "took": 0.0, "refused": why, "ahead": ahead, "at": at}
    value = with_step(role, value, settings().get(role) or {})
    t0 = time.time()
    ok = C.write(value, ram=role)
    pre, last = T.var_name("at", role + "~"), {}      # [A_REPORT_NAMES_ITS_ROLE_V1] this Role's reports, in its memory
    # [EVERY_END_OF_THE_NETWORK_IS_WAITED_FOR_V1] The ends of the network are the ones that say so in the ControlRam
    # (each writes there what it runs for traffic of no Role, naming no Role). The command is not done until each of
    # them has reported in this Role's memory. Without this the first command after a start returned when the three
    # ends that had reported so far held the setting, and said "every end holds it (3)" of six (simulator, 28 Nov 2026).
    known = set(C.reports())

    def done(rows):
        at = [(str(r.get("SensorName") or "")[len(pre):], r.get("data") or {}) for r in rows
              if str(r.get("SensorName") or "").startswith(pre)]
        last["ends"] = len(at)
        last["missing"] = sorted(known - {n for n, _d in at})

        def ignored(d):
            # [AN_END_SAYS_WHAT_IT_IGNORED_V1] this end could not verify THIS setting (it does not hold the Role's own
            # secret, or holds another) and said so: it will not take it, and is not waited for
            i = ((d.get("rams") or {}).get(role) or {}).get("ignored") or {}
            return (bool(i) and int(i.get("number") or 0) == int(value.get("number") or 0) and i.get("driver") == value.get("driver")
                    and int(i.get("step") or 0) == int(value.get("step") or 0))
        last["ignored"] = sorted(n for n, d in at if ignored(d))
        at = [(n, d) for n, d in at if not ignored(d)]
        last["hold"] = sum(1 for _n, d in at if _fits((d.get("rams") or {}).get(role) or {}, value))
        last["carry"] = sum(1 for _n, d in at if role in (d.get("running") or {}))
        last["run"] = sum(1 for _n, d in at if role in (d.get("running") or {}) and _fits(d["running"][role], value))
        last["behind"] = sorted(n + " (has not reported for this Role)" for n in last["missing"]) + sorted(n for n, d in at if not _fits((d.get("rams") or {}).get(role) or {}, value)
                                or (role in (d.get("running") or {}) and not _fits(d["running"][role], value)))
        return last["ends"] >= ends and not last["missing"] and last["hold"] == len(at) and last["run"] == last["carry"]
    try:
        # [A_HOSTS_EVENT_IS_NOT_THE_ANSWER_V1] a held read also returns when the hosts file is rewritten (the role may
        # be elsewhere now): that is a reason to hold again, not the ends' answer. Held again until done or the bound.
        until = time.monotonic() + wait_s
        while True:
            # [A_ROLES_SETTING_IS_IN_THE_ROLES_OWN_RAM_V1] the ends' reports for a Role are in the Role's memories (each
            # end writes its report into every one of them): the read is held on one
            rows = H.holder().wait_until(C.SERVICE, (list(C.memories(role)) or [C.CONTROL_RAM])[0], done, name_like=pre + "%",
                                         wait_s=max(0.0, until - time.monotonic()))
            finished = done(rows)
            if finished or time.monotonic() >= until:
                break
    except Exception as e:                     # the held read ended without it (its own timeout says so)
        finished = False
        last["error"] = "%s: %s" % (type(e).__name__, e)
    return dict(last, ok=bool(ok), done=bool(finished), took=time.time() - t0, wrote=value)


def settings() -> dict:
    """{Role: its setting} as the Roles' own memories hold them now, and "semantic" (traffic of no Role) as the
    ControlRam holds it. [A_ROLES_SETTING_IS_IN_THE_ROLES_OWN_RAM_V1]"""
    from core import channel_setting as C
    T, _H = C._tuples()
    out = {}
    for r in C.oldest_first(T.get(C.SERVICE, C.VAR, dbhost=C.CONTROL_RAM)):     # [THE_NEWEST_CELL_IS_THE_SETTING_V1]
        if str(r.get("scope") or "") == C.SCOPE:
            out[C.SCOPE] = r.get("value") or {}
    for role in sorted(set(C.memories().values())):
        v = C.read_setting(role)
        if v is not None:
            out[role] = v
    return out


def reports() -> dict:
    """{end: its report}: what each end says of traffic of no Role (the ControlRam) together with what it says, in each
    Role's own memories, of that Role. One report for each end, its "rams" and "running" holding every Role it told."""
    from core import channel_setting as C
    out = {n: dict(d) for n, d in C.reports().items()}
    for role in sorted(set(C.memories().values())):
        for n, d in C.reports(role).items():
            e = out.setdefault(n, {})
            for part in ("rams", "running"):
                if isinstance(d.get(part), dict):
                    e.setdefault(part, {}).update(d[part])
            e.setdefault("build", d.get("build"))
    return out


def cmd_secret(a) -> int:
    from core import channel_frame as F
    role = a.role.lower()
    path = os.path.join(F.SECRETS, role)
    if os.path.exists(path):
        secret, step, _own = F.read_secret(role)
        print("%s is already there (%s, hash %s). Nothing was changed."
              % (path, "as it was placed, step 0" if not step else "moved forward to step %d" % step, F.secret_fingerprint(secret)))
        return 0
    os.makedirs(F.SECRETS, mode=0o700, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(secrets.token_bytes(32))
    if role == F.DEFAULT_SECRET:
        print("made %s: the network's secret, 32 bytes, hash %s. It is the secret of every Role that has no file of "
              "its own, and it never moves." % (path, F.secret_fingerprint(open(path, "rb").read())))
        print("Place the same file at the same path on every node of the network.")
    else:
        print("made %s: the secret of the Role %r, 32 bytes, hash %s." % (path, role, F.secret_fingerprint(open(path, "rb").read())))
        print("Place the same file at the same path on every other node that carries this Role's traffic, BEFORE the "
              "Role's first key switch: from then on each node moves its own copy forward and the files as placed no "
              "longer match.")
    print("frognet_monitor shows the same hash on every node that has the same file.")
    return 0


def _setting_text(v: dict) -> str:
    """A setting, or what an end holds, in words: the protocol, sealed or not, the number, the step."""
    return "protocol %s, %s%s" % (v.get("driver") or "(the node's own)",
                                  "sealed with %s, number %s" % (v.get("protection") or "aes-256-gcm", v.get("number")) if v.get("number") else "not sealed",
                                  ", step %s" % v.get("step") if v.get("step") is not None else "")


def cmd_show(a) -> int:
    from core import channel_frame as F
    from core import channel_setting as C
    held, ends = settings(), reports()
    hosts = _hosts()
    try:
        me = C._tuples()[0].my_ip()
    except Exception:
        me = ""
    roles = sorted(r for r in held if r != "semantic" and (not a.role or r == a.role.lower()))
    if not roles:
        print("no Role has a channel setting%s" % (" named %r" % a.role if a.role else ""))
    for role in roles:
        s = held[role]
        print("%s: %s" % (role, _setting_text(s)))
        # this node's own secret file for the Role: read, never made here (`secret <role>` makes one)
        try:
            secret, at, own = F.read_secret(role)
            print("    this node, %s: %s" % (node_of(me, hosts) if me else "(no FrogNet address)",
                  "its secret for %s is at step %d, hash %s" % (role, at, F.secret_fingerprint(secret)) if own
                  else "it has no secret of %s's own: it uses the network's default secret, which has no steps" % role))
        except F.DoesNotOpen:
            at, own = None, False
            print("    this node, %s: it holds no secret for %s" % (node_of(me, hosts) if me else "(no FrogNet address)", role))
        ignored_it = 0
        for end in sorted(ends):
            d = ends[end]
            h, r = dict((d.get("rams") or {}).get(role) or {}), (d.get("running") or {}).get(role)
            ig = h.pop("ignored", None)
            holds = ("it%s" % (" (step %s)" % h["step"] if h.get("step") is not None else "") if h and _fits(h, s)
                     else ("another: %s" % _setting_text(h) if h else "nothing"))
            print("    %-46s holds %s; sending %s" % (("%s %s" % (node_name(end, hosts), end)).strip(), holds, ("this way" if _fits(r, s) else "ANOTHER WAY: %s" % json.dumps(r, sort_keys=True)) if r else "nothing for this Role"))
            if ig:
                # [AN_END_SAYS_WHAT_IT_IGNORED_V1] the end names the setting it ignored; its journal has the reason
                this = (int(ig.get("number") or 0) == int(s.get("number") or 0) and ig.get("driver") == s.get("driver")
                        and int(ig.get("step") or 0) == int(s.get("step") or 0))
                ignored_it += this
                # (an end reports the number, protocol and step of what it ignored, and nothing else of it)
                print("        IGNORED %s: protocol %s, number %s, step %s. Its journal says why, on a line with [A_SETTING_PROVES_ITSELF_V1]."
                      % ("THIS setting" if this else "a setting", ig.get("driver") or "(the node's own)", ig.get("number") or "none", ig.get("step") or 0))
        steps = steps_reported(role, ends)
        if steps:
            by = {}
            for ip, step in steps.items():
                by.setdefault(step, []).append(ip)
            print("    steps of the secret of %s, by node: %s" % (role, "; ".join("%d at %s" % (step, ", ".join(node_of(ip, hosts) for ip in sorted(by[step])))
                                                                        for step in sorted(by, reverse=True))))
            top = max(by)
            if len(by) > 1:
                print("    THE NODES ARE NOT AT ONE STEP. The highest is %d, at %s. A setting written from a node below "
                      "the highest is refused by the nodes above it." % (top, ", ".join(node_of(ip, hosts) for ip in sorted(by[top]))))
            if own and at is not None and top > at:
                print("    THIS NODE IS BEHIND: its file is at step %d and the highest reported is %d. `set` does not write "
                      "from here ([A_SETTING_IS_NOT_WRITTEN_FROM_BEHIND_V1])." % (at, top))
        else:
            print("    no end reports a step for %s: none holds a secret of %s's own" % (role, role))
        if ignored_it:
            print("    %d end(s) IGNORED the setting the ControlRam holds." % ignored_it)
    return 0


def cmd_set(a) -> int:
    from core import channel_frame as F
    from core import channel_protection as P
    role = a.role.lower()
    now = dict(settings().get(role) or {})
    now.pop("check", None)
    now.pop("ts", None)                                # the store's own stamp on the tuple: not part of the setting
    new = dict(now)
    encryption = a.seal or a.unseal or a.new_key or bool(a.algorithm)
    if a.protocol and encryption:
        print("a protocol and an encryption are not changed in one write: give one, then the other")
        return 2
    if not a.protocol and not encryption:
        print("nothing to set: give --protocol, --seal, --new-key, --algorithm or --unseal")
        return 2
    if a.protocol:
        new["driver"] = a.protocol
    else:
        if a.algorithm and a.algorithm not in P.PROTECTIONS:
            print("no algorithm %r is registered here (there are: %s)" % (a.algorithm, ", ".join(sorted(P.PROTECTIONS))))
            return 2
        if a.unseal:
            new.pop("number", None); new.pop("protection", None)
        else:
            if a.new_key and not now.get("number"):
                print("%s is not sealed: --seal turns its encryption on" % role)
                return 2
            F.secret_of(role)                              # this node must hold a secret: it makes the setting's check
            number = secrets.randbits(62) + 1              # (the step is raised with it, in write_and_wait: with_step)
            while number == now.get("number"):
                number = secrets.randbits(62) + 1
            new["number"] = number                         # the Admin's random number: the key is computed from it
            new["protection"] = a.algorithm or now.get("protection") or "aes-256-gcm"
    new.setdefault("driver", "tcp")
    saw = write_and_wait(role, new, ends=1, wait_s=a.wait)
    if saw.get("refused"):
        return 1                                        # said by write_and_wait, with the nodes that are ahead
    print("%s: %s" % (role, json.dumps(saw.get("wrote") or new, sort_keys=True)))
    if saw.get("ignored"):
        print("%d end(s) IGNORED it: its check did not verify there. They do not hold %s's secret at this step (or hold "
              "another), and do not carry its sealed traffic: %s" % (len(saw["ignored"]), role, ", ".join(saw["ignored"])))
    if saw.get("done"):
        print("every end holds it (%d) and every end that carries %s is sending that way (%d), %.0f ms after the write"
              % (saw["hold"], role, saw["run"], saw["took"] * 1000))
        return 0
    print("NOT every end is on it after %.0f s: %d of %d hold it, %d of %d that carry %s are sending that way. Behind: %s%s"
          % (saw["took"], saw.get("hold", 0), saw.get("ends", 0), saw.get("run", 0), saw.get("carry", 0), role,
             ", ".join(saw.get("behind") or []) or "(none reported)", "  (%s)" % saw["error"] if saw.get("error") else ""))
    return 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="The Admin's acts on a Role's channel (%s)." % BUILD)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("secret"); p.add_argument("role"); p.set_defaults(fn=cmd_secret)
    p = sub.add_parser("show"); p.add_argument("role", nargs="?", default=""); p.set_defaults(fn=cmd_show)
    p = sub.add_parser("set"); p.add_argument("role")
    p.add_argument("--protocol", choices=PROTOCOLS)
    p.add_argument("--seal", action="store_true"); p.add_argument("--unseal", action="store_true")
    p.add_argument("--new-key", action="store_true"); p.add_argument("--algorithm", default="")
    p.add_argument("--wait", type=float, default=60.0)
    p.set_defaults(fn=cmd_set)
    a = ap.parse_args(argv)
    print("[CHANNEL-ADMIN] %s" % BUILD, flush=True)
    try:
        return a.fn(a)
    except Exception as e:
        print("[CHANNEL-ADMIN] %s: %s" % (type(e).__name__, e), flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
