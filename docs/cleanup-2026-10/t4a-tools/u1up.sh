cd /root/FrogNet && (setsid bash usr/local/bin/installer/frognet_install.sh --from-repo --preserve --db-pass testpw --no-ssid-projection --no-reboot > /root/install.out 2>&1; echo "EXIT $?" >> /root/install.out) < /dev/null > /dev/null 2>&1 &
echo "started upgrade (--preserve)"
