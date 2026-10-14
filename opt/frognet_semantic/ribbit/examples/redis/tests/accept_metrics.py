#!/usr/bin/env python3
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
"""accept_metrics.py -- lines of code and cyclomatic complexity: stock Redis 7.2.11's server (src/*.c, *.h; deps/ such
as Lua, jemalloc and hiredis excluded) beside the Redis region (src/ and the vendored platform it builds on, counted
separately). Counted by lizard: NLOC = lines with code (no blanks, no comments); CCN = McCabe cyclomatic complexity
per function.

Stock is grouped by what each file does, so the reader can see which machinery the region has no counterpart for.
Usage: accept_metrics.py <redis-src> <region-root>"""
import fnmatch, glob, os, sys
import lizard

RSRC, ROOT = sys.argv[1], sys.argv[2]

# stock src/ file -> group. First match wins; anything unmatched is "other".
GROUPS = [
    ("replication",          ["replication.c"]),
    ("cluster, sentinel",    ["cluster*.c", "cluster*.h", "sentinel.c"]),
    ("persistence",          ["rdb*.c", "rdb*.h", "aof.c", "rio.c", "rio.h", "redis-check-*.c", "childinfo.c", "syncio.c"]),
    ("scripting, functions", ["script*.c", "script*.h", "eval.c", "function*.c", "function*.h"]),
    ("modules API",          ["module.c", "redismodule.h"]),
    ("networking, event loop, TLS", ["networking.c", "ae*.c", "ae*.h", "anet.c", "anet.h", "connection.c", "connection.h",
                              "socket.c", "unix.c", "tls.c", "connhelpers.h"]),
    ("clients (cli, benchmark)", ["redis-cli.c", "redis-benchmark.c", "cli_common.*", "cli_commands.*"]),
    ("command code (t_*, geo, hll, sort, expire, db, multi, pubsub...)",
                             ["t_*.c", "bitops.c", "geo*.c", "geo*.h", "hyperloglog.c", "sort.c", "expire.c", "db.c",
                              "multi.c", "blocked.c", "pubsub.c", "tracking.c", "notify.c", "lazyfree.c", "evict.c"]),
    ("data structures",      ["ziplist.*", "listpack*", "quicklist.*", "dict.*", "intset.*", "sds*", "adlist.*", "rax*",
                              "zipmap.*", "stream.h", "zmalloc.*", "object.c", "kvstore.*"]),
    ("server core, config, commands table", ["server.c", "server.h", "config.c", "config.h", "commands.c", "commands.def",
                              "acl.c", "debug.c", "latency.*", "slowlog.*", "monotonic.*", "util.*", "localtime.c"]),
]

def group(name):
    for g, pats in GROUPS:
        if any(fnmatch.fnmatch(name, p) for p in pats): return g
    return "other"

def measure(files):
    nloc = funcs = ccn_sum = ccn_max = over10 = 0
    for f in files:
        try: a = lizard.analyze_file(f)
        except Exception as e: print("cannot analyse %s: %s" % (f, e), file=sys.stderr); continue
        nloc += a.nloc
        for fn in a.function_list:
            funcs += 1; ccn_sum += fn.cyclomatic_complexity; ccn_max = max(ccn_max, fn.cyclomatic_complexity)
            over10 += fn.cyclomatic_complexity > 10
    return nloc, funcs, ccn_sum, ccn_max, over10

def row(label, files):
    n, f, s, m, o = measure(files)
    print("  %-62s %6d files %8d NLOC %6d functions  CCN total %6d  max %4d  >10: %4d" % (label, len(files), n, f, s, m, o))
    return n, f, s

stock = sorted(p for p in glob.glob(os.path.join(RSRC, "src", "*.[ch]")) if os.path.isfile(p))
print("stock Redis 7.2.11 server, src/ (deps/ excluded)")
by = {}
for p in stock: by.setdefault(group(os.path.basename(p)), []).append(p)
# Not counted in the server totals: the client programs are not the server. Not in the matched total: what the region
# does not implement (Lua scripting and functions, the modules API), so the matched total compares like with like.
NOT_SERVER = {"clients (cli, benchmark)"}; NOT_IN_REGION = {"scripting, functions", "modules API"}
tot = [0, 0, 0]; matched = [0, 0, 0]
for g, _ in GROUPS + [("other", [])]:
    if g in by:
        n, f, s = row(g, by[g])
        if g in NOT_SERVER: continue
        tot = [tot[0] + n, tot[1] + f, tot[2] + s]
        if g not in NOT_IN_REGION: matched = [matched[0] + n, matched[1] + f, matched[2] + s]
print("  %-62s %8d NLOC %6d functions  CCN total %6d" % ("TOTAL server (clients excluded)", *tot))
print("  %-62s %8d NLOC %6d functions  CCN total %6d" % ("MATCHED (also without scripting and modules, which the region lacks)", *matched))

def tree(d, exts=(".cpp", ".hpp", ".h", ".cc", ".c")):
    return sorted(os.path.join(r, x) for r, _, fs in os.walk(d) for x in fs if x.endswith(exts))
print("Redis region")
a = row("the region: commands, data model, memory, front (src/)", tree(os.path.join(ROOT, "src")))
b = row("the vendored Ribbit platform it builds on (platform/)", tree(os.path.join(ROOT, "platform")))
print("  %-62s %8d NLOC %6d functions  CCN total %6d" % ("TOTAL", a[0] + b[0], a[1] + b[1], a[2] + b[2]))
