"""Per-operation timing for the conformance suite ([PER_OPERATION_TIMING_V1], John 2026-09-26).

Wraps either adapter -- the control adapter (lispers.net's own API) or the command adapter (ribbit-lisp over RAM) --
and times every call() the tests make: the same operations, in the same order, through each implementation's own
interface. Nothing about either implementation is changed or assumed; the wall clock around call() is the number.
"""
import json
import statistics
import time
from typing import Any, Dict, List


class TimedAdapter:
    def __init__(self, inner, label: str):
        self._inner, self.label = inner, label
        self.samples: Dict[str, List[float]] = {}     # operation -> seconds, successful calls
        self.errors: Dict[str, int] = {}              # operation -> calls that raised

    def capabilities(self):
        return self._inner.capabilities()

    def call(self, operation: str, **kwargs: Any):
        t0 = time.perf_counter()
        try:
            r = self._inner.call(operation, **kwargs)
        except BaseException:
            self.errors[operation] = self.errors.get(operation, 0) + 1
            raise
        self.samples.setdefault(operation, []).append(time.perf_counter() - t0)
        return r

    def __getattr__(self, name):                      # close() and anything else the tests reach for
        return getattr(self._inner, name)

    def table(self) -> List[Dict[str, Any]]:
        rows = []
        for op in sorted(set(self.samples) | set(self.errors)):
            s = sorted(self.samples.get(op, []))
            def q(p):
                return s[min(len(s) - 1, int(round(p * (len(s) - 1))))] * 1000 if s else None
            rows.append({"operation": op, "calls": len(s), "errors": self.errors.get(op, 0),
                         "median_ms": statistics.median(s) * 1000 if s else None,
                         "p90_ms": q(0.90), "p99_ms": q(0.99), "max_ms": s[-1] * 1000 if s else None,
                         "total_ms": sum(s) * 1000})
        return rows

    def write(self, path: str, meta: Dict[str, Any]) -> None:
        with open(path, "w") as f:
            json.dump({"label": self.label, "meta": meta, "operations": self.table()}, f, indent=1)

    def print_table(self, out) -> None:
        f = lambda v: "-" if v is None else "%9.3f" % v
        print("\nper-operation timing, %s (wall clock around each adapter call, ms)" % self.label, file=out)
        print("%-24s %6s %6s %9s %9s %9s %9s %10s" % ("operation", "calls", "errors", "median", "p90", "p99", "max", "total"), file=out)
        for r in self.table():
            print("%-24s %6d %6d %s %s %s %s %10.1f" % (r["operation"], r["calls"], r["errors"], f(r["median_ms"]),
                  f(r["p90_ms"]), f(r["p99_ms"]), f(r["max_ms"]), r["total_ms"]), file=out)
