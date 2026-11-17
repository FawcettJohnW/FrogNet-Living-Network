#!/bin/bash
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
#
# Kept as it was run in the sandbox (EXPLORATION-LOG.md section 8); its paths are that sandbox's.
# Runs every gate; output of each to terminal summary and to $1/<gate>.log
OUT="${1:-/home/claude/logs/run}"; mkdir -p "$OUT"
export FROGNET_TREE=/home/claude/src/frognet
. /home/claude/handoff/handoff/env.sh
cd "$FROGNET_TREE/opt/frognet_semantic" || exit 1
empty() { $PY -I - <<'PYEOF'
import json, mysql.connector
c=json.load(open('/root/.frogsim/DB_CONFIG.json'))
kw={k:c[k] for k in ('host','user','password') if k in c}
if 'unix_socket' in c: kw['unix_socket']=c['unix_socket']
db=mysql.connector.connect(database='FrogNet', **kw); cur=db.cursor()
for t in ('frognet_request_templates','frognet_response_templates','SemCacheDaemon','SemCacheProxy'):
    try: cur.execute('DELETE FROM `%s`'%t)
    except Exception as e: print('empty',t,e)
db.commit()
PYEOF
}
run() { name="$1"; shift; empty; timeout 900 "$@" > "$OUT/$name.log" 2>&1; rc=$?; echo "rc=$rc $name :: $(grep -v '^\s*$' "$OUT/$name.log" | tail -1 | cut -c1-150)"; }
run layout   $PY core/test_homa_layout_oracle.py /home/claude/src/homa
run sim      $PY core/test_homa_sim_oracle.py
run liveness $PY core/test_homa_liveness_oracle.py
run ordering $PY proxy/test_homa_ordering_oracle.py
for t in test_fanout_wire_order_oracle test_no_fallback_oracle test_resp_same_reference_oracle test_worker_liveness_oracle test_connect_retire_oracle test_channel_sets; do
  run tcp-$t $PY proxy/$t.py
done


run carrier $PY core/test_homa_carrier_oracle.py
run liveness-ram $PY core/test_homa_liveness_ram_oracle.py
run writer-gone $PY daemon/test_writer_gone_oracle.py
