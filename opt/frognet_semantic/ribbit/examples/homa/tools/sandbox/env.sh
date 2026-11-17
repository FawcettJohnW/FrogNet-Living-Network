# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
#
# The sandbox environment the scripts here source (kept as it was).
# Sandbox environment for the engine oracles (the variables simulation/sim_env.py sets).
# Usage:  FROGNET_TREE=/path/to/frognet . env.sh      (after simulation/setup.sh --yes has run)
export FROGNET_TREE="${FROGNET_TREE:-/home/claude/src/frognet}"
export FROGNET_DB_CONFIG=/root/.frogsim/DB_CONFIG.json
export FROGNET_BIN_ROOT="$FROGNET_TREE/usr/local/bin"
export PY=/root/.frogsim/venv/bin/python3
ip addr show lo | grep -q 10.77.77.1 || ip addr add 10.77.77.1/32 dev lo
service mariadb status >/dev/null 2>&1 || service mariadb start >/dev/null 2>&1
