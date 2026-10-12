# region-state.sh -- inside a region member: key, hub link, routes, ping the other node's .1 across the hub
echo "== $(hostname)  $(tr '\n' ' ' < /opt/fdf/BUILD)"
journalctl -b -u frognet-tunnel-daemon-v3 --no-pager -o cat | grep -E "Starting frognet-tunnel-daemon|WG_KEY" | tail -2
journalctl -u frognet-tunnel-daemon-v3 --no-pager -o cat | grep -m1 "WG_KEY: made"
echo "key: $(ls -l /var/lib/frognet-tunnel/node_private.key | awk '{print $1}') pub $(cat /var/lib/frognet-tunnel/node_public.key)"
for u in fdf-client fdf-facts frognet-tunnel-daemon-v3; do printf "  %-26s %s\n" $u "$(systemctl is-active $u)"; done
wg show 2>/dev/null | grep -E "^interface|endpoint|allowed|handshake" | sed 's/^/  /'
echo "routes: $(ip -4 route | grep -E 'wg|10\.8[12]' | tr '\n' ';')"
P=${1:?peer .1}; ping -c 3 -W 2 -I "$(ip -4 -o addr show eth1 | awk '{print $4}' | cut -d/ -f1 | head -1)" "$P" | tail -2
