#!/usr/bin/env bash
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
#
# demo.sh -- everything on one machine: the memory, the media server, an unattended feed playing the stock video,
# and one window. Pick the feed in the lobby and press Watch; start a second window (see TESTING.md) to call.
set -euo pipefail
cd "$(dirname "$0")"
B=build
trap 'kill $(jobs -p) 2>/dev/null' EXIT
$B/comms-ram --listen 127.0.0.1:8800 > /tmp/comms-ram.log 2>&1 &
sleep 1
$B/comms-media --listen 127.0.0.1:8994 --ram 127.0.0.1:8800 > /tmp/comms-media.log 2>&1 &
sleep 1
$B/comms-feed --ram 127.0.0.1:8800 --media 127.0.0.1:8994 --name "Chapel camera" --video media/stock-720p.mp4 --tone 440 > /tmp/comms-feed.log 2>&1 &
sleep 2
$B/comms-app --ram 127.0.0.1:8800 --media 127.0.0.1:8994 --name "${1:-John}" ${2:+--video "$2"}
