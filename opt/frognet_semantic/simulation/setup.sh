#!/bin/bash
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#                                                              #
#  SPDX-License-Identifier: GPL-2.0-only                       #
#                                                              #
#  This program is free software; you can redistribute it      #
#  and/or modify it under the terms of the GNU General Public  #
#  License as published by the Free Software Foundation;       #
#  version 2 of the License, and no other version.             #
#                                                              #
#  This program is distributed in the hope that it will be     #
#  useful, but WITHOUT ANY WARRANTY; without even the implied  #
#  warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR     #
#  PURPOSE.  See the GNU General Public License for details.   #
#                                                              #
#  See COPYRIGHT and LICENSE at the root of this tree.         #
################################################################
# setup.sh -- [SIM_SETUP_V1] make a clean Linux machine (Ubuntu 24.04 is what it is run on) ready to run the simulator
# and the gates from this checkout. It looks first, says what is missing, asks once, then does it all itself.
#
#   bash opt/frognet_semantic/simulation/setup.sh            the simulator and the full gate, but the Ribbit-LISP tier
#   bash opt/frognet_semantic/simulation/setup.sh --lisp     also the Ribbit-LISP tier (lispers.net itself you bring)
#   bash opt/frognet_semantic/simulation/setup.sh --netns    also the network-namespace tests (wireguard-go, built)
#   --yes                                                    do not ask (an unattended machine)
#
# What it makes, and where. Nothing goes into /usr/local or /opt, and nothing is pip-installed into the system Python:
#   apt packages            the build's libraries, python3-venv, MariaDB, php-cli, curl, ping, WireGuard tools, tcsh
#   ~/.frogsim/venv         the simulator's Python (from /usr/bin/python3) with the modules the engine imports
#   ~/.frogsim/DB_CONFIG.json  the simulator's MariaDB user FrogUser with a password made here; databases FrogNet and
#                           FrogNet7 (the door tier's two template stores). sim_env.py points the simulator at it.
#   rinab/build/static      the CroakOS binaries, built if they are not there
#   --lisp: ~/.frogsim/lisp-py312  a Python 3.12 with lispers.net's modules (its .pyc files are 3.12's), and the
#                           Ribbit-LISP package built (make front ram). lispers.net 0.643 is Dino Farinacci's and is not
#                           in this tree: unpack it in /opt/lispers.net-0.643, or set LISPERS_DIR. It also needs IPv6.
#   --netns: rinab/build/wg/wireguard-go-<arch>, built from source (golang-go, git)
# Then it says the two commands to run. FROGSIM_HOME moves ~/.frogsim.
set -e -o pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; FS="$(dirname "$HERE")"; ROOT="$(cd "$FS/../.." && pwd)"
RINAB="$FS/../fdf/src/fdf-source/rinab"; LISP_PKG="$FS/ribbit/examples/lispers.net"
FROGSIM_HOME=${FROGSIM_HOME:-$HOME/.frogsim}; VENV=$FROGSIM_HOME/venv; LISPV=$FROGSIM_HOME/lisp-py312
LISP=0; NETNS=0; YES=0
for a in "$@"; do case "$a" in --lisp) LISP=1;; --netns) NETNS=1;; --yes|-y) YES=1;;
  -h|--help) sed -n '19,40p' "$0"; exit 0;; *) echo "setup.sh: unknown option $a (--lisp, --netns, --yes)"; exit 2;; esac; done
SUDO=""; [ "$(id -u)" = 0 ] || SUDO=sudo
say() { echo "== $*"; }

# ---- what is missing
PKGS="g++ make file liblz4-dev libssl-dev libjemalloc-dev python3 python3-venv python3-dev mariadb-server mariadb-client
      php-cli curl iputils-ping iproute2 wireguard-tools"
[ $LISP = 1 ] && PKGS="$PKGS tcsh python3.12 python3.12-venv libffi-dev libpcap-dev"
[ $NETNS = 1 ] && PKGS="$PKGS golang-go git iptables"
NEED=""; for p in $PKGS; do dpkg-query -W -f='${Status}' $p 2>/dev/null | grep -q "install ok installed" || NEED="$NEED $p"; done
SIM_MODS="lz4 mysql-connector-python lxml numpy scipy flask PyYAML"
LISP_MODS="cheroot bottle netifaces pyOpenSSL pycryptodome requests curve25519-donna geopy ecdsa future distro"
TODO=""
[ -n "$NEED" ] && TODO="$TODO\n  apt-get install$NEED"
[ -x $VENV/bin/python3 ] || TODO="$TODO\n  the simulator's Python: $VENV ($SIM_MODS)"
[ -s $FROGSIM_HOME/DB_CONFIG.json ] || TODO="$TODO\n  MariaDB: user FrogUser, databases FrogNet and FrogNet7; its credential in $FROGSIM_HOME/DB_CONFIG.json"
[ -x $RINAB/build/static/fdf-ram ] || TODO="$TODO\n  build the CroakOS binaries (a few minutes)"
if [ $LISP = 1 ]; then
  [ -x $LISPV/bin/python ] || TODO="$TODO\n  lispers.net's Python 3.12: $LISPV ($LISP_MODS)"
  [ -x $LISP_PKG/ribbit_cpp/lisper-ram ] || TODO="$TODO\n  build the Ribbit-LISP package (make front ram, about two minutes)"
fi
[ $NETNS = 1 ] && { [ -x $RINAB/build/wg/wireguard-go-$(uname -m) ] || TODO="$TODO\n  build wireguard-go from source"; }
if [ -z "$TODO" ]; then say "nothing to do: this machine is set up"
else
  printf "This machine needs:$TODO\n"
  if [ $YES = 0 ]; then printf "Do it now? [y/N] "; read -r ans < /dev/tty || ans=""; case "$ans" in y|Y|yes|YES) ;; *) echo "nothing done"; exit 1;; esac; fi
fi

# ---- packages
if [ -n "$NEED" ]; then say "apt-get install$NEED"; $SUDO apt-get update -q; DEBIAN_FRONTEND=noninteractive $SUDO apt-get install -y -q $NEED; fi

# ---- the simulator's Python: from /usr/bin/python3, so the netns tests (which bind the tree over /usr/local) keep it
if [ ! -x $VENV/bin/python3 ]; then say "venv $VENV"; mkdir -p $FROGSIM_HOME; /usr/bin/python3 -m venv $VENV; fi
$VENV/bin/python3 -c "import lz4, mysql.connector, lxml, numpy, scipy, flask, yaml" 2>/dev/null || { say "pip install $SIM_MODS (into $VENV)"; $VENV/bin/pip install -q $SIM_MODS; }

# ---- MariaDB: running, with the simulator's user and databases
if ! $SUDO mysql -u root -e "SELECT 1" >/dev/null 2>&1; then
  say "starting MariaDB"
  if [ "$(ps -o comm= -p 1)" = systemd ]; then $SUDO systemctl start mariadb; else $SUDO service mariadb start; fi
  for i in $(seq 30); do $SUDO mysql -u root -e "SELECT 1" >/dev/null 2>&1 && break; sleep 1; done
  $SUDO mysql -u root -e "SELECT 1" >/dev/null || { echo "setup.sh: MariaDB did not start"; exit 1; }
fi
if [ ! -s $FROGSIM_HOME/DB_CONFIG.json ]; then
  say "MariaDB: FrogUser, FrogNet, FrogNet7; $FROGSIM_HOME/DB_CONFIG.json"
  PW=$(head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 24)
  $SUDO mysql -u root <<SQL
CREATE DATABASE IF NOT EXISTS \`FrogNet\` CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;
CREATE DATABASE IF NOT EXISTS \`FrogNet7\` CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;
CREATE USER IF NOT EXISTS 'FrogUser'@'127.0.0.1' IDENTIFIED BY '$PW';
CREATE USER IF NOT EXISTS 'FrogUser'@'localhost' IDENTIFIED BY '$PW';
ALTER USER 'FrogUser'@'127.0.0.1' IDENTIFIED BY '$PW';
ALTER USER 'FrogUser'@'localhost' IDENTIFIED BY '$PW';
GRANT ALL PRIVILEGES ON \`FrogNet\`.* TO 'FrogUser'@'127.0.0.1', 'FrogUser'@'localhost';
GRANT ALL PRIVILEGES ON \`FrogNet7\`.* TO 'FrogUser'@'127.0.0.1', 'FrogUser'@'localhost';
FLUSH PRIVILEGES;
SQL
  (umask 077; printf '{\n  "host": "127.0.0.1",\n  "user": "FrogUser",\n  "password": "%s",\n  "database": "FrogNet",\n  "port": 3306\n}\n' "$PW" > $FROGSIM_HOME/DB_CONFIG.json)
fi

# ---- the CroakOS binaries
if [ ! -x $RINAB/build/static/fdf-ram ]; then
  say "building the CroakOS binaries"
  (cd $RINAB && bash tools/build.sh && bash tools/build_static_host.sh && bash tools/build_static_clients.sh)
fi

# ---- the Ribbit-LISP tier
if [ $LISP = 1 ]; then
  if [ ! -x $LISPV/bin/python ]; then say "venv $LISPV (Python 3.12)"; /usr/bin/python3.12 -m venv $LISPV; fi
  $LISPV/bin/python -c "import bottle, cheroot, netifaces, OpenSSL, Crypto, curve25519, requests, geopy, ecdsa, future, distro" 2>/dev/null \
    || { say "pip install $LISP_MODS (into $LISPV)"; $LISPV/bin/pip install -q $LISP_MODS; }
  [ -x $LISP_PKG/ribbit_cpp/lisper-ram ] || { say "make front ram ($LISP_PKG)"; make -C $LISP_PKG front ram; }
  L=${LISPERS_DIR:-/opt/lispers.net-0.643}
  [ -f $L/lisp-core.pyc ] || echo "   lispers.net 0.643 is not at $L: unpack it there, or set LISPERS_DIR. It is Dino Farinacci's, not in this tree."
  $LISPV/bin/python -c "import socket; socket.socket(socket.AF_INET6, socket.SOCK_DGRAM).close()" 2>/dev/null \
    || echo "   this kernel has IPv6 disabled; lispers.net listens on :: and will not start here"
fi

# ---- wireguard-go for the netns tests
if [ $NETNS = 1 ] && [ ! -x $RINAB/build/wg/wireguard-go-$(uname -m) ]; then
  say "building wireguard-go"; bash $RINAB/tools/build_wireguard_go.sh
fi

echo
say "ready. From the checkout's root:"
echo "   bash opt/frognet_semantic/simulation/quickstart.sh                 # about a minute: CroakOS forming, splitting, healing"
echo "   $VENV/bin/python3 opt/frognet_semantic/simulation/run_all.py      # the whole gate, about nine minutes"
