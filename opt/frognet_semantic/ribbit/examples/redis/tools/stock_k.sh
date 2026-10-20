#!/bin/bash
# stock_k.sh K -- stock redis-server, persistence off; K redis-benchmark processes -c 50/K -P 1 incr at once, N in all
K=$1; N=${N:-180000}
P=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])')
/srv/redis-7.2.11/src/redis-server --port $P --save "" --appendonly no > /dev/null 2>&1 & SP=$!
for i in $(seq 1 200); do redis-cli -p $P ping >/dev/null 2>&1 && break; sleep 0.05; done
ticks() { awk '{print $14+$15}' /proc/$1/stat; }
s0=$(ticks $SP); t0=$(date +%s.%N)
BP=(); for k in $(seq 1 $K); do redis-benchmark -p $P -c $((50 / K)) -P 1 -n $((N / K)) -t incr -q > /dev/null 2>&1 & BP+=($!); done
wait "${BP[@]}"
t1=$(date +%s.%N); s1=$(ticks $SP); HZ=$(getconf CLK_TCK)
python3 -c "
N=$N; el=$t1-$t0; s=($s1-$s0)/$HZ*1e6/N
print('%-14s K=%d  %7.0f req/s   CPU us per command: server %5.1f' % ('stock', $K, N/el, s))"
kill $SP; wait 2>/dev/null
