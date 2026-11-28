#!/bin/bash
# mknode.sh CONTAINER HOSTNAME WAN_IP [NETWORK:IP:IFNAME ...] -- sandbox only: the October harness's node.sh, plus the
# two things this workspace needs: pip told where the HTTPS proxy relay is, and the proxy's CA bundle trusted.
set -eu
T=/home/claude/clean/docs/cleanup-2026-10/t4a-tools
bash $T/node.sh "$@"
docker cp /root/.ccr/ca-bundle.crt "$1":/etc/ssl/ccr-bundle.crt
docker exec "$1" bash -c 'printf "[global]\ncert = /etc/ssl/ccr-bundle.crt\nproxy = http://172.30.0.1:41789\n" > /etc/pip.conf; echo "pip.conf: proxy relay set"'
