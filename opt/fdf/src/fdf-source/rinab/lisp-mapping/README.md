# lisp-mapping — unmodified lispers.net as the oracle for FD&F's area mapping

## What this directory is, and what it is not

FD&F names each area with one LISP distinguished-name EID (`[AREA_NAME_IS_A_LISP_EID_V1]`, AFI 17, as lispers.net
encodes it). FD&F's own mapping control path runs **in a region over the session** (`[AREA_MAPPING_IN_THE_REGION_V1]`,
`src/rina_lisp.cpp`), not over UDP. lispers.net is used only at the edge and as the **oracle**: the same registrations
and lookups go to it and to the mapping region, and the answers are compared.

This directory starts that oracle. It contains no LISP implementation of its own.

It is **not** the Ribbit-LISP conversion of lispers.net (the separate project that ported lispers.net's control API
onto Ribbit, with its own conformance suite and test counts). That project is not in this package and none of its
results are claimed here. The `ribbit-lisp` source that ships inside the vendored platform
(`platform/ribbit_cpp/ribbit_lisp.cpp`, `lisp_engine.hpp`, version `v0.63-bounded`) is the platform's, vendored with it;
FD&F does not build or use it.

## The three LISP pieces in rinab, told apart

| file | what | used by FD&F |
|---|---|---|
| `src/rina_lisp.cpp`, `src/lisp_area.hpp` | FD&F's area mapping: `map-site`, `map-register`, `map-resolve` in the mapping region over the session; `register`, `resolve`, `seen` on lispers.net's UDP 4342 wire for edge interop and the oracle | yes (`build/bin/rina-lisp`, built by `tools/build_rlite.sh`) |
| `lisp-mapping/` (here) | starts unmodified lispers.net as Map-Server + Map-Resolver for the oracle checks | tests only |
| `platform/ribbit_cpp/ribbit_lisp.cpp` | the platform's ribbit-lisp program, vendored untouched | no |

## Files

- `LISPERS-COMMIT` — the lispers.net commit these were run against (`0246ef3f754366b40365d74d159d62f655176e6c`).
  lispers.net itself is not included. Clone it and check out that commit.
- `lisp.config` — Map-Server and Map-Resolver only; one site `fdf-areas`, key `fdf-area-key`, EID `'rina-area.test'`,
  `force-proxy-reply`.
- `lisp-up.sh LISPERS_LISP_DIR` — copies `lisp.config` in, empties `logs/` (`[LISPERS_LOGS_START_EMPTY_V1]`), starts
  lisp-core (8080), lisp-ms and lisp-mr, and fails if nothing listens on UDP 4342.
- `lisp-down.sh` — stops them.
- `run-lispers.py` — runs a lispers.net script unchanged, with two run-time settings: `lisp_is_raspbian` returns true,
  which selects lispers.net's own IPv4-only listen path on kernels without IPv6, and its exception hook is disabled so
  real tracebacks show. No lispers.net file is changed.
- `FINDINGS-FOR-DINO.md` — four behaviours of lispers.net found while doing this, offered as reports. lispers.net was
  not changed.

## Run the checks

Needs `build/` (`tools/build.sh`, then `tools/build_rlite.sh RLITE_ROOT` for `rina-lisp`), python3, and a lispers.net checkout at `LISPERS-COMMIT`.

    lisp-mapping/lisp-up.sh /path/to/lispers.net/lisp
    tools/map-oracle-check.sh /path/to/lispers.net/lisp       # every case: both answers printed; exit 0 only if all agree
    tools/map-expiry-compare.sh /path/to/lispers.net/lisp     # seconds until each stops answering an expired name
    lisp-mapping/lisp-down.sh
    tools/map-region-check.sh                                 # the mapping region alone; port 4342 must be free

## Where the results are

`experiments/RESULTS.md`:

- "D5 through LISP" (the area name resolves only through the mapping system);
- "D5: LISP's mapping in a region, over the session";
- "The mapping region against the incumbent, lispers.net" (`[MAPPING_MATCHES_LISPERS_V1]`);
- the NAT traversal and moving-RAM-manager sections.

The `experiments/retired/expD5-*.sh` scripts are the original D5 runs. Their mapping step is superseded by the
region and they are kept for the record. Some RESULTS.md lines cite `evidence/…` files, which are not in this package.
