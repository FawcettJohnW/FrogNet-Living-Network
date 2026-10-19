Redis on the memory (boxes, DESIGN.md) acceptance, beside stock Redis 7.2.11 -- one package for every machine type (it builds where it runs).

  cd / && tar xvzf - < /tmp/redis-ribbit-accept.tgz
  /opt/redis-ribbit-accept/run.sh build
  /opt/redis-ribbit-accept/run.sh node                        # everything, this machine
  STAGES="bench scale" /opt/redis-ribbit-accept/run.sh node   # throughput only

Across machines (IPs only, no names):
  host machine:   /opt/redis-ribbit-accept/run.sh lan host
  each other:     /opt/redis-ribbit-accept/run.sh lan node <HOST_IP> <NAME>
  host machine:   /opt/redis-ribbit-accept/run.sh lan coordinate <number of nodes>

Ports 7490 (the memory), 7491 (stock), 7492 (a box) must be reachable on the host machine.
The region source and its documents (ACCEPTANCE.md, SUPPORTED-CONTRACT.md) are in ribbit/examples/redis/.
