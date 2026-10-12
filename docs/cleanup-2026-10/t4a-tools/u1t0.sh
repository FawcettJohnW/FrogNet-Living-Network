# the T0 baseline (42465dd) install; its fresh-box failures are compensated here (the T0 audit's own list):
# the -dev packages mysqlclient/NetfilterQueue need are put on first, as John's boxes had them.
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq libmariadb-dev libnetfilter-queue-dev pkg-config build-essential python3-dev > /root/pre.log 2>&1 || echo "pre FAILED"
umask 022
cd /root/FrogNet-T0 && (printf "n\n" | setsid bash usr/local/bin/installer/frognet_install.sh --from-repo --name U1 --ip 10.63.63.1 --db-pass testpw --no-ssid-projection --broker-host 192.0.2.1 --broker-port 8443 --broker-scheme http --pond testpond > /root/install-t0.out 2>&1; echo "EXIT $?" >> /root/install-t0.out) > /dev/null 2>&1 &
echo "started T0 install"
