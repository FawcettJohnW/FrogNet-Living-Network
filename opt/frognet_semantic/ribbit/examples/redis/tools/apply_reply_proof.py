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
"""[REPLY_EXTRACTION_PROVEN_V1] Apply the reply-proof fix to a copy of the platform's fnwp_engine.hpp.
Refuses if the anchor is not found exactly once."""
import sys
p = sys.argv[1]; s = open(p).read()
old = """        else dyn = semtpl::extract(t.resp_frag, a.body, true);
    }"""
new = """        else {
            dyn = semtpl::extract(t.resp_frag, a.body, true);
            // [REPLY_EXTRACTION_PROVEN_V1] prove the template holds this answer before trusting the fields, as the
            // client proves a request: extract keeps only the template paths (absent ones read as null, others are
            // dropped), so an answer of another shape would be rebuilt by the far end as the old shape, silently.
            if (dyn && !a.body.empty()) {
                pyv::List vals; for (auto& kv : *dyn) vals.push_back(kv.second);
                bool same = false;
                try { std::string rebuilt = semtpl::rebuild_reply(t.resp_frag, vals);
                      auto x = semtpl::detail::json_try(a.body), y = semtpl::detail::json_try(rebuilt);
                      same = (x && y) ? semtpl::python_eq(*x, *y) : rebuilt == a.body; }
                catch (const std::exception&) { same = false; }
                if (!same) dyn.reset();                 // -> RESP_RAW below; nothing is guessed
            }
        }
    }"""
if s.count(old) != 1: sys.exit("apply_reply_proof: anchor found %d times" % s.count(old))
open(p, "w").write(s.replace(old, new))
