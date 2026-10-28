#!/bin/bash
# fdf-services.sh start|stop|status -- the droplet's services, together:
#   fdf     FD&F's RAM server            port 8840   (fdf-ram-server.sh)
#   kart    the kart memory and service  port 8799   (fdf-kart-ram.sh, as the systemd unit fdf-kart)
#   comms   the Communicator's memory    port 8800   (fdf-comms.sh, from COMMS_BIN)
#           and media server             port 8994
# start: each in the background, its output still to THIS terminal (no log files), each in its own process group,
#        whose id is kept in /opt/fdf/var/<name>.pgid. stop: ends each group (the service and anything it started).
# COMMS_BIN: the Communicator's build directory (default /opt/communicator/build).
set -u; V=/opt/fdf/var; mkdir -p $V; B=/opt/fdf/bin
export COMMS_BIN=${COMMS_BIN:-/opt/communicator/build}
declare -A CMD=( [fdf]="$B/fdf-ram-server.sh" [kart]="$B/fdf-kart-ram.sh" [comms]="$B/fdf-comms.sh" )
declare -A PORTS=( [fdf]="8840" [kart]="8799" [comms]="8800 8994" )
ORDER="fdf kart comms"
up() { ss -ltn 2>/dev/null | grep -q ":$1 "; }
running() { [ -f $V/$1.pgid ] && kill -0 -- -"$(cat $V/$1.pgid)" 2>/dev/null; }
# [KART_SERVICE_UNIT_V1] kart runs as the systemd unit fdf-kart (journalctl -u fdf-kart -f): started from an ssh session,
# its Python service died when the session closed. A process group left by an earlier fdf-services.sh is stopped first.
kart_unit() { case $1 in
  start) if running kart; then kill -- -"$(cat $V/kart.pgid)" 2>/dev/null; sleep 1; fi; rm -f $V/kart.pgid
         systemctl daemon-reload && systemctl enable --now fdf-kart >/dev/null 2>&1 && echo "kart: started (systemd unit fdf-kart; its log: journalctl -u fdf-kart -f)" || echo "kart: NOT started: $(systemctl status fdf-kart --no-pager 2>&1 | tail -3)" ;;
  stop)  systemctl stop fdf-kart 2>/dev/null; if running kart; then kill -- -"$(cat $V/kart.pgid)" 2>/dev/null; fi; rm -f $V/kart.pgid; echo "kart: stopped" ;;
esac; }
case "${1:-}" in
start)
    for s in $ORDER; do
        if [ $s = kart ]; then kart_unit start; continue; fi
        if running $s; then echo "$s: already running"; continue; fi
        busy=""; for p in ${PORTS[$s]}; do up $p && busy="$busy $p"; done
        if [ -n "$busy" ]; then echo "$s: NOT started -- port(s)$busy already in use by: $(ss -ltnp | grep -E "$(echo $busy | sed 's/ /|:/g;s/^/:/')" | grep -o 'users:(("[^"]*' | cut -d'"' -f2 | sort -u | tr '\n' ' ')"; continue; fi
        setsid ${CMD[$s]} < /dev/null & echo $! > $V/$s.pgid
    done
    sleep 2
    # [FDF_WHERE_V1] tell the FD&F region where the race and the Communicator are: players read these, and are given
    # only the FD&F RAM server's address. FDF_PUBLIC: this machine's address as players reach it (default: the
    # source address of its default route -- a droplet's public address).
    PUB=${FDF_PUBLIC:-$(ip -4 route get 1.1.1.1 2>/dev/null | grep -o 'src [0-9.]*' | cut -d' ' -f2)}
    A="http://127.0.0.1:8840/FawcettInnovations.RINA.ram_interface.php?op=write"
    if [ -n "$PUB" ] && up 8840; then
        curl -s -m 3 -X POST -d "{\"service\":\"fdf\",\"variable\":\"where\",\"instance\":\"race\",\"bag\":{\"host\":\"$PUB\",\"port\":8799}}" "$A" > /dev/null &&
        curl -s -m 3 -X POST -d "{\"service\":\"fdf\",\"variable\":\"where\",\"instance\":\"comms\",\"bag\":{\"ram\":\"$PUB:8800\",\"media\":\"$PUB:8994\"}}" "$A" > /dev/null &&
        echo "published in the FD&F region: the race at $PUB:8799, the Communicator at $PUB:8800 / $PUB:8994" ||
        echo "could NOT publish the locations in the FD&F region (is it answering on 8840?)"
    else echo "could NOT publish the locations: no public address (set FDF_PUBLIC) or FD&F is not up on 8840"; fi
    "$0" status ;;
stop)
    for s in $ORDER; do
        if [ $s = kart ]; then kart_unit stop; continue; fi
        if running $s; then kill -- -"$(cat $V/$s.pgid)" 2>/dev/null; echo "$s: stopped"; else echo "$s: not running"; fi
        rm -f $V/$s.pgid
    done ;;
status)
    for s in $ORDER; do
        st=""; for p in ${PORTS[$s]}; do
            who=$(ss -ltnp 2>/dev/null | grep ":$p " | grep -o 'users:(("[^"]*' | head -1 | cut -d'"' -f2)
            if [ -n "$who" ]; then st="$st $p:$who"; else st="$st $p:DOWN"; fi; done
        [ $s = kart ] && st="$st  service:$(pgrep -f /opt/fdf/kart/kart_service.py >/dev/null && echo running || echo DOWN) unit:$(systemctl is-active fdf-kart 2>/dev/null)"
        echo "$s:$st"
    done ;;
*) echo "usage: fdf-services.sh start|stop|status"; exit 2 ;;
esac
