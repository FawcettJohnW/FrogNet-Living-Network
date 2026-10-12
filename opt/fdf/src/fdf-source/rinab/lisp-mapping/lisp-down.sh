#!/bin/bash
# lisp-down.sh -- stop lispers.net started by lisp-up.sh (by pid; this script's own command line does not match)
for p in $(pgrep -f "run-lispers.py"); do kill -9 $p; done
cd /tmp/w/lispers.net/lisp && python3 remove-lisp-locks.py > /dev/null 2>&1; rm -f logs/*
