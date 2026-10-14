# inst.sh NAME IP [extra installer args...] -- run the tree's installer in the background, output to /root/install.out
N=$1; IP=$2; shift 2
cd /root/FrogNet && (setsid bash usr/local/bin/installer/frognet_install.sh --from-repo --name "$N" --ip "$IP" --db-pass testpw --no-ssid-projection --no-reboot "$@" > /root/install.out 2>&1; echo "EXIT $?" >> /root/install.out) < /dev/null > /dev/null 2>&1 &
echo "started install of $N $IP $*"
