#!/bin/bash
# cpu_k.sh BUILD_DIR LABEL K -- K fronts (boxes) on one memory; redis-benchmark -c 50/K -P 1 incr against each at once,
# N requests in all. Prints total requests/s and CPU microseconds per command: all fronts, the memory, everything.
B=$1; L=$2; K=$3; N=${N:-180000}
port() { python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])'; }
M=$(port)
$B/ribbit-redis-host --listen 127.0.0.1:$M > /dev/null 2>&1 & HP=$!
sleep 0.3
FPS=(); PORTS=()
for k in $(seq 1 $K); do
  F=$(port); E=""; [ $k = 1 ] && E="RIBBIT_REDIS_CONFIG_OWNER=1"
  env RIBBIT_RAM=127.0.0.1:$M $E $B/ribbit-redis-front --port $F --save "" > /dev/null 2>&1 & FPS+=($!); PORTS+=($F)
done
for F in "${PORTS[@]}"; do for i in $(seq 1 200); do redis-cli -p $F ping >/dev/null 2>&1 && break; sleep 0.05; done; done
ticks() { awk '{print $14+$15}' /proc/$1/stat; }
sumf() { local t=0; for p in "${FPS[@]}"; do t=$((t + $(ticks $p))); done; echo $t; }
h0=$(ticks $HP); f0=$(sumf); t0=$(date +%s.%N)
BP=(); for F in "${PORTS[@]}"; do redis-benchmark -p $F -c $((50 / K)) -P 1 -n $((N / K)) -t incr -q > /dev/null 2>&1 & BP+=($!); done
wait "${BP[@]}"
t1=$(date +%s.%N); h1=$(ticks $HP); f1=$(sumf); HZ=$(getconf CLK_TCK)
python3 -c "
N=$N; el=$t1-$t0; hz=$HZ; f=($f1-$f0)/hz*1e6/N; h=($h1-$h0)/hz*1e6/N
print('%-14s K=%d  %7.0f req/s   CPU us per command: fronts %5.1f  memory %5.1f  fronts+memory %5.1f' % ('$L', $K, N/el, f, h, f+h))"
kill "${FPS[@]}" $HP; wait 2>/dev/null
