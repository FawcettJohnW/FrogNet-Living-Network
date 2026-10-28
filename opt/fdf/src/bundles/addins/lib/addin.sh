# addin.sh -- [ADDIN_V1] what every add-in's start sources (bash). An add-in is a stand-alone bundle on top of an FD&F core;
# it uses its OWN RAM for its work, writes its OWN capability tuple to the control RAM, reads the others', and decides
# for itself what a network event means (ADDIN: on-loss=):
#   exit         its servers stop and it stays stopped (until the core starts again: PartOf the core's unit)
#   rediscover   its servers stop, it exits non-zero, and its unit starts it again: the control RAM, the mode and its
#                tuple are found afresh
#   stay         its servers keep running; its tuple is written again when the control RAM is back
# addin_control   the core's view, whatever the machine is:
#                 a core (fdf-core bundle)  $FDF_HOME/var/control.env: FDF_MODE leaf|standalone, FDF_CONTROL_RAM, FDF_NAME
#                 the region's holder       /opt/fdf/hub/hub.env: RINA_RAM; mode region; its member is w:<its name>
#                 a FrogNet node            $FDF_HOME/var/join.env: RINA_RAM; mode node; its facts.json names it
# addin_hold SERVICE key=value...   [ADDIN_HOLDS_ITS_TUPLE_V1] (pond, where, <ns>/<add-in>) {"from":..., SERVICE:{...}}
#                 (self:<port> = this machine by identity), written by `fdf-where --hold` with own:true on its own
#                 session and held: the tuple is that session's, so when the add-in stops, crashes or loses its network
#                 the region writes it {} -- gone with its writer, nothing stale. The held session's end is the network
#                 event; on-loss decides.
H=${FDF_HOME:-/opt/fdf}
AD=$(cd "$(dirname "$0")" && pwd); NAME=$(sed -n 's/^name=//p' "$AD/ADDIN"); ON_LOSS=$(sed -n 's/^on-loss=//p' "$AD/ADDIN")
asay() { echo "fdf-addin-$NAME: $*"; }
HUB_ENV=${FDF_HUB_ENV:-/opt/fdf/hub/hub.env}
addin_control() {
    if [ -s "$H/var/control.env" ]; then . "$H/var/control.env"
    elif [ -s "$HUB_ENV" ]; then
        . "$HUB_ENV"; FDF_MODE=region; FDF_CONTROL_RAM=${RINA_RAM:-}
        FDF_NAME=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("name",""))' "${FDF_FACTS:-/nonexistent}" 2>/dev/null || true)
        [ -n "$FDF_NAME" ] || FDF_NAME=$(hostname -s)
    elif [ -s "$H/var/join.env" ]; then . "$H/var/join.env"; FDF_MODE=node; FDF_CONTROL_RAM=${RINA_RAM:-}; FDF_NAME=$(hostname)
    else asay "no FD&F core on this machine ($H/var/control.env, $HUB_ENV or $H/var/join.env): install the core first"; exit 1; fi
    [ -n "${FDF_CONTROL_RAM:-}" ] || { asay "the core names no control RAM"; exit 1; }
    case "$FDF_MODE" in
      leaf|region) WHO="FDF_NS=w:$FDF_NAME" ;;
      standalone)  WHO="FDF_STANDALONE=1" ;;
      node)        WHO="FDF_FACTS=$H/var/facts.json" ;;
      *) asay "unknown mode '$FDF_MODE' in the core's control.env"; exit 1 ;;
    esac
    asay "build $(sed -n 's/^build=//p' "$AD/ADDIN"), mode $FDF_MODE, control RAM $FDF_CONTROL_RAM, on-loss $ON_LOSS"
}
TPL=""; [ -f "$H/templates/fdf.prime" ] && TPL="RIBBIT_TEMPLATES=$H/templates/fdf.prime"
where() { env RINA_RAM="$FDF_CONTROL_RAM" FDF_ADDIN="$NAME" $WHO $TPL "$H/bin/fdf-where" "$@"; }
ADDIN_PIDS=""
addin_child() { ADDIN_PIDS="$ADDIN_PIDS $1"; }
addin_stop() { [ -n "$ADDIN_PIDS" ] && kill $ADDIN_PIDS 2>/dev/null; ADDIN_PIDS=""; }
addin_hold() {      # bash (coproc; a TERM interrupts the read and the trap runs at once)
    svc=$1; shift; HP=""
    trap '[ -n "$HP" ] && kill $HP 2>/dev/null; addin_stop; exit 0' TERM INT
    while :; do
        coproc HOLD { exec env RINA_RAM="$FDF_CONTROL_RAM" FDF_ADDIN="$NAME" $WHO $TPL "$H/bin/fdf-where" --hold "$svc" "$@" 2>&1; }
        HP=$HOLD_PID                                      # bash unsets HOLD_PID when it ends
        while IFS= read -r ln <&"${HOLD[0]}"; do
            case "$ln" in held*|*"written again"*|fdf-where:*) asay "$ln" ;; esac
        done
        wait $HP 2>/dev/null || true; HP=""
        case "$ON_LOSS" in
          exit)       asay "network event: the control RAM session ended; on-loss exit: stopping"; addin_stop; exit 0 ;;
          rediscover) asay "network event: the control RAM session ended; on-loss rediscover: stopping, the unit starts me again"; addin_stop; exit 75 ;;
          *)          asay "network event: the control RAM session ended; on-loss stay: the servers keep running, back when it is"; sleep 2 ;;
        esac
    done
}
