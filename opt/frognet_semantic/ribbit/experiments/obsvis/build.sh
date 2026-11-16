#!/bin/bash
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
#
# build.sh -- what the experiment runs, built from this tree into build/: chat-ram and libchat.so (the chat example's
# own build), libspace.so (ribbit::Space behind a C ABI), and the apt packages the conditions need.
set -eu
cd "$(dirname "$0")"
R=$(cd ../.. && pwd)
need=""; for c in mosquitto:mosquitto wireguard-go:wireguard-go tcpdump:tcpdump wg:wireguard-tools openssl:openssl ethtool:ethtool; do
  command -v ${c%%:*} >/dev/null || need="$need ${c#*:}"; done
[ -z "$need" ] || { echo "== apt-get install$need"; apt-get install -y -q $need; }
# [OBSVIS_VENV_V1] the experiment's Python modules go in its own venv (build/venv), never the system Python
[ -x build/venv/bin/python ] || { command -v python3 >/dev/null && python3 -m venv --help >/dev/null 2>&1 || { echo "== apt-get install python3-venv"; apt-get install -y -q python3-venv; }; python3 -m venv build/venv; }
build/venv/bin/python -c "import paho.mqtt, sklearn, numpy" 2>/dev/null || { echo "== build/venv: pip install paho-mqtt scikit-learn numpy"; build/venv/bin/pip install -q paho-mqtt scikit-learn numpy; }
[ -x "$R/examples/chat/build/chat-ram" ] && [ "$R/examples/chat/build/chat-ram" -nt "$R/cpp/src/ram_host.cpp" ] || bash "$R/examples/chat/build.sh"
mkdir -p build/obj
# libspace.so: ribbit::Space (the one pub/sub library, the conversions' own) behind a C ABI (space_c.cpp)
F="-std=c++20 -O2 -fPIC -pthread -Wall -Wextra -Werror -Wno-free-nonheap-object -I$R/cpp/include"
for s in frogram ram_host lf_ebr ribbit_space; do
  [ build/obj/$s.o -nt $R/cpp/src/$s.cpp ] || { echo "== $s.o"; g++ $F -c $R/cpp/src/$s.cpp -o build/obj/$s.o & }
done; wait
echo "== libspace.so"; g++ $F -shared space_c.cpp build/obj/ribbit_space.o build/obj/frogram.o build/obj/ram_host.o build/obj/lf_ebr.o -llz4 -lcrypto -o build/libspace.so
cp "$R/examples/chat/build/chat-ram" "$R/examples/chat/build/libchat.so" "$R/examples/chat/build/chat.py" build/
echo "built: $(ls build | tr '\n' ' ')"
