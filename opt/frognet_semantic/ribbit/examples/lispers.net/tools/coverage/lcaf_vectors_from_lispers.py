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
# Generate LCAF RLOC-record vectors with lispers.net's OWN encoder (lisp.py from its source tree)
import sys; sys.argv = ["x"]
import lisp, json
def rloc_with(setter):
    r = lisp.lisp_rloc_record(); r.rloc = lisp.lisp_address(lisp.LISP_AFI_IPV4, "198.51.100.9", 32, 0)
    r.priority, r.weight = 1, 100; r.reach_bit = True; r.local_bit = False
    setter(r); return r.encode().hex()
out = {}
def geo(r):
    g = lisp.lisp_geo("acc-geo"); g.parse_geo_string("45-30-10-N-122-40-20-W"); r.geo = g
out["geo"] = rloc_with(geo)
def elp(r):
    e = lisp.lisp_elp("acc-elp")
    for a, strict in (("198.51.100.1", True), ("198.51.100.2", False)):
        n = lisp.lisp_elp_node(); n.address = lisp.lisp_address(lisp.LISP_AFI_IPV4, a, 32, 0); n.strict = strict; e.elp_nodes.append(n)
    r.elp = e
out["elp"] = rloc_with(elp)
def rle(r):
    e = lisp.lisp_rle("acc-rle")
    for a, lvl in (("198.51.100.3", 0), ("198.51.100.4", 1)):
        n = lisp.lisp_rle_node(); n.rloc.rloc = lisp.lisp_address(lisp.LISP_AFI_IPV4, a, 32, 0); n.level = lvl; e.rle_nodes.append(n)
    r.rle = e
out["rle"] = rloc_with(rle)
def js(r): r.json = lisp.lisp_json("acc-json", '{"site":"acceptance","n":1}')
out["json"] = rloc_with(js)
print(json.dumps(out, indent=1))
