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
install_plane.py - simulate a CLEAN machine being installed, from the REAL scripts.

The merge planes (proof_plane, lifecycle, frognet_sim) all start from a node that is
already installed. Everything that goes wrong BEFORE the first merge - the database
user that never got created, the schema that was never loaded, the service that was
never enabled - was invisible to the whole simulator, so it could only ever be found
by installing a real box and watching it fail. This plane closes that gap.

It does NOT execute bash. It models the machine state an install must ESTABLISH, and
it derives the state changes from the REAL script text (the SQL those scripts run),
so the model cannot drift from the shipped installer the way a hand-written fake would.

Two things it proves:
  1. POSTCONDITIONS - after the databasehost install, does the machine actually hold
     the databases, users, grants and files the mesh needs? A databasehost that has
     FrogUser@localhost but not FrogUser@'%' is eligible for the role and unusable in
     it: local queries pass, every remote node gets access-denied.
  2. REACHABILITY - is the code that establishes a postcondition reachable on a CLEAN
     machine? frognet_install.sh D8 puts CREATE USER 'FrogUser' *below*
     `if [[ -f /frognet_db.sql ]]; then ... else die`, so a fresh box with no restore
     dump dies before the user is ever created. The SQL is correct and unreachable.
"""
from __future__ import annotations

import os
import re


# --------------------------------------------------------------------------- #
#  a very small MySQL model - enough for CREATE/ALTER/DROP USER, CREATE
#  DATABASE and GRANT, which is all an install actually does.
# --------------------------------------------------------------------------- #
class FakeMysql:
    def __init__(self):
        self.databases: set[str] = set()
        self.users: set[tuple[str, str]] = set()          # (user, host)
        self.grants: set[tuple[str, str, str]] = set()    # (db, user, host)
        # rows[db] = set of markers. Enough to prove a restore CLOBBERS live data.
        self.rows: dict[str, set] = {}
        self.migrated = False

    # -- statement handlers -------------------------------------------------- #
    _RE_CREATE_DB = re.compile(
        r"CREATE\s+DATABASE\s+(?:IF\s+NOT\s+EXISTS\s+)?`?([A-Za-z0-9_$]+)`?", re.I)
    _RE_DROP_DB = re.compile(r"DROP\s+DATABASE\s+(?:IF\s+EXISTS\s+)?`?([A-Za-z0-9_$]+)`?", re.I)
    _RE_USER = re.compile(
        r"(CREATE|ALTER|DROP)\s+USER\s+(?:IF\s+(?:NOT\s+)?EXISTS\s+)?'([^']+)'@'([^']+)'", re.I)
    _RE_GRANT = re.compile(
        r"GRANT\s+.+?\s+ON\s+`?([A-Za-z0-9_$*]+)`?\.\*\s+TO\s+'([^']+)'@'([^']+)'",
        re.I | re.S)

    def execute(self, sql: str):
        # Shell heredocs/-e blocks escape backticks (\`FrogNet\`) so bash does not
        # command-substitute them. Strip the escapes before matching identifiers.
        sql = sql.replace("\\`", "`").replace("\\$", "$")
        for stmt in [s.strip() for s in sql.split(";") if s.strip()]:
            m = self._RE_CREATE_DB.search(stmt)
            if m:
                self.databases.add(m.group(1)); continue
            m = self._RE_DROP_DB.search(stmt)
            if m:
                self.databases.discard(m.group(1))
                self.rows.pop(m.group(1), None); continue
            m = self._RE_USER.search(stmt)
            if m:
                verb, user, host = m.group(1).upper(), m.group(2), m.group(3)
                if verb == "DROP":
                    self.users.discard((user, host))
                    self.grants = {g for g in self.grants if (g[1], g[2]) != (user, host)}
                else:
                    self.users.add((user, host))
                continue
            m = self._RE_GRANT.search(stmt)
            if m:
                db, user, host = m.group(1), m.group(2), m.group(3)
                self.users.add((user, host))   # GRANT implies the account exists
                self.grants.add((db, user, host))
                continue

    def seed_live_data(self, db, marker):
        self.rows.setdefault(db, set()).add(marker)

    def restore_dump(self, db):
        """A dump restore REPLACES the database contents - which is precisely why it
        must never run under --preserve, where those rows are the user's live data."""
        self.rows[db] = {"__from_shipped_dump__"}

    def has_row(self, db, marker):
        return marker in self.rows.get(db, set())

    # -- queries the oracle asks --------------------------------------------- #
    def has_user(self, user, host):
        return (user, host) in self.users

    def can_reach(self, db, user, host):
        return (db, user, host) in self.grants


class Machine:
    """A clean machine: nothing installed, nothing configured."""

    def __init__(self, name="clean", files=None):
        self.name = name
        self.mysql = FakeMysql()
        self.files = dict(files or {})      # path -> contents
        self.dirs: dict[str, str] = {}      # path -> octal mode ('' = default)
        self.venv = False                   # is a usable venv present?
        self.installed = False              # does a prior FrogNet install exist?

    def has_file(self, path):
        return path in self.files



# --------------------------------------------------------------------------- #
#  filesystem: what directories the install actually creates, and with what mode
# --------------------------------------------------------------------------- #
def _brace_expand(tok: str):
    """/var/log/{a,b} -> ['/var/log/a', '/var/log/b'] (one level, which is all the
    installer uses). Anything without braces passes through untouched."""
    m = re.search(r"\{([^{}]*)\}", tok)
    if not m:
        return [tok]
    out = []
    for part in m.group(1).split(","):
        out.extend(_brace_expand(tok[:m.start()] + part + tok[m.end():]))
    return out


def apply_filesystem(machine, script_path: str):
    """Replay the script's `mkdir -p` and `chmod` against the modelled machine, so
    a missing directory (or a mode the daemon cannot write through) is provable
    here instead of showing up as a dead service on a box."""
    text = open(script_path, encoding="utf-8", errors="replace").read()
    text = text.replace("\\\n", " ")          # join line continuations
    for line in text.splitlines():
        # [CHAINED_CMD_PARSE_V1] Split on shell separators before matching.
        # The previous version tested startswith() against the WHOLE line with
        # chmod as an elif, so a chained command was invisible:
        #     mkdir -p /var/log/apache2 && chmod 777 /var/log/apache2 || true
        # matched the mkdir branch, recorded the directory, and never looked at
        # the chmod -- so the mode read back as default and the oracle failed on
        # an installer that was doing exactly the right thing.
        for seg in re.split(r"&&|\|\||;", line):
            seg = seg.strip()
            if seg.startswith("mkdir -p"):
                for tok in seg[len("mkdir -p"):].split():
                    if tok.startswith("/"):
                        for d in _brace_expand(tok):
                            machine.dirs.setdefault(d, "")
            elif seg.startswith("chmod "):
                parts = seg.split()
                if len(parts) >= 3 and re.fullmatch(r"[0-7]{3,4}", parts[1]):
                    for tok in parts[2:]:
                        if tok.startswith("/"):
                            for d in _brace_expand(tok):
                                machine.dirs[d] = parts[1]
    return machine

# --------------------------------------------------------------------------- #
#  run the three install paths against a modelled machine, using the REAL SQL
# --------------------------------------------------------------------------- #
def simulate_install(machine: Machine, mode: str, bin_dir: str, web_dir: str,
                     dump_present: bool = True, log=None):
    """mode: 'clean' | 'reset' | 'preserve'.

    clean     - virgin box, no prior install.
    reset     - prior install, no --preserve: frognet_reset.sh wipes first.
    preserve  - prior install, --preserve: UPGRADE IN PLACE. Nothing removed, the
                shipped dump must NOT be restored over live data, schema migrated.

    The SQL comes from the real scripts; only the branch decisions are modelled.
    """
    say = log or (lambda *_a: None)
    m = machine.mysql

    # ---- A0: reset (default on a prior install; skipped by --preserve) ------ #
    if mode == "reset":
        rst = os.path.join(bin_dir, "frognet_reset.sh")
        if os.path.exists(rst):
            for block in extract_sql(rst):
                m.execute(block)                     # the real DROP DATABASE/USER
            say("A0 reset: applied frognet_reset.sh SQL")
        machine.installed = False
        # the venv is carried across the wipe, not destroyed
        say(f"A0 reset: venv carried = {machine.venv}")
    elif mode == "preserve":
        say("A0: --preserve, nothing removed")

    # ---- D4/D8 via the databasehost installer (real SQL) ------------------- #
    dbh = os.path.join(bin_dir, "install_databasehost.sh")
    if os.path.exists(dbh):
        apply_script(machine, dbh, subs={"DB_USER": "FrogUser",
                                         "DB_PASS": "secret", "DB_NAME": "FrogNet"})

    # ---- D8: restore-or-migrate -------------------------------------------- #
    # --preserve must NOT restore the dump: that would overwrite live rows.
    if mode != "preserve" and dump_present:
        m.restore_dump("FrogNet")
        say("D8: restored /frognet_db.sql (seed)")
    else:
        say("D8: no dump restore")
    # migrations run in ALL modes - the only way a schema change reaches an
    # existing database on an upgrade.
    fixups = os.path.join(web_dir, "schema_fixups.sql")
    if os.path.exists(fixups):
        m.migrated = True
        say("D8: applied schema_fixups.sql")

    machine.installed = True
    return machine



# --------------------------------------------------------------------------- #
#  pull the REAL SQL out of the REAL shell scripts
# --------------------------------------------------------------------------- #
_HEREDOC = re.compile(r"mysql[^\n<]*<<-?\s*'?(\w+)'?\n(.*?)\n\1\s*$", re.S | re.M)
_DASH_E = re.compile(r"mysql[^\n]*?-e\s+\"(.*?)\"", re.S)


def extract_sql(script_path: str) -> list[str]:
    """Every SQL block the script hands to mysql, in order. Real source, not a copy."""
    text = open(script_path, encoding="utf-8", errors="replace").read()
    blocks = [m.group(2) for m in _HEREDOC.finditer(text)]
    blocks += [m.group(1) for m in _DASH_E.finditer(text)]
    return blocks


def apply_script(machine: Machine, script_path: str, subs=None):
    """Run a script's SQL against the modelled machine. `subs` fills shell vars."""
    subs = subs or {}
    for block in extract_sql(script_path):
        sql = block
        for k, v in subs.items():
            sql = sql.replace(f"${{{k}}}", v).replace(f"${k}", v)
        machine.mysql.execute(sql)
    return machine


# --------------------------------------------------------------------------- #
#  reachability: is the statement that establishes a postcondition guarded by a
#  condition a CLEAN machine cannot satisfy?
# --------------------------------------------------------------------------- #
def guarded_by_missing_file(script_path: str, needle: str, clean_machine: Machine):
    """Return the guard file if `needle` sits after an `if [[ -f X ]] ... else die`
    whose X is absent on a clean machine, else None - i.e. the statement is CORRECT
    but UNREACHABLE on a fresh install.

    Nesting is walked line by line rather than regex-matched. A non-greedy `.*?fi`
    stops at the FIRST `fi`, which is the inner block's, so an inner `else die`
    (a legitimate "the operation failed" fatal) got attributed to the outer guard
    and flagged a correct error handler. Only a die in the OUTER block's final else
    - the branch taken when the file is absent - makes the tail unreachable."""
    text = open(script_path, encoding="utf-8", errors="replace").read()
    idx = text.find(needle)
    if idx < 0:
        return None

    _IFF = re.compile(r"^\s*if\s+.*;\s*then\s*$")
    _ELIF = re.compile(r"^\s*elif\s+.*;\s*then\s*$")
    _FTEST = re.compile(r"\[\[\s+-f\s+([^\s\]]+)\s+\]\]")
    stack, closed = [], []          # closed: (end_offset, guard_path)
    off = 0
    for line in text.splitlines(keepends=True):
        s = line.strip()
        if _IFF.match(line):
            m = _FTEST.search(line)
            stack.append({"paths": [m.group(1)] if m else [], "else": False,
                          "else_txt": ""})
        elif _ELIF.match(line) and stack:
            m = _FTEST.search(line)
            if m:
                stack[-1]["paths"].append(m.group(1))
            stack[-1]["else"] = False          # elif ends any prior else
        elif s == "else" and stack:
            stack[-1]["else"] = True
        elif s == "fi" and stack:
            blk = stack.pop()
            for p in blk["paths"]:
                p = p.strip('"').strip("'")
                if not clean_machine.has_file(p) and "die" in blk["else_txt"]:
                    closed.append((off + len(line), p))
        elif stack and stack[-1]["else"]:
            stack[-1]["else_txt"] += line
        off += len(line)

    for end, path in closed:
        if end < idx:
            nxt = text.find('phase "', end)
            if nxt == -1 or idx < nxt:         # same phase => the die kills it
                return path
    return None
