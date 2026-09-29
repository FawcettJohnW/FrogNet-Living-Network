# lizard: lispers.net 0.643 vs Ribbit-LISP -- same tool, same rules

lizard 1.17 (`python3 -m lizard <files>`), default settings; "over CCN 15" is lizard's warning count (CCN > 15).

## The control is the frozen 0.643
The 26 `.py` files of the lispers.net source tree (`lisp/`) each compile (Python 3.12, `-O`, as RUN-LISP runs them) to
bytecode identical to the corresponding `.pyc` in `lispers.net-release-py3-0.643.tgz` -- the release every acceptance
and performance run used. 26 of 26 identical, 0 different. (The release's two other `.pyc`, the installer helpers
`lispers.net-test-install` and `provision-lisp`, have no source in `lisp/` and are not part of the running system.)

## Totals

| | files | NLOC | functions | avg CCN | over CCN 15 |
|---|---:|---:|---:|---:|---:|
| lispers.net 0.643, whole implementation | 26 `.py` | 22,527 | 1,139 | 5.9 | 82 |
| lispers.net 0.643, map-server / map-resolver surface (below) | 5 `.py` | ~6,411 | ~370 | 6.27 | 27 |
| Ribbit-LISP v0.60 (frozen, measured) | 19 C++ | 6,271 | 554 | 6.3 | 39 |
| Ribbit-LISP v0.61 (register-path fixes) | 19 C++ | 6,487 | 575 | 6.3 | 41 |

- **Ribbit's 19 files** are `ribbit_cpp/*.hpp *.cpp` without the two generated Unicode tables (`pyuni_tables.hpp`,
  `pyuni_s3_tables.hpp`). This reproduces the reported 6,271 / 554 / 6.3 / 39 exactly. They include machinery that is
  not LISP (FNWP, the semantic wire and codec, templates, the frogram client, the memory, EBR, the data plane); they do
  NOT include `tools/ramsrv/ram_server.cpp` or `tools/lisp_service.cpp`.
- **lispers.net whole** includes what Ribbit-LISP does not implement: the ITR/ETR/RTR data planes, the DDT node, the
  lig/rig/ltr tools, the API client (`lispapi.py`), and its own crypto primitives (`chacha.py`, `poly1305.py`).
- **The surface row** is the part of lispers.net that the Ribbit Map-Server replaces: the functions reachable from its
  map-server and map-resolver entry points (UDP handlers and their `lisp.config` commands) in lisp.py, lisp-ms.py,
  lisp-mr.py, lispconfig.py and lisp-core.py -- the P5 surface inventory's call graph. That call graph is by name:
  312 reachable names match 370 lizard functions, because several classes share method names (`encode`, `decode`,
  ...); the row is therefore approximate, and if anything counts a little too much.

## Largest single functions

| lispers.net 0.643 | CCN | NLOC |
|---|---:|---:|
| `lisp_database_mapping_command` (lispconfig.py) | 88 | 162 |
| `lisp_rtr_data_plane` (lisp-rtr.py) | 78 | 208 |
| `lisp_itr_data_plane` (lisp-itr.py) | 68 | 160 |
| `lisp_ms_show_site_detail_command` (lisp-ms.py) | 65 | 208 |
| `lisp_process_map_reply` (lisp.py) | 65 | 155 |
| `decode` (lisp.py:4763) | 64 | 153 |
| `lisp_site_command` (lisp-ms.py) | 63 | 132 |
| `lisp_process_map_register` (lisp.py) | 60 | 223 |
| `lisp_policy_command` (lisp.py) | 60 | 133 |

| Ribbit-LISP | CCN | NLOC |
|---|---:|---:|
| `Engine::dispatch` v0.60 (dissected in DISPATCH-DISSECTION.md) | 524 | 834 |
| `Engine::dispatch` v0.61 | 548 | 833 |

## Reading it
Python and C++ NLOC are not the same unit (C++ lines carry braces and declarations Python does not), so NLOC across
languages is indicative only; CCN counts decisions in both and compares more directly.
