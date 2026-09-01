Oracles for the changes in this tarball. Each was proven RED on the code as it
stood and GREEN after the change, in that order.

  oracle_dhcp_scope.sh
      Pins /etc/dnsmasq.d/opts_only.conf against /etc/dnsmasq_conf_template:
      every non-interface template line present, substituted, in order; the
      interface block computed per the rules (eth0 by presence; a wlan only
      while hosting hostapd; everything else excluded); truncates rather than
      appends; refuses rather than writing an unscoped config.

      Run:  /opt/frognet_semantic/oracles/oracle_dhcp_scope.sh \
                /usr/local/bin/frognet_dhcp_scope.sh \
                /etc/dnsmasq_conf_template

      Fakes /sys/class/net, ip, systemctl and mapInterfaces in a temp dir.
      Touches nothing on the box.

  oracle_forwarders.py
      Differential test: extracts the real awk out of
      mergeHostsAndResolv.bash stage_dnsmasq, runs it through sort -u exactly
      as the bash did, and byte-compares against live.py:_write_forwarders for
      several host tables. Also checks idempotence and that a table with no
      FrogNetHost lines writes nothing.

      Run:  cd /opt/frognet_semantic && python3 oracles/oracle_forwarders.py
      Edit SRC/BASH at the top if the paths differ.

  oracle_mapinterfaces_names.sh
      Pins eth0Name / wlan0Name / wlan1Name to DETECTED devices rather than the
      three hard-coded constants. Reproduces the BAMacBook fixture exactly
      (ens9 LAN, enx... USB NIC on an upstream lease, down wlan0, wlx... radio,
      frognet0) and asserts eth0Name=ens9. Also: the served-/24 owner wins; on
      a fresh install the wired dev with no upstream lease wins; overlay and
      tunnel devices are never eth0Name; the hostapd radio is wlan0Name
      whatever it is called; wireless is decided by the kernel, not by name.

      Run:  /opt/frognet_semantic/oracles/oracle_mapinterfaces_names.sh \
                /usr/local/bin/mapInterfaces

      Fakes /sys/class/net and ip in a temp dir. Touches nothing on the box.
