#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
################################################################
"""[A_SETTING_IS_NOT_WRITTEN_FROM_BEHIND_V1] oracle, and what `channel_admin.py show` says of steps and ignored settings.

John's network, 25 Nov 2026: Seattle7's secret for units was at step 110; a setting written from a node at step 106
carried step 107 and was refused there; `show` said only "holds another" of every end. The contracts:

  1. `show` prints, for each end, the setting it holds when that is not the ControlRam's, its step, and the setting
     it IGNORED; then each node's step, the node at the highest, and whether this node is behind.
  2. The owner's one write (write_and_wait) writes NOTHING from a node whose own secret file for the Role is at a
     lower step than an end reports, and names the nodes that are ahead.
  3. A node that is not behind writes as before. A Role on the network's default secret has no steps and is written
     as before.

This node is a secrets directory. The ControlRam's setting and the ends' reports are the ones his network printed.
"""
import contextlib
import io
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DIR = tempfile.mkdtemp(prefix="secrets-here-")
os.environ["FROGNET_CHANNEL_SECRETS"] = DIR
HOSTS = os.path.join(DIR, "hosts")
os.environ["FROGNET_HOSTS"] = HOSTS
from core import channel_frame as F  # noqa: E402
from core import channel_setting as C  # noqa: E402
from core import channel_admin as ADM  # noqa: E402

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    passed, failed = passed + bool(ok), failed + (not ok)
    print("  %s  %s%s" % ("PASS" if ok else "FAIL", name, "" if ok else "  " + str(detail)[:400]))


def secret_at_step(step):
    """This node's own secret file for units, moved to `step` the way a machine moves it."""
    path = os.path.join(DIR, "units")
    if os.path.exists(path):
        os.remove(path)
    open(path, "wb").write(b"p" * 32)
    if step:
        F.keep_secret("units", F.secret_at("units", step, DIR)[0], step, DIR)
    C._secrets.clear()


class _T:
    @staticmethod
    def my_ip():
        return "10.251.251.1"

    @staticmethod
    def var_name(var, scope):
        return "%s.%s" % (var, scope)


class _Holder:
    def wait_until(self, *a, **k):
        return []


class _H:
    @staticmethod
    def holder():
        return _Holder()


def main():
    C.SECRETS = F.SECRETS = DIR
    open(os.path.join(DIR, "default"), "wb").write(b"d" * 32)
    open(HOSTS, "w").write("10.251.251.1 FrogNetHost FrogNetHost.Seattle3B\n10.170.170.1 FrogNetHost.Seattle7\n"
                           "10.28.28.1 FrogNetHost.New-York-2\n")
    now = {"driver": "chorus", "number": 2966159178094958340, "protection": "chacha20-poly1305", "step": 107, "check": "x"}
    held7 = {"driver": "openqasm", "number": 741535880863264567, "protection": "chacha20-poly1305", "sealed": True, "step": 110,
             "ignored": {"number": 2966159178094958340, "driver": "chorus", "step": 107}}
    ends = {
        "10.170.170.1:daemon_main.py.976088": {"rams": {"units": dict(held7)}},
        "10.170.170.1:proxy_main.py.976089": {"rams": {"units": dict(held7)},
                                              "running": {"units": {"driver": "openqasm", "number": 741535880863264567,
                                                                    "protection": "chacha20-poly1305", "sealed": True}}},
        "10.251.251.1:proxy_main.py.3093226": {"rams": {"units": {"driver": "chorus", "number": 2966159178094958340,
                                                                  "protection": "chacha20-poly1305", "sealed": True, "step": 107}}},
        "10.28.28.1:daemon_main.py.511304": {"rams": {"units": {"ignored": {"number": 5, "driver": "tcp", "step": 3}}}},
    }
    wrote = []
    ADM.settings = lambda: {"units": dict(now), "warning": {"driver": "tcp"}}
    ADM.reports = lambda: ends
    C._tuples = lambda: (_T, _H)
    C.write = lambda value, dbhost=None, ram=None: (wrote.append((ram, dict(value))), True)[1]

    class A:
        role = "units"

    # ---- 1. show, on a node whose file is at step 107 while Seattle7 reports 110
    secret_at_step(107)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = ADM.cmd_show(A)
    text = out.getvalue()
    print(text)
    lines = text.splitlines()
    check("show returns 0 and writes nothing", rc == 0 and not wrote)
    check("the Role's line says its step", lines[0] == "units: protocol chorus, sealed with chacha20-poly1305, number 2966159178094958340, step 107", lines[0])
    check("this node's own step and hash are said, by its name",
          any(l.startswith("    this node, Seattle3B (10.251.251.1): its secret for units is at step 107, hash ") for l in lines), lines[1])
    check("an end that holds another setting says which, with its step",
          any("Seattle7 10.170.170.1:daemon_main.py.976088" in l and "holds another: protocol openqasm, sealed with chacha20-poly1305, "
              "number 741535880863264567, step 110" in l for l in lines))
    check("an end that holds the ControlRam's setting says so, with its step",
          any("Seattle3B 10.251.251.1:proxy_main.py.3093226" in l and "holds it (step 107)" in l for l in lines))
    check("an end that only ignored a setting holds nothing",
          any("New-York-2 10.28.28.1:daemon_main.py.511304" in l and "holds nothing" in l for l in lines))
    check("an end that IGNORED the ControlRam's setting says THIS setting, by number, protocol and step",
          sum("IGNORED THIS setting: protocol chorus, number 2966159178094958340, step 107." in l for l in lines) == 2)
    check("an end that ignored some other setting says a setting, and nothing the end did not report",
          any("IGNORED a setting: protocol tcp, number 5, step 3." in l for l in lines) and not any("IGNORED" in l and "sealed with" in l for l in lines))
    check("each node's step is listed, highest first, by name",
          any(l == "    steps of the secret of units, by node: 110 at Seattle7 (10.170.170.1); 107 at Seattle3B (10.251.251.1)" for l in lines))
    check("the nodes are said not to be at one step, naming the highest",
          any("THE NODES ARE NOT AT ONE STEP. The highest is 110, at Seattle7 (10.170.170.1)." in l for l in lines))
    check("this node is said to be behind", any("THIS NODE IS BEHIND: its file is at step 107 and the highest reported is 110." in l for l in lines))
    check("the count of ends that ignored the ControlRam's setting is said", any("2 end(s) IGNORED the setting the ControlRam holds." in l for l in lines))

    # ---- 2. the owner's write from behind: nothing is written
    secret_at_step(106)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        saw = ADM.write_and_wait("units", {"driver": "chorus", "number": 12345, "protection": "chacha20-poly1305"}, ends=1, wait_s=0)
    text = out.getvalue()
    print(text)
    check("from a node at step 106 with an end at 110, nothing is written", not wrote, wrote)
    check("the result says it was not written and why", saw.get("ok") is False and saw.get("done") is False and "NOT WRITTEN" in (saw.get("refused") or ""))
    check("the nodes that are ahead are named with their steps, and this node's step is said",
          "this node's secret for units is at step 106 and Seattle7 (10.170.170.1) is at step 110" in text
          and "Seattle3B (10.251.251.1) is at step 107" in text, text)
    check("the result names the nodes ahead", saw.get("ahead") == {"10.170.170.1": 110, "10.251.251.1": 107} and saw.get("at") == 106, saw)
    check("it says where the secret file goes and the step to take it from", os.path.join(DIR, "units") in text and "from a node at step 110" in text)

    class S:
        role, protocol, seal, unseal, new_key, algorithm, wait = "units", "tcp", False, False, False, "", 0.0
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = ADM.cmd_set(S)
    check("`set` from behind returns 1, writes nothing, and says it once", rc == 1 and not wrote and out.getvalue().count("NOT WRITTEN") == 1, out.getvalue())

    # ---- 3. a node that is not behind writes as before
    secret_at_step(110)
    with contextlib.redirect_stdout(io.StringIO()):
        saw = ADM.write_and_wait("units", {"driver": "chorus", "number": 12345, "protection": "chacha20-poly1305"}, ends=1, wait_s=0)
    check("from a node at step 110, the highest reported, the setting is written", len(wrote) == 1 and wrote[0][0] == "units" and not saw.get("refused"), (wrote, saw))
    check("a key switch written there carries step 111", wrote and wrote[0][1].get("step") == 111, wrote)
    del wrote[:]
    with contextlib.redirect_stdout(io.StringIO()):
        saw = ADM.write_and_wait("warning", {"driver": "tcp"}, ends=1, wait_s=0)
    check("a Role on the network's default secret has no steps and is written as before", len(wrote) == 1 and "step" not in wrote[0][1] and not saw.get("refused"), wrote)
    check("ahead_of_here says (None, {}) for such a Role", ADM.ahead_of_here("warning") == (None, {}))

    print("\n%d passed, %d failed  (%s)" % (passed, failed, ADM.BUILD))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
