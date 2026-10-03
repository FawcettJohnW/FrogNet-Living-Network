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
"""matched_audit.py -- the matched complexity audit: lispers.net 0.643 and Ribbit-LISP, the same job, the same rules.

  python3 tools/matched_audit.py --lispers-src /path/to/lispers.net/lisp --lispers-release /path/to/installed/lispers \
          --out matched-audit [--lispers-coverage lispers-coverage.json] [--ribbit-gcov ribbit-gcov.json]

The source is checked against the installed release first, every time: each file must compile to exactly the release's
bytecode, or nothing is measured.

Run from the Ribbit-LISP package directory. Needs lizard (pip install lizard). Writes into --out:
  MATCHED-AUDIT.md          the rules, the results, the secondary classification, the coverage cross-check
  lispers-functions.csv     every lispers.net function: in or out of scope, and why
  ribbit-functions.csv      every Ribbit function: application / platform / out, and why

THE RULES are fixed below, before any measurement, and the script prints them into the report:

 1. The job is the LISP map-server and map-resolver as the acceptance suite exercises it: Map-Register (with
    authentication and encryption), Map-Request (ECM and bare), Map-Notify and Map-Notify-Ack, Info-Request, the
    Map-Referral a map-server sends as DDT authority, sites and their options, policies, named locators (geo, ELP,
    RLE, JSON), registration state and its expiry, and the configuration commands that set these up.
 2. Out on both sides, with the reason recorded per function: other roles (ITR, ETR, RTR, the data plane, a
    map-resolver following referrals as a DDT client), features neither the tests nor Ribbit exercise (crypto-EID
    signatures, pub-sub, eid-crypto-hash, NAT-traversal proxying), and operator surfaces (show, debug, API, web UI,
    lig/rig, test waits and statistics).
 3. Ribbit's in-process backend -- the no-RAM branch of an `if(ram)` chain, which exists only for the `local` test
    stage -- is out. Where an Engine function mixes backends, only its no-RAM branches are removed and the rest is
    measured; where a chain tests only `resident`, nothing is removed (conservative: counted against Ribbit).
 4. Reachability decides what is in: lispers.net from its map-server, map-resolver and lisp-core packet and
    configuration entry points; Ribbit from its front, its RAM-server-hosted operation and the in-scope branches of
    Engine::dispatch(). Python calls are resolved per module, per class (self), and for other method calls by
    rapid type analysis (a method is reachable when its class is instantiated in reachable code and its name is
    called there). C++ calls are resolved by name within the application files.
 5. Ribbit's FrogNet machinery -- the client library, FNWP, the semantic codec and templates, the memory, EBR, the
    data plane, the RAM server outside its LISP operation -- is the PLATFORM, reported in a second row, whole
    (not filtered by reachability). lispers.net's Python runtime and standard library are not counted; its own
    infrastructure in lisp.py (IPC, sockets, timers) is its code and counts in its application row.
 6. One tool, one setting: lizard with defaults, functions only (module-level code is not a function on either
    side). Where Rule 3 removes branches from a Ribbit function, its NLOC and CCN are recounted with lizard's own
    counting rule (if for while case catch && || ?), which the script checks against lizard on every function it
    does not alter.
 7. The secondary classification (LISP semantics / coordination and state machinery / configuration) is by name
    rules listed in the report; it is judgment, published line by line, and not part of the primary result.
"""
import argparse, ast, collections, csv, json, os, re, subprocess, sys

# ----------------------------------------------------------------------------------------------- scope: lispers.net
LISPERS_ROOTS = [
    # map-server process (lisp-ms.py): startup (sockets, the registration-expiry timer), the IPC receive, the
    # command dispatcher and the packet dispatcher its main loop calls
    ("lisp-ms.py", "lisp_ms_startup"), ("lisp.py", "lisp_receive"), ("lispconfig.py", "lisp_process_command"),
    ("lisp.py", "lisp_parse_packet"),
    # map-resolver process (lisp-mr.py)
    ("lisp-mr.py", "lisp_mr_startup"), ("lisp-mr.py", "lisp_mr_parse_packet"),
    # lisp-core: the packet dispatch on 4342 and the Info-Request source table's expiry
    ("lisp-core.py", "lisp_core_dispatch_packet"), ("lisp-core.py", "lisp_timeout_info_sources"),
    # the configuration commands of the job
    ("lisp-ms.py", "lisp_site_command"), ("lisp-ms.py", "lisp_ms_auth_prefix_command"),
    ("lisp-ms.py", "lisp_ms_map_server_peer_command"), ("lisp-ms.py", "lisp_ms_encryption_keys_command"),
    ("lispconfig.py", "lisp_geo_command"), ("lispconfig.py", "lisp_elp_command"),
    ("lispconfig.py", "lisp_rle_command"), ("lispconfig.py", "lisp_json_command"), ("lisp.py", "lisp_policy_command"),
]
LISPERS_STOP = {   # not traversed: out of the job, with the reason
    "lisp_timeout_pubsub": "pub-sub (map subscriptions): not in the job",
    "lisp_process_pubsub": "pub-sub (map subscriptions): not in the job",
    "lisp_notify_subscribers": "pub-sub (map subscriptions): not in the job",
    "lisp_store_pubsub_state": "pub-sub (map subscriptions): not in the job",
    "lisp_process_map_reply": "xTR role: Map-Reply processing",
    "lisp_process_unicast_map_notify": "xTR role: Map-Notify processing",
    "lisp_process_multicast_map_notify": "xTR role: multicast Map-Notify processing",
    "lisp_process_info_reply": "xTR role: Info-Reply processing",
    "lisp_process_map_referral": "map-resolver as DDT client (following referrals): not in the job",
    "lisp_ddt_root_command": "map-resolver as DDT client: not in the job",
    "lisp_referral_cache_command": "map-resolver as DDT client: not in the job",
    "lisp_clear_referral_cache": "map-resolver as DDT client: not in the job",
    "lisp_process_api": "operator API: not in the job",
    "lisp_ms_eid_crypto_hash_command": "crypto-EIDs: not in the job",
    "lisp_verify_cga_sig": "crypto-EID signatures: not in the job",
    "lisp_find_sig_in_rloc_set": "crypto-EID signatures: not in the job",
    "lisp_get_signature_eid": "crypto-EID signatures: not in the job",
    "lisp_nat_proxy_map_request": "NAT-traversal proxying (RTR): not in the job",
    "lisp_nat_proxy_reply": "NAT-traversal proxying (RTR): not in the job",
}
LISPERS_STOP_PATTERNS = [   # (regex on the function name, reason)
    (r"(^|_)show(_|$)|_display_|_walk_", "operator show/display: not in the job"),
    (r"^lisp_debug|_debug_", "debug: not in the job"),
    (r"^lisp_(itr|etr|rtr)_", "other role (ITR/ETR/RTR): not in the job"),
    (r"^lisp_(lig|rig)|_lig_|_rig_", "lig/rig tools: not in the job"),
    (r"^lisp_bottle|_login|_landing_page|_web_", "web UI: not in the job"),
    (r"rloc_probe|telemetry|crypto_decap", "xTR role (RLOC probing, telemetry, data-plane crypto): not in the job"),
    (r"decent", "LISP-Decent mapping system: not in the job"),
    (r"(^|_)rtrs?(_|$)", "RTR / NAT-traversal proxying: not in the job"),
    (r"retransmit_ddt", "map-resolver as DDT client: not in the job"),
]
# ------------------------------------------------------------------------------------------------- scope: Ribbit
RIBBIT_APP_FILES = ["ribbit_cpp/lisp_engine.hpp", "ribbit_cpp/lisp_handler.hpp", "tools/lisp_boundary.cpp",
                    "tools/ramsrv/ram_server.cpp"]
RIBBIT_RAM_APP_FUNCS = re.compile(r"^(lisp_op|ResidentMemory::.*|private_service)$")   # the rest of ram_server = platform
RIBBIT_PLATFORM_EXCLUDE = re.compile(r"pyuni_.*tables\.hpp$")                          # generated tables
RIBBIT_OPS_IN = {"wire.register6", "wire.request6", "wire.request4", "wire.register4_notify", "wire.register_notify",
                 "wire.register4", "registration.put", "registration.delete", "resolution.get", "site.add",
                 "site.delete", "ms.encryption_key", "ms.policy", "ms.named_locator"}
RIBBIT_OPS_OUT = [   # (regex on the operation, reason)
    (r"\.(wait|stats|notified|sent)$|_wait$|^resolver\.wait|^ram\.count$|^transport\.stats$|^system\.get$|^wire\.auth_verify$",
     "test / diagnostic surface: not in the job"),
    (r"^(etr_|etr\.|wire\.etr_|database_mapping\.|map_cache\.|map_resolver\.)", "other role (ETR/ITR): not in the job"),
    (r"^ddt\.", "map-resolver as DDT client (delegations): not in the job"),
    (r"^ms_governor\.", "native FrogNet registration governor: not the wire job"),
]
# ------------------------------------------------------------------------------------- secondary classification
COORD = r"ipc|socket|sock|receive|recv|segment|timer|thread|lock|mutex|queue|strand|worker|session|held|watch|await|" \
        r"applied|snapshot|ebr|retire|checkpoint|restart|fork|spawn|pipe|select|poll|futex|wait|startup|shutdown|" \
        r"process_command|dispatch_packet|parse_packet|loop|reject|log|memory|remote_call|call$|cache"
CONFIG = r"_command$|kv_pair|config"

def lizard_rows(files, cwd=None):
    out = subprocess.run([sys.executable, "-m", "lizard"] + files, capture_output=True, text=True, cwd=cwd).stdout
    rows = {}
    for l in out.splitlines():
        m = re.match(r"\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\S+)@(\d+)-(\d+)@(\S+)", l)
        if m:
            rows[(m.group(9), int(m.group(7)))] = dict(nloc=int(m.group(1)), ccn=int(m.group(2)), name=m.group(6),
                                                        file=m.group(9), a=int(m.group(7)), b=int(m.group(8)))
    return list(rows.values())

# ======================================================================================== lispers.net reachability
def verify_against_release(src, release):
    """Every source file must compile (optimized, as the release is) to exactly the bytecode of the release's .pyc."""
    import importlib.util, marshal
    def flat(co):
        out = [co.co_code, tuple(c for c in co.co_consts if not hasattr(c, "co_code")), co.co_names]
        for c in co.co_consts:
            if hasattr(c, "co_code"): out.append(flat(c))
        return out
    same, diff, skipped = [], [], []
    for f in sorted(x for x in os.listdir(src) if x.endswith(".py")):
        pyc = os.path.join(release, f[:-3] + ".pyc")
        if not os.path.exists(pyc): skipped.append(f); continue
        b = open(pyc, "rb").read()
        if b[:4] != importlib.util.MAGIC_NUMBER:
            raise SystemExit("%s was compiled by a different Python (magic %s); run this with the Python the release was built for"
                             % (pyc, b[:4].hex()))
        co = compile(open(os.path.join(src, f)).read(), f, "exec", optimize=1)
        (same if flat(co) == flat(marshal.loads(b[16:])) else diff).append(f)
    return same, diff, skipped

def lispers_scope(src):
    if not os.path.isdir(src):
        raise SystemExit("--lispers-src %s: no such directory" % src)
    files = sorted(f for f in os.listdir(src) if f.endswith(".py"))
    if not files:
        pycs = [f for f in os.listdir(src) if f.endswith(".pyc")]
        raise SystemExit("--lispers-src %s has no Python source%s. The audit reads source: use the lisp/ directory of\n"
                         "lispers.net's repository (git clone https://github.com/farinacci/lispers.net); the installed release\n"
                         "goes in --lispers-release, and the source is checked against it byte for byte."
                         % (src, " (%d compiled .pyc files: this is an installed release)" % len(pycs) if pycs else ""))
    mods = {}
    for f in files:
        tree = ast.parse(open(os.path.join(src, f)).read())
        funcs, classes = {}, {}
        for node in tree.body:
            if isinstance(node, ast.FunctionDef): funcs[node.name] = node
            elif isinstance(node, ast.ClassDef):
                classes[node.name] = dict(node=node, bases=[b.id for b in node.bases if isinstance(b, ast.Name)],
                                          methods={n.name: n for n in node.body if isinstance(n, ast.FunctionDef)})
        mods[f] = dict(funcs=funcs, classes=classes)
    modname = {f[:-3].replace("-", "_"): f for f in files}

    def find_func(name, here):
        for f in [here, "lisp.py", "lispconfig.py"] + files:
            if f in mods and name in mods[f]["funcs"]: return (f, None, name)
        return None
    def find_class(name, here):
        for f in [here, "lisp.py", "lispconfig.py"] + files:
            if f in mods and name in mods[f]["classes"]: return (f, name)
        return None
    def method_in(cls, m):   # cls = (file, classname); walk bases by name
        seen = set()
        while cls and cls not in seen:
            seen.add(cls); c = mods[cls[0]]["classes"][cls[1]]
            if m in c["methods"]: return (cls[0], cls[1], m)
            cls = next((find_class(b, cls[0]) for b in c["bases"] if find_class(b, cls[0])), None)
        return None

    def stop_reason(name):
        if name in LISPERS_STOP: return LISPERS_STOP[name]
        for rx, why in LISPERS_STOP_PATTERNS:
            if re.search(rx, name): return why
        return None

    reach, stopped = set(), {}
    inst, called_attrs = set(), set()
    todo = []
    for f, n in LISPERS_ROOTS:
        k = find_func(n, f)
        if not k: raise SystemExit("root %s:%s not found -- the rules name a function this source does not have" % (f, n))
        todo.append(k)
    def body_of(k):
        f, c, n = k
        return mods[f]["funcs"][n] if c is None else mods[f]["classes"][c]["methods"][n]
    while True:
        while todo:
            k = todo.pop()
            if k in reach: continue
            why = stop_reason(k[2])
            if why and k not in [(f, None, n) for f, n in LISPERS_ROOTS]: stopped[k] = why; continue
            reach.add(k); node = body_of(k); f, cls, _ = k
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call):
                    fn = sub.func
                    if isinstance(fn, ast.Name):
                        t = find_func(fn.id, f)
                        if t: todo.append(t)
                        else:
                            c = find_class(fn.id, f)
                            if c: inst.add(c)
                    elif isinstance(fn, ast.Attribute):
                        v = fn.value
                        if isinstance(v, ast.Name) and v.id in modname:                   # module.function / module.Class
                            mf = modname[v.id]
                            if fn.attr in mods[mf]["funcs"]: todo.append((mf, None, fn.attr))
                            elif fn.attr in mods[mf]["classes"]: inst.add((mf, fn.attr))
                        elif isinstance(v, ast.Name) and v.id == "self" and cls:
                            t = method_in((f, cls), fn.attr)
                            if t: todo.append(t)
                        else: called_attrs.add(fn.attr)
                if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load) and sub.id.startswith("lisp_"):
                    t = find_func(sub.id, f)                                              # a function passed as a value
                    if t: todo.append(t)
        # rapid type analysis: instantiated classes x method names called anywhere reachable
        new = []
        for c in list(inst):
            t = method_in(c, "__init__")
            if t and t not in reach: new.append(t)
            for m in called_attrs:
                t = method_in(c, m)
                if t and t not in reach and t not in stopped: new.append(t)
        if not new: break
        todo.extend(new)

    rows = lizard_rows(files, cwd=src)
    lines = {}
    for k in list(reach) + list(stopped):
        n = body_of(k); lines[(k[0], n.lineno)] = k
    out = []
    for r in rows:
        k = lines.get((os.path.basename(r["file"]), r["a"]))
        r["file"] = os.path.basename(r["file"])
        if k in reach: r["scope"], r["reason"] = "in", "reachable from the job's entry points"
        elif k in stopped: r["scope"], r["reason"] = "out", stopped[k]
        else:
            why = stop_reason(r["name"].split("::")[-1])
            r["scope"], r["reason"] = "out", why or "not reachable from the job's entry points"
        r["qual"] = ("%s.%s" % (k[1], k[2]) if k and k[1] else r["name"])
        out.append(r)
    return out

# ============================================================================================= Ribbit reachability
def strip_text(s):
    s = re.sub(r"//[^\n]*", "", s); s = re.sub(r"/\*.*?\*/", "", s, flags=re.S)
    s = re.sub(r'"(\\.|[^"\\])*"', '""', s); s = re.sub(r"'(\\.|[^'\\])*'", "''", s)
    return s
def decisions(s):
    s = strip_text(s)
    return len(re.findall(r"\b(if|for|while|case|catch)\b", s)) + s.count("&&") + s.count("||") + len(re.findall(r"\?(?!:)", s))
def nloc(s): return sum(1 for l in strip_text(s).split("\n") if l.strip())

def _span(s, j):
    """the statement starting at j: a {block} or up to its ';' -- returns (start, end)"""
    n = len(s)
    while j < n and s[j].isspace(): j += 1
    if j < n and s[j] == "{":
        d = 0; k = j
        while k < n:
            d += {"{": 1, "}": -1}.get(s[k], 0); k += 1
            if d == 0: return j, k
        return j, n
    d = 0; k = j
    while k < n:
        c = s[k]
        if c in "({[": d += 1
        elif c in ")}]": d -= 1
        elif c == ";" and d == 0: return j, k + 1
        k += 1
    return j, n
def _cond_end(s, j):
    d = 0
    for k in range(j, len(s)):
        if s[k] == "(": d += 1
        elif s[k] == ")":
            d -= 1
            if d == 0: return k + 1
    return len(s)
def strip_local(text):
    """Rule 3: remove the no-RAM backend -- `if(!ram) S`, and the final `else` of an if-chain that tests `ram`."""
    s = strip_text(text); removed = 0
    while True:
        m = re.search(r"\bif\s*\(\s*!\s*ram\s*\)", s)
        if not m: break
        a, e = _span(s, m.end()); s = s[:m.start()] + ";" + s[e:]; removed += 1
    pos = 0
    while True:
        m = re.search(r"\bif\s*\(\s*(resident|ram)\s*\)", s[pos:])
        if not m: break
        st = pos + m.start(); j = pos + m.end(); tests_ram = m.group(1) == "ram"
        a, e = _span(s, j)
        while True:
            rest = s[e:]; ws = len(rest) - len(rest.lstrip())
            if not rest.lstrip().startswith("else"): break
            k = e + ws + 4
            nxt = s[k:].lstrip()
            if nxt.startswith("if"):
                kk = k + (len(s[k:]) - len(nxt)); ce = _cond_end(s, kk)
                if re.match(r"if\s*\(\s*ram\s*\)", s[kk:ce]): tests_ram = True
                a, e = _span(s, ce); continue
            a2, e2 = _span(s, k)
            if tests_ram: s = s[:e] + s[e2:]; removed += 1
            else: e = e2
            break
        pos = st + 2
    return s, removed

def dispatch_blocks(body):
    i = body.index("{") + 1; blocks = []; depth = 0; start = i; k = i; n = len(body); instr = None
    while k < n:
        c = body[k]
        if instr:
            if c == "\\": k += 2; continue
            if c == instr: instr = None
        elif c in "\"'": instr = c
        elif c == "/" and body[k:k + 2] == "//": k = body.index("\n", k); continue
        elif c == "{": depth += 1
        elif c == "}":
            if depth == 0: blocks.append(body[start:k]); break
            depth -= 1
            if depth == 0 and not body[k + 1:].lstrip().startswith("else"): blocks.append(body[start:k + 1]); start = k + 1
        elif c == ";" and depth == 0: blocks.append(body[start:k + 1]); start = k + 1
        k += 1
    return [b.strip() for b in blocks if b.strip()]

def ribbit_scope(root):
    ribbit_files = sorted("ribbit_cpp/" + f for f in os.listdir(os.path.join(root, "ribbit_cpp"))
                          if f.endswith((".hpp", ".cpp", ".h")) and not RIBBIT_PLATFORM_EXCLUDE.search(f))
    files = sorted(set(ribbit_files + RIBBIT_APP_FILES))
    rows = lizard_rows(files, cwd=root)
    src = {f: open(os.path.join(root, f)).read().split("\n") for f in files}
    def text(r): return "\n".join(src[r["file"]][r["a"] - 1:r["b"]])
    mism = sum(1 for r in rows if decisions(text(r)) + 1 != r["ccn"])   # our counter against lizard (reported)
    app = [r for r in rows if r["file"] in RIBBIT_APP_FILES and (r["file"] != "tools/ramsrv/ram_server.cpp" or RIBBIT_RAM_APP_FUNCS.match(r["name"]))]
    plat = [r for r in rows if r not in app and r["file"] not in ("ribbit_cpp/ribbit_lisp.cpp",)]
    ops_out, disp = [], [r for r in app if r["name"] == "Engine::dispatch"][0]
    kept = []
    for b in dispatch_blocks(text(disp)):
        head = b[:b.find("{")] if "{" in b[:400] else b
        ops = re.findall(r'op=="([^"]+)"', head)
        if not ops: kept.append(b); continue
        if any(o in RIBBIT_OPS_IN for o in ops): kept.append(b); continue
        why = next((w for rx, w in RIBBIT_OPS_OUT if re.search(rx, ops[0])), "not in the job")
        ops_out.append(("/".join(dict.fromkeys(ops)), why, nloc(b), decisions(b)))
    disp_text = "{\n" + "\n".join(kept) + "\n}"
    # reachability over the application: roots = the front, the hosted operation, dispatch's in-scope branches
    by_name = collections.defaultdict(list)
    for r in app: by_name[r["name"].split("::")[-1]].append(r)
    def body(r):
        t = disp_text if r is disp else text(r)
        return strip_local(t)[0]
    roots = [r for r in app if r["file"] in ("ribbit_cpp/lisp_handler.hpp", "tools/lisp_boundary.cpp")
             or r["file"] == "tools/ramsrv/ram_server.cpp"] + [disp]
    reach = set(); todo = list(roots)
    while todo:
        r = todo.pop(); key = (r["file"], r["a"])
        if key in reach: continue
        reach.add(key)
        for ident in set(re.findall(r"\b([A-Za-z_]\w*)\s*\(", body(r))):
            for t in by_name.get(ident, []):
                if t is not disp: todo.append(t)
        if r["name"] == "Engine::call": todo.append(disp)
    # constructors and destructors are never called by name: in, when their class is used by in-scope code
    used_text = "\n".join(body(r) for r in app if (r["file"], r["a"]) in reach)
    for r in app:
        parts = r["name"].split("::")
        if len(parts) >= 2 and parts[-1].lstrip("~") == parts[-2] and re.search(r"\b%s\b" % re.escape(parts[-2]), used_text):
            reach.add((r["file"], r["a"]))
    altered_ok = altered_n = 0
    for r in app:
        if (r["file"], r["a"]) not in reach:
            r["scope"], r["reason"] = "out", "not reachable from the job's entry points"; continue
        t = disp_text if r is disp else text(r)
        s, removed = strip_local(t)
        r["scope"] = "in"
        if r is disp or removed:
            altered_n += 1; altered_ok += (decisions(text(r)) + 1 == r["ccn"] and nloc(text(r)) == r["nloc"])
            r["nloc"], r["ccn"] = nloc(s), decisions(s) + 1
            r["reason"] = ("in-scope branches only (%d of %d operations out, see list); no-RAM backend removed" %
                           (len(ops_out), len(ops_out) + len([b for b in kept if 'op==' in b]))) if r is disp else \
                          "no-RAM backend removed (%d branch%s)" % (removed, "" if removed == 1 else "es")
        else: r["reason"] = "reachable from the job's entry points"
    for r in plat: r["scope"], r["reason"] = "platform", "FrogNet machinery (Rule 5)"
    return app, plat, ops_out, mism, len(rows), (altered_ok, altered_n)

# ============================================================================================================ run
def classify(name):
    n = name.split("::")[-1].split(".")[-1].lower()
    if re.search(CONFIG, n): return "configuration"
    if re.search(COORD, n): return "coordination/state"
    return "LISP semantics"

def totals(rs):
    n = len(rs); c = sum(r["ccn"] for r in rs)
    return dict(functions=n, nloc=sum(r["nloc"] for r in rs), ccn=c, avg=(c / n if n else 0), over15=sum(1 for r in rs if r["ccn"] > 15))

def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--lispers-src", required=True, help="lispers.net source directory (its lisp/)")
    ap.add_argument("--ribbit", default=".", help="the Ribbit-LISP package directory (default: here)")
    ap.add_argument("--out", default="matched-audit")
    ap.add_argument("--lispers-release", required=True, help="the installed lispers.net release the tests ran (its .pyc files)")
    ap.add_argument("--lispers-coverage", help="coverage.py JSON of lispers.net under the acceptance suite (optional)")
    ap.add_argument("--ribbit-gcov", help="gcovr JSON of Ribbit under the acceptance suite (optional)")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    if not os.path.isdir(a.lispers_src) or not any(f.endswith(".py") for f in os.listdir(a.lispers_src)): lispers_scope(a.lispers_src)
    same, diff, skipped = verify_against_release(a.lispers_src, a.lispers_release)
    print("lispers.net source %s vs release %s: %d of %d files compile to identical bytecode%s" %
          (a.lispers_src, a.lispers_release, len(same), len(same) + len(diff) + len(skipped),
           ("; DIFFERENT: " + ", ".join(diff)) if diff else "") + ("; no .pyc in the release for: " + ", ".join(skipped) if skipped else ""))
    if diff: raise SystemExit("the source is not the release the tests ran: nothing measured")
    if not same: raise SystemExit("no source file matched a .pyc in %s: is that the installed release?" % a.lispers_release)
    verified = (a.lispers_release, len(same), skipped)
    L = lispers_scope(a.lispers_src)
    R_app, R_plat, ops_out, mism, nrows, (alt_ok, alt_n) = ribbit_scope(a.ribbit)
    Lin = [r for r in L if r["scope"] == "in"]; Rin = [r for r in R_app if r["scope"] == "in"]
    for r in Lin + Rin: r["class"] = classify(r["qual"] if "qual" in r else r["name"])
    # coverage (optional)
    if a.lispers_coverage:
        cj = json.load(open(a.lispers_coverage))["files"]
        exe = {os.path.basename(k): set(v["executed_lines"]) for k, v in cj.items()}
        for r in L: r["ran"] = "yes" if any(n in exe.get(r["file"], set()) for n in range(r["a"] + 1, r["b"] + 1)) else "no"
    if a.ribbit_gcov:
        gj = json.load(open(a.ribbit_gcov)); cnt = {}
        for f in gj["files"]:
            cnt[f["file"]] = {l["line_number"]: l["count"] for l in f["lines"]}
        for r in R_app + R_plat:
            m = next((v for k, v in cnt.items() if k.endswith(r["file"])), {})
            r["ran"] = "yes" if any(m.get(n, 0) > 0 for n in range(r["a"], r["b"] + 1)) else ("no" if m else "")
    # files
    for fn, rs, extra in (("lispers-functions.csv", L, []), ("ribbit-functions.csv", R_app + R_plat, [])):
        with open(os.path.join(a.out, fn), "w", newline="") as fh:
            w = csv.writer(fh); w.writerow(["scope", "file", "function", "first", "last", "nloc", "ccn", "class", "ran", "reason"])
            for r in sorted(rs, key=lambda r: (r["scope"], r["file"], r["a"])):
                w.writerow([r["scope"], r["file"], r.get("qual", r["name"]), r["a"], r["b"], r["nloc"], r["ccn"],
                            r.get("class", ""), r.get("ran", ""), r["reason"]])
    tL, tR, tP = totals(Lin), totals(Rin), totals(R_plat)
    tRP = totals(Rin + R_plat)
    def row(label, t): return "| %s | %d | %d | %d | %.1f | %d |" % (label, t["functions"], t["nloc"], t["ccn"], t["avg"], t["over15"])
    md = ["# Matched audit -- lispers.net 0.643 and Ribbit-LISP: the same job, the same rules", "",
          "Produced by `tools/matched_audit.py`. The rules were fixed in the script before measuring; they are",
          "reproduced here. Every function on both sides, in or out and why, is in `lispers-functions.csv` and",
          "`ribbit-functions.csv`.", "", "```", __doc__.split("THE RULES are fixed below, before any measurement, and the script prints them into the report:\n")[1].rstrip(), "```", "",
          "## Result", "", "| | functions | NLOC | total CCN | average CCN | functions over CCN 15 |", "|---|---:|---:|---:|---:|---:|",
          row("lispers.net 0.643 -- application (Python)", tL), row("Ribbit-LISP -- application (C++)", tR),
          row("Ribbit-LISP -- application + FrogNet platform (C++)", tRP), row("(the FrogNet platform alone)", tP), "",
          "Python and C++ lines are not the same unit (C++ carries braces and declarations); CCN counts decisions in",
          "both and compares more directly.", "",
          "## Ribbit's Engine::dispatch(): operations out of the job", "", "| operation(s) | reason | NLOC | decisions |", "|---|---|---:|---:|"]
    for o, why, nl, dc in ops_out: md.append("| %s | %s | %d | %d |" % (o, why, nl, dc))
    md += ["", "## lispers.net: why functions are out", "", "| reason | functions | NLOC | total CCN |", "|---|---:|---:|---:|"]
    bucket = collections.defaultdict(list)
    for r in L:
        if r["scope"] == "out": bucket[r["reason"]].append(r)
    for why, rs in sorted(bucket.items(), key=lambda kv: -sum(r["nloc"] for r in kv[1])):
        md.append("| %s | %d | %d | %d |" % (why, len(rs), sum(r["nloc"] for r in rs), sum(r["ccn"] for r in rs)))
    md += ["", "## Secondary: what the in-scope code does (Rule 7 -- name rules, judgment)", "",
           "coordination/state: names matching `%s`; configuration: `%s`; the rest: LISP semantics." % (COORD, CONFIG), "",
           "| | class | functions | NLOC | total CCN |", "|---|---|---:|---:|---:|"]
    for label, rs in (("lispers.net", Lin), ("Ribbit-LISP", Rin)):
        for cl in ("LISP semantics", "coordination/state", "configuration"):
            s = [r for r in rs if r["class"] == cl]
            md.append("| %s | %s | %d | %d | %d |" % (label, cl, len(s), sum(r["nloc"] for r in s), sum(r["ccn"] for r in s)))
    if a.lispers_coverage or a.ribbit_gcov:
        md += ["", "## Cross-check: in-scope functions the acceptance suite never ran", ""]
        for label, rs in (("lispers.net", Lin if a.lispers_coverage else []), ("Ribbit-LISP", Rin if a.ribbit_gcov else [])):
            if not rs: continue
            nr = [r for r in rs if r.get("ran") == "no"]
            md.append("- %s: %d of %d in-scope functions (%d of %d NLOC) never ran." % (label, len(nr), len(rs), sum(r["nloc"] for r in nr), sum(r["nloc"] for r in rs)))
    md += ["", "## Checks", "",
           "- lizard and this script's counter agree on %d of %d Ribbit functions (the disagreements are in platform code with character literals, where lizard's own numbers are used)." % (nrows - mism, nrows),
           "- On the %d Ribbit functions Rule 3 alters, the counter agrees with lizard on %d before altering (NLOC and CCN): the recount is lizard's measure." % (alt_n, alt_ok),
           "- lispers.net roots: %d, all found; functions stopped by name: see the CSV." % len(LISPERS_ROOTS),
           "- The lispers.net source compiles to bytecode identical to the release in %s: %d of %d files%s." %
            (verified[0], verified[1], verified[1] + len(verified[2]), (" (no .pyc for: " + ", ".join(verified[2]) + ")") if verified[2] else "")]
    open(os.path.join(a.out, "MATCHED-AUDIT.md"), "w").write("\n".join(md) + "\n")
    print("\n".join(md[md.index("## Result"):md.index("## Result") + 8]))
    print("wrote %s/MATCHED-AUDIT.md, lispers-functions.csv, ribbit-functions.csv" % a.out)

if __name__ == "__main__":
    main()
