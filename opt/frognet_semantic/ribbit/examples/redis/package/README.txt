Redis on the memory (boxes, DESIGN.md) acceptance, beside stock Redis 7.2.11 -- one package for every machine type (it builds where it runs).

  cd / && tar xvzf - < /tmp/redis-ribbit-accept.tgz
  /opt/redis-ribbit-accept/run.sh build
  /opt/redis-ribbit-accept/run.sh node                        # everything, this machine
  STAGES="bench scale" /opt/redis-ribbit-accept/run.sh node   # throughput only

Across machines (IPs only, no names):
  host machine:   /opt/redis-ribbit-accept/run.sh lan host
  each other:     /opt/redis-ribbit-accept/run.sh lan node <HOST_IP> <NAME>
  host machine:   /opt/redis-ribbit-accept/run.sh lan coordinate <number of nodes>    (a second terminal)

The coordinate step runs the cross-machine correctness check on both arms, then the benchmark: every node runs the same
redis-benchmark at the same moment (INCR on one key, SET, GET; -P 1 and -P 16), stock then region, 3 rounds
interleaved, and prints the total req/s of all nodes together with a median table at the end.
  stock:  every node's clients -> stock redis-server on the host machine
  region: every node's clients -> the box on their own machine -> the memory on the host machine
Settings: REQS=200000 (per node per run at -P 1; x5 at -P 16), CLIENTS=50 (per node), REPEAT=3.

Ports 7490 (the memory), 7491 (stock), 7492 (a box) must be reachable on the host machine.
The region source and its documents (ACCEPTANCE.md, SUPPORTED-CONTRACT.md) are in ribbit/examples/redis/.
