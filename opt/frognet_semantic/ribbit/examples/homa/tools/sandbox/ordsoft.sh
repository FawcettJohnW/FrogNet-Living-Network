#!/bin/bash
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
#
# Kept as it was run in the sandbox (EXPLORATION-LOG.md section 8); its paths are that sandbox's.
# ordsoft.sh: the ordering oracle's soft mode alone, with the gates' environment and empty template tables
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
H=$PWD/ribbit/examples/homa; $H/build/softhoma-ram 127.0.0.1:9151 >/home/claude/logs/fast/oram.log 2>&1 & RAM=$!; sleep 0.7
LD_PRELOAD=$H/build/libsofthoma.so SOFTHOMA_RAM=127.0.0.1:9151 SOFTHOMA_IP=10.77.77.2 timeout ${1:-240} $PY proxy/test_homa_ordering_oracle.py soft > /home/claude/logs/fast/ord-soft.log 2>&1; echo rc=$?; kill $RAM
grep "^  \[\|^HOMA-ORDERING\|^RESULT\|^Traceback\|Error\|GONE" /home/claude/logs/fast/ord-soft.log | cut -c1-250 | tail -26
