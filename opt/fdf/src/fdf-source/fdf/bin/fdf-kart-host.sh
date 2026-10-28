#!/bin/bash
# fdf-kart-host.sh KART_MEMORY [FDF_RAM] -- [KART_HOST_AGENT_V1] this machine as a dedicated game-serving machine: it
# tells the kart service (at KART_MEMORY, host:8799) that it can serve a game's memory, and serves the races the
# service gives it (/opt/fdf/kart/kart_host.py --kind dedicated, with /opt/fdf/bin/rina-host). Racers reach it at the
# address this machine announced to FD&F when FDF_RAM (host:8840) is given, else at the address the kart memory sees.
# A race memory listens on a port this machine's system picks: racers must be able to reach this machine on any port.
# Foreground; everything prints to this terminal.
set -eu; K=${1:?usage: fdf-kart-host.sh KART_MEMORY [FDF_RAM]}
exec python3 /opt/fdf/kart/kart_host.py --memory "$K" --kind dedicated --rina-host /opt/fdf/bin/rina-host ${2:+--fdf "$2"}
