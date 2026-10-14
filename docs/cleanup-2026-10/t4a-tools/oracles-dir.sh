# oracles-dir.sh TREE -- run opt/frognet_semantic/oracles/* against TREE's own files (T4b; sandbox harness)
T=${1:?tree}; O=$T/opt/frognet_semantic/oracles
r() { echo "== $1"; shift; timeout 180 "$@" 2>&1 | tail -3; echo "   rc=${PIPESTATUS[0]}"; }
r default_route_resolver bash $O/oracle_default_route_resolver.sh $T/usr/local/bin/manageResolv.bash
r dhcp_scope bash $O/oracle_dhcp_scope.sh $T/usr/local/bin/frognet_dhcp_scope.sh $T/etc/dnsmasq_conf_template
r exit_gw_dot1 python3 $O/oracle_exit_gw_dot1.py
r identity_fails_loud bash $O/oracle_identity_fails_loud.sh $T
r mapinterfaces_names bash $O/oracle_mapinterfaces_names.sh $T/usr/local/bin/mapInterfaces
r opts_only_interface_lines bash $O/oracle_opts_only_interface_lines.sh $T
r routing_topology python3 $O/oracle_routing_topology.py
r self_identity_lines python3 $O/oracle_self_identity_lines.py
