#!/bin/bash
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
#
# Kept as it was run in the sandbox (EXPLORATION-LOG.md section 8); its paths are that sandbox's.
# ordmode.sh <mode> [seconds]: one mode of the ordering oracle with the gates' environment and empty template tables
export FROGNET_TREE=/home/claude/src/frognet
. /home/claude/handoff/handoff/env.sh
cd "$FROGNET_TREE/opt/frognet_semantic" || exit 1
$PY -I - <<'PYEOF'
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
mkdir -p /home/claude/logs/pieces
timeout ${2:-300} $PY proxy/test_homa_ordering_oracle.py "$1" > /home/claude/logs/pieces/ord-$1.log 2>&1; echo rc=$?
grep "^  \[\|^HOMA-ORDERING\|^RESULT\|^Traceback\|Error" /home/claude/logs/pieces/ord-$1.log | cut -c1-260 | tail -40
