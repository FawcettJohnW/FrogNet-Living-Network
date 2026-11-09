#!/usr/bin/env python3
# Copyright (C) 2016-2026 Fawcett Innovations LLC. SPDX-License-Identifier: GPL-2.0-only
# test_op_selects_template_oracle.py -- [OP_SELECTS_THE_TEMPLATE_V1] each op of an endpoint is its own template; the
# same op with another app (an instance value) is the same template.
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from proxy.templates import canonical_semantic_key  # noqa: E402

FAILS = []
def check(ok, what):
    print(("PASS  " if ok else "FAIL  ") + what)
    if not ok: FAILS.append(what)

k = lambda q, m="GET", b=b"": canonical_semantic_key(m, "/dc/ram.php?" + q, b)
ops = ["op=rams&app=openeoc", "op=admin&app=openeoc", "op=roles&app=openeoc", "op=whoami"]
keys = [k(q) for q in ops]
check(len(set(keys)) == len(keys), "1 every op of /dc/ram.php is its own template: %s" % keys)
check(k("op=rams&app=openeoc") == k("op=rams&app=kart"), "2 the same op for another app is the same template: %s" % k("op=rams&app=kart"))
check(k("op=login&app=openeoc", "POST", b'{"user":"a","password":"b"}') != k("op=admin&app=openeoc", "POST", b'{"user":"a","password":"b"}'),
      "3 POSTs: op=login and op=admin with the same body fields are different templates")
a = canonical_semantic_key("GET", "/api.php?entity=sensors&action=list&SensorType=GPS", b"")
check(a == "/api.php?entity=sensors&action=list&__dyn=SensorType", "4 api.php keys (no op) as before: " + a)
print("RESULT", "PASS" if not FAILS else "FAIL: %d" % len(FAILS)); sys.exit(1 if FAILS else 0)
