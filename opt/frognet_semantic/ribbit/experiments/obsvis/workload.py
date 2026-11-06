# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
#
# workload.py -- the seeded schedule, the same for every condition: who follows whom, and each producer's sends.
#
#   follows(seed)            {consumer: [3 producers it follows]}, drawn from the seed: 8 * 3 = 24 true edges of 56
#   schedule(seed, p, kind, t0, dur)   [(t, msg id, payload text)] -- chat: Poisson, mean 5 s, 40..400 bytes;
#                                      telemetry: every 1 s, 32 bytes, unchanged from the last half the time
#                                      (an unchanged write carries the same id and text: the same bytes)
import random, string

PARTS = ["p%d" % i for i in range(1, 9)]


def follows(seed):
    r = random.Random("follows-%d" % seed)
    return {c: sorted(r.sample([p for p in PARTS if p != c], 3)) for c in PARTS}


def followers(seed):
    f = follows(seed)
    return {p: sorted(c for c in PARTS if p in f[c]) for p in PARTS}


def _text(r, n):
    return "".join(r.choice(string.ascii_letters + string.digits + " ") for _ in range(n))


def schedule(seed, p, kind, t0, dur):
    r = random.Random("%s-%d-%s" % (kind, seed, p))
    out, n = [], 0
    if kind == "chat":
        t = t0 + r.expovariate(1 / 5.0)
        while t < t0 + dur:
            out.append((t, "%s-%d" % (p, n), _text(r, r.randint(40, 400)))); n += 1
            t += r.expovariate(1 / 5.0)
    else:
        t, last = t0 + r.random(), None
        while t < t0 + dur:
            if last is None or r.random() >= 0.5:       # changed: a new value, a new id
                last = ("%s-%d" % (p, n), _text(r, 32)); n += 1
            out.append((t, last[0], last[1]))           # unchanged: the same value and id, written again
            t += 1.0
    return out
