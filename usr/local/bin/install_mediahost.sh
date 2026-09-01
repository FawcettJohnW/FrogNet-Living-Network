#!/usr/bin/env bash
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
# install_mediahost.sh — make THIS machine able to serve the mediahost role.
#
# A mediahost transcodes/relays SotF-ACP A/V on port 9000. Like the databasehost it
# becomes a CANDIDATE: it advertises its capability under `mediahost/capability` and
# the merge-end election picks the best candidate by merit (ffmpeg+libvpx is the hard
# gate; measured cross-arch CPU benchmark + hardware encoder decide among the eligible).
#
# What it does:
#   1. Install ffmpeg (capture/encode/decode) — the hard requirement for the role.
#   2. Deploy the A/V server (frognet_communicator.py) to the communicator bundle.
#   3. Install a systemd service that runs the A/V host on :9000.
#   4. Install a 60s timer that writes mediahost/capability so the election can score
#      this machine.
#
# Usage:  sudo ./install_mediahost.sh [--src <dir>] [--port N] [--bind ADDR]
#   --src   directory holding frognet_communicator.py + frognet_capability_probe.sh
#           (default: . then /etc/frognet_bundles/communicator)
#   --port  A/V server port (default 9000)
#   --bind  bind address (default 0.0.0.0 — reachable on the LAN)
set -eu

SRC=""
PORT=9000
BIND="0.0.0.0"
while [ $# -gt 0 ]; do
  case "$1" in
    --src) SRC="$2"; shift 2;;
    --port) PORT="$2"; shift 2;;
    --bind) BIND="$2"; shift 2;;
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac
done
[ "$(id -u)" = "0" ] || { echo "must run as root (sudo)" >&2; exit 1; }

if [ -z "$SRC" ]; then
  for c in . /etc/frognet_bundles/communicator; do
    [ -f "$c/frognet_communicator.py" ] && { SRC="$c"; break; }
  done
fi
[ -n "$SRC" ] && [ -f "$SRC/frognet_communicator.py" ] || {
  echo "[mediahost] no payload found (need frognet_communicator.py); pass --src <dir>" >&2; exit 1; }
echo "[mediahost] payload: $SRC"

# 1. ffmpeg — the hard gate for the role
echo "[mediahost] installing ffmpeg ..."
export DEBIAN_FRONTEND=noninteractive
if command -v apt-get >/dev/null 2>&1; then
  apt-get update -qq
  apt-get install -y -qq ffmpeg python3 jq curl >/dev/null
else
  echo "[mediahost] non-apt system: install ffmpeg + python3 yourself, then re-run" >&2
fi
if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "[mediahost] ERROR: ffmpeg not present after install; the role cannot run here" >&2
  exit 1
fi
has_libvpx() { ffmpeg -hide_banner -encoders 2>/dev/null | grep -q libvpx; }
if ! has_libvpx; then
  echo "[mediahost] ffmpeg present but lacks libvpx (VP8) — attempting to install it ..."
  if command -v apt-get >/dev/null 2>&1; then
    # 1. install the libvpx runtime (package name varies by release)
    apt-get install -y -qq libvpx-dev >/dev/null 2>&1 \
      || { for p in libvpx9 libvpx8 libvpx7 libvpx6 libvpx; do
             apt-get install -y -qq "$p" >/dev/null 2>&1 && break
           done; } || true
    # 2. if the ffmpeg BINARY still has no libvpx encoder, it was built without it;
    #    reinstall the distro ffmpeg (which is linked against libvpx) and re-check.
    if ! has_libvpx; then
      echo "[mediahost] reinstalling distro ffmpeg (built with libvpx) ..."
      apt-get install -y -qq --reinstall ffmpeg >/dev/null 2>&1 || true
    fi
  fi
  if has_libvpx; then
    echo "[mediahost] libvpx (VP8) now available — host is eligible for the media role"
  else
    echo "[mediahost] WARN: still no libvpx in ffmpeg — the election will gate this host OUT." >&2
    echo "[mediahost]       The ffmpeg in PATH is likely a static/snap build without VP8;" >&2
    echo "[mediahost]       install a libvpx-enabled ffmpeg (e.g. the distro package) to qualify." >&2
  fi
fi

# Whatever we just did to ffmpeg/libvpx changed this host's A/V capability. The
# capability probe caches the static A/V blob (ffmpeg/libvpx/encoders) in
# /run/frognet_avcap.json and returns it verbatim forever — it never re-probes
# once cached. So a host first probed BEFORE libvpx existed keeps advertising
# libvpx=false and the election keeps gating it out even after libvpx is installed.
# Drop the cache so the next advertise re-detects the real capability.
rm -f /run/frognet_avcap.json 2>/dev/null || true

# 2. deploy the A/V server + shared probe into the communicator bundle
BUNDLE=/etc/frognet_bundles/communicator
mkdir -p "$BUNDLE"
# copy a file into the bundle UNLESS it's already that same file (SRC may BE the
# bundle, e.g. the default payload dir) — cp of a file onto itself errors under set -e.
_deploy() {  # <srcfile> <destdir>
  local f="$1" dd="$2" b; b="$(basename "$f")"
  [ -f "$f" ] || return 0
  if [ -e "$dd/$b" ] && [ "$f" -ef "$dd/$b" ]; then
    echo "[mediahost] $b already in place ($dd) — skip"; return 0
  fi
  cp -f "$f" "$dd/"
}
_deploy "$SRC/frognet_communicator.py" "$BUNDLE"
for extra in frognet_tuples.py sotf_ladder.py frognet_capability_probe.sh; do
  _deploy "$SRC/$extra" "$BUNDLE"
done
install -m 0755 "$BUNDLE/frognet_capability_probe.sh" /usr/local/bin/frognet_capability_probe.sh 2>/dev/null || \
  cp -f "$SRC/frognet_capability_probe.sh" /usr/local/bin/ 2>/dev/null || true

# 3. A/V server systemd unit on :PORT
cat > /etc/systemd/system/frognet-mediahost.service <<UNIT
[Unit]
Description=FrogNet mediahost A/V server (SotF-ACP) on ${BIND}:${PORT}
After=network-online.target
Wants=network-online.target
[Service]
Type=simple
WorkingDirectory=${BUNDLE}
Environment=PYTHONPATH=/opt/frognet_semantic
ExecStart=/usr/bin/env python3 ${BUNDLE}/frognet_communicator.py --serve ${BIND}:${PORT}
Restart=on-failure
RestartSec=3
[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable --now frognet-mediahost.service >/dev/null 2>&1 || true

# 4. candidate registration (advertises mediahost/capability every 60s) via the
# shared election-COMPATIBLE writer. ffmpeg+libvpx in the blob gates eligibility; the
# served av_port (${PORT}) is stamped into the blob. The election keeps only LAN media
# candidates (reach_plane split), so registering everywhere is safe.
for f in frognet_capability_probe.sh frognet_register_candidate.sh frognet_setup_advertisers.sh; do
  install -m 0755 "$f" /usr/local/bin/ 2>/dev/null || cp -f "$f" /usr/local/bin/ 2>/dev/null || true
done
if [ -x /usr/local/bin/frognet_setup_advertisers.sh ]; then
  /usr/local/bin/frognet_setup_advertisers.sh || true       # installs + enables BOTH timers
fi
# re-point the media timer at the actual served port and register once now
FROGNET_AV_PORT="${PORT}" /usr/local/bin/frognet_register_candidate.sh mediahost || true
if [ -f /etc/systemd/system/frognet-mediahost-advertise.service ]; then
  sed -i "s#frognet_register_candidate.sh mediahost.*#frognet_register_candidate.sh mediahost ${PORT}#" \
      /etc/systemd/system/frognet-mediahost-advertise.service
  systemctl daemon-reload 2>/dev/null || true
fi

echo "[mediahost] DONE. This machine is now a mediahost CANDIDATE."
echo "[mediahost]   - ffmpeg present; A/V server running on ${BIND}:${PORT}"
echo "[mediahost]   - capability advertised every 60s under mediahost/capability"
echo "[mediahost]   - the merge-end election will pick it if it scores best."
