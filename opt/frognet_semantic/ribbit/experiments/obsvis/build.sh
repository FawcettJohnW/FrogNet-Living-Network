#!/bin/bash
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
#
# build.sh -- what the experiment runs, built from this tree into build/: chat-ram and libchat.so (the chat example's
# own build), libtuples.so (the default API's own build), and the apt packages the conditions need.
set -eu
cd "$(dirname "$0")"
R=$(cd ../.. && pwd)
need=""; for c in mosquitto:mosquitto wireguard-go:wireguard-go tcpdump:tcpdump wg:wireguard-tools openssl:openssl ethtool:ethtool; do
  command -v ${c%%:*} >/dev/null || need="$need ${c#*:}"; done
[ -z "$need" ] || { echo "== apt-get install$need"; apt-get install -y -q $need; }
python3 -c "import paho.mqtt, sklearn, numpy" 2>/dev/null || { echo "== pip install paho-mqtt scikit-learn numpy"; pip install -q --break-system-packages paho-mqtt scikit-learn numpy; }
bash "$R/examples/chat/build.sh"
bash "$R/defaults/api/build.sh"
mkdir -p build
cp "$R/examples/chat/build/chat-ram" "$R/examples/chat/build/libchat.so" "$R/examples/chat/build/chat.py" "$R/defaults/api/build/libtuples.so" build/
echo "built: $(ls build | tr '\n' ' ')"
