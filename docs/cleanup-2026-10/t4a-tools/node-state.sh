# node-state.sh -- run inside a node: what the gates read (services, merge, getHosts, echo, wg, DNS)
echo "hostname: $(hostname)   build lines:"; journalctl -b --no-pager -o cat 2>/dev/null | grep -E "Starting frognet-tunnel-daemon v3|LAN-only loop started|Region mode|REGION switch" | tail -3 | sed 's/^/  /'
for u in mariadb apache2 dnsmasq NetworkManager cron frognet-proxy frognet-daemon frognet-merge-watcher frognet-tunnel-daemon-v3 frognet-eth0-fixup frognet-sysperf frognet-dbhost-elect.timer fdf-client fdf-facts; do
  printf "  %-30s %s/%s\n" "$u" "$(systemctl is-enabled $u 2>/dev/null)" "$(systemctl is-active $u 2>/dev/null)"; done
echo "failed units: $(systemctl --failed --no-legend --plain | awk '{print $1}' | tr '\n' ' ')"
echo "merge: $(ls /etc/sentinels/discovered 2>/dev/null && echo discovered) last: $(journalctl -b --no-pager -o cat | grep -E 'runMerge.*(END|DONE|complete)|MERGE_DONE' | tail -1)"
echo "databasehost: $(cat /etc/database_ip 2>/dev/null) / $(getent hosts databasehost.frognet | awk '{print $1}')"
echo "getHosts.php: $(curl -s --max-time 5 http://127.0.0.1:8080/getHosts.php)"
echo "echo (:8080): $(curl -s --max-time 5 http://127.0.0.1:8080/frognet_echo.php)"
echo "echo (:80, the proxy): $(curl -s --max-time 5 http://127.0.0.1/frognet_echo.php)"
echo "wg interfaces: $(ip -o link show type wireguard 2>/dev/null | awk -F': ' '{print $2}' | tr '\n' ' ')$(wg show interfaces 2>/dev/null)"
echo "routes: $(ip -4 route | grep -E '^10\.' | tr '\n' ';')"
echo "resolv.conf: $(grep -v '^#' /etc/resolv.conf | tr '\n' ' ')"
