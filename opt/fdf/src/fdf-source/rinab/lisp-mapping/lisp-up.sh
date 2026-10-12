#!/bin/bash
# lisp-up.sh LISPERS_LISP_DIR -- unmodified lispers.net (manual mode) as the FD&F mapping system: core, map-server,
# map-resolver, with lisp-mapping/lisp.config (one site, the area name 'rina-area.test', force-proxy-reply).
# run-lispers.py only selects lispers.net's own IPv4-only listen path (lisp_is_raspbian) for kernels without IPv6.
# Stop with lisp-down.sh.
L=${1:?usage: lisp-up.sh /path/to/lispers.net/lisp}; H=$(cd "$(dirname "$0")" && pwd)
cp "$H/lisp.config" "$L/lisp.config"
# [LISPERS_LOGS_START_EMPTY_V1] lispers.net writes logs/lisp-<name>.log only if that file does not exist at start
# (lisp_process_logfile); a leftover log makes it log to stdout instead, and experiments read logs/lisp-ms.log
cd "$L" && mkdir -p logs && rm -f logs/*
setsid python3 "$H/run-lispers.py" "$L/lisp-core.py" 8080 > logs/core.out 2>&1 < /dev/null &
sleep 3
setsid python3 "$H/run-lispers.py" "$L/lisp-ms.py" > logs/ms.out 2>&1 < /dev/null &
setsid python3 "$H/run-lispers.py" "$L/lisp-mr.py" > logs/mr.out 2>&1 < /dev/null &
sleep 5; ss -ulnp | grep -q ":4342 " || { echo "lisp-up: lispers.net is not listening on 4342" >&2; exit 1; }
