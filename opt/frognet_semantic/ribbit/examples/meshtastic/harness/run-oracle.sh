#!/bin/bash
# run-oracle.sh NAME -- CMD...
# Runs one oracle against stock meshtasticd while capturing all client-API (TCP 4403-4420)
# and OTA (UDP 4403) traffic, then tallies the defined-API surface it exercised.
# Writes: p0/logs/NAME.log  p0/pcap/NAME.pcap  p0/cov/NAME.json  p0/runs.tsv (appended)
set -u
NAME=$1; shift; [ "$1" == "--" ] && shift
P0=/home/claude/mesh/p0
mkdir -p $P0/logs $P0/pcap $P0/cov
tcpdump -i any -s 0 -U -w $P0/pcap/$NAME.pcap 'tcp portrange 4403-4520 or udp port 4403' 2>$P0/logs/$NAME.tcpdump &
TD=$!
sleep 1
T0=$(date +%s.%N)
"$@" 2>&1 | tee $P0/logs/$NAME.log
RC=${PIPESTATUS[0]}
T1=$(date +%s.%N)
sleep 1; kill $TD; wait $TD 2>/dev/null
/home/claude/mesh/venv-mpy/bin/python /home/claude/mesh/tools/apitap.py --ports 4403-4520 $P0/cov/$NAME.json $P0/pcap/$NAME.pcap > $P0/logs/$NAME.apitap
printf "%s\t%s\t%.1f\t%s\n" "$NAME" "$RC" "$(echo "$T1 - $T0" | bc)" "$*" >> $P0/runs.tsv
echo "ORACLE $NAME rc=$RC wall=$(echo "$T1 - $T0" | bc)s"
cat $P0/logs/$NAME.apitap
exit $RC
