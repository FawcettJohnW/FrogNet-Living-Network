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
# frognet-collect-state.sh — read-only state snapshot for one FrogNet node.
#
# Produces /tmp/frognet-state-<hostname>-<utc-timestamp>.tar.gz
# Run as root. Safe to run on a live system — does not modify anything.
# Completes in well under a minute on a healthy node; bounded output sizes.
#
# What it captures:
#   net.* — routing, addresses, wg, iptables, conntrack, sockets, neighbours
#   proc.* — running services, process tree, timers, cron
#   logs.* — recent journal for frognet-{proxy,daemon,tunnel-daemon-v3}, dmesg
#   frognet.* — /etc/hosts, /etc/frognet, /var/lib/frognet-tunnel state JSON
#   versions.* — md5 + first/last lines of the critical Python modules so we
#                know what's actually deployed vs what's in the source tarball

set -u   # treat unset vars as errors; do NOT set -e (we want best-effort capture)

# ── output dirs ──────────────────────────────────────────────────────
HOST="$(hostname -s)"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
OUTDIR="/tmp/frognet-state-${HOST}-${TS}"
TARBALL="${OUTDIR}.tar.gz"
mkdir -p "${OUTDIR}"/{net,proc,logs,frognet,versions}

# Helper: run a command, capture stdout+stderr, never abort on failure.
run() {
  local outfile="$1"; shift
  printf '## $ %s\n' "$*" > "${outfile}"
  "$@" >> "${outfile}" 2>&1 || printf '\n## EXIT=%d\n' "$?" >> "${outfile}"
}

# Helper: capture a file if it exists, with a header showing its origin.
capture_file() {
  local src="$1" dest="$2"
  if [[ -e "$src" ]]; then
    {
      printf '## SOURCE: %s\n' "$src"
      printf '## STAT:   '; stat -c '%n size=%s mtime=%y owner=%U:%G mode=%a' "$src"
      printf '## ----\n'
      cat -- "$src"
    } > "$dest" 2>/dev/null
  else
    printf '## NOT PRESENT: %s\n' "$src" > "$dest"
  fi
}

echo "[collect] $(date -u +%H:%M:%SZ) starting on ${HOST}" >&2

# ── network state ────────────────────────────────────────────────────
run "${OUTDIR}/net/ip_addr.txt"          ip -o -4 addr show
run "${OUTDIR}/net/ip_route_main.txt"    ip -4 route show
run "${OUTDIR}/net/ip_route_all.txt"     ip -4 route show table all
run "${OUTDIR}/net/ip_rule.txt"          ip -4 rule show
run "${OUTDIR}/net/ip_neigh.txt"         ip -4 neigh show
run "${OUTDIR}/net/ip_link.txt"          ip -o link show

# wg — needs root; dump form gives the raw private keys.  Use `show all`
# (NOT dump) to avoid capturing private keys.
run "${OUTDIR}/net/wg_show.txt"          wg show all

# iptables — list with packet counters, plus rule dump
run "${OUTDIR}/net/iptables_filter.txt"  iptables -L -nv --line-numbers
run "${OUTDIR}/net/iptables_nat.txt"     iptables -t nat -L -nv --line-numbers
run "${OUTDIR}/net/iptables_save.txt"    iptables-save -c
run "${OUTDIR}/net/iptables_nat_save.txt" iptables-save -c -t nat

# conntrack — just the count + a sample, full table can be huge
{
  echo "## conntrack count:"
  conntrack -C 2>&1 || cat /proc/sys/net/netfilter/nf_conntrack_count 2>&1
  echo
  echo "## conntrack first 200 entries:"
  conntrack -L 2>&1 | head -200
} > "${OUTDIR}/net/conntrack.txt"

# Established TCP sockets — process owner, addresses, state
run "${OUTDIR}/net/ss_tcp.txt"           ss -tnpaH
run "${OUTDIR}/net/ss_listening.txt"     ss -tlnp

# Kernel network counters
run "${OUTDIR}/net/softnet_stat.txt"     cat /proc/net/softnet_stat
run "${OUTDIR}/net/netstat_i.txt"        cat /proc/net/dev
run "${OUTDIR}/net/snmp.txt"             cat /proc/net/snmp
run "${OUTDIR}/net/netstat_summary.txt"  netstat -s

# DNS
capture_file /etc/resolv.conf            "${OUTDIR}/net/resolv.conf"

# ── process / service state ──────────────────────────────────────────
run "${OUTDIR}/proc/ps.txt"              ps auxf
run "${OUTDIR}/proc/systemctl_running.txt"  systemctl list-units --type=service --state=running --no-pager --no-legend
run "${OUTDIR}/proc/systemctl_failed.txt"   systemctl list-units --state=failed --no-pager
run "${OUTDIR}/proc/systemctl_timers.txt"   systemctl list-timers --all --no-pager
run "${OUTDIR}/proc/frognet_proxy_status.txt"        systemctl status frognet-proxy --no-pager -n 20
run "${OUTDIR}/proc/frognet_daemon_status.txt"       systemctl status frognet-daemon --no-pager -n 20
run "${OUTDIR}/proc/frognet_tunnel_v3_status.txt"    systemctl status frognet-tunnel-daemon-v3 --no-pager -n 20
run "${OUTDIR}/proc/uptime.txt"          uptime
run "${OUTDIR}/proc/loadavg.txt"         cat /proc/loadavg

# crontabs — root + every user with one
{
  echo "## root crontab:"
  crontab -u root -l 2>&1
  echo
  for u in $(cut -d: -f1 /etc/passwd); do
    if crontab -u "$u" -l >/dev/null 2>&1; then
      echo "## ${u} crontab:"
      crontab -u "$u" -l 2>&1
      echo
    fi
  done
  echo "## /etc/cron.d:"
  ls -la /etc/cron.d/ 2>/dev/null
  for f in /etc/cron.d/* /etc/cron.daily/* /etc/cron.hourly/*; do
    [[ -f "$f" ]] || continue
    echo "## ---- $f ----"
    cat "$f" 2>/dev/null
  done
} > "${OUTDIR}/proc/crontabs.txt"

# Open file descriptors on frognet processes (just the count — full list
# can be thousands of entries)
{
  for p in $(pgrep -f 'frognet|proxy_main|daemon_main' 2>/dev/null); do
    [[ -d "/proc/$p" ]] || continue
    name=$(cat "/proc/$p/comm" 2>/dev/null)
    cmd=$(tr '\0' ' ' < "/proc/$p/cmdline" 2>/dev/null)
    fd_count=$(ls "/proc/$p/fd" 2>/dev/null | wc -l)
    thread_count=$(ls "/proc/$p/task" 2>/dev/null | wc -l)
    printf 'pid=%-7s name=%-15s fds=%-5s threads=%-4s cmd=%s\n' "$p" "$name" "$fd_count" "$thread_count" "$cmd"
  done
} > "${OUTDIR}/proc/frognet_pids.txt"

# ── recent logs ──────────────────────────────────────────────────────
SINCE="${FROGNET_COLLECT_SINCE:-4 hours ago}"

# Per-service journals, capped at ~5MB each
journalctl -u frognet-proxy --since "${SINCE}" --no-pager 2>&1 | tail -100000 > "${OUTDIR}/logs/frognet-proxy.log"
journalctl -u frognet-daemon --since "${SINCE}" --no-pager 2>&1 | tail -100000 > "${OUTDIR}/logs/frognet-daemon.log"
journalctl -u frognet-tunnel-daemon-v3 --since "${SINCE}" --no-pager 2>&1 | tail -100000 > "${OUTDIR}/logs/frognet-tunnel-daemon-v3.log"

# Full unfiltered journal for the window — catches kernel, NM, dnsmasq,
# anything else that might be relevant.  Last 50000 lines max.
journalctl --since "${SINCE}" --no-pager 2>&1 | tail -50000 > "${OUTDIR}/logs/journal_full.log"

# Kernel ring buffer — both -T (human) and recent only
dmesg -T 2>&1 | tail -500 > "${OUTDIR}/logs/dmesg_tail.log"

# ── frognet state files ──────────────────────────────────────────────
capture_file /etc/hosts                  "${OUTDIR}/frognet/etc_hosts.txt"
capture_file /etc/frognet_hosts          "${OUTDIR}/frognet/etc_frognet_hosts.txt"

# /etc/frognet/* — the whole tree, small files
{
  echo "## tree of /etc/frognet:"
  ls -laR /etc/frognet 2>&1
  echo
  echo "## file contents:"
  find /etc/frognet -type f 2>/dev/null | while IFS= read -r f; do
    echo "## ---- $f (size=$(stat -c %s "$f" 2>/dev/null), mtime=$(stat -c %y "$f" 2>/dev/null)) ----"
    cat -- "$f" 2>&1
    echo
  done
} > "${OUTDIR}/frognet/etc_frognet.txt"

# Tunnel daemon state
{
  echo "## tree of /var/lib/frognet-tunnel:"
  ls -laR /var/lib/frognet-tunnel 2>&1
  echo
  echo "## file contents (text only — *.json, *.txt, *.conf):"
  find /var/lib/frognet-tunnel -type f \( -name '*.json' -o -name '*.txt' -o -name '*.conf' \) 2>/dev/null | while IFS= read -r f; do
    echo "## ---- $f (size=$(stat -c %s "$f" 2>/dev/null), mtime=$(stat -c %y "$f" 2>/dev/null)) ----"
    cat -- "$f" 2>&1
    echo
  done
} > "${OUTDIR}/frognet/var_lib_frognet_tunnel.txt"

# Sentinels / run-state
{
  echo "## /etc/sentinels:"
  ls -la /etc/sentinels 2>&1
  echo
  echo "## /run/frognet:"
  ls -laR /run/frognet 2>/dev/null | head -200
} > "${OUTDIR}/frognet/sentinels_and_run.txt"

# Recent merge debug dirs — list only, not contents (too much)
{
  echo "## /run/frognet/debug recent dirs:"
  ls -1dt /run/frognet/debug/*/ 2>/dev/null | head -20
  echo
  echo "## most recent debug dir contents:"
  newest=$(ls -1dt /run/frognet/debug/*/ 2>/dev/null | head -1)
  if [[ -n "$newest" ]]; then
    echo "DIR: $newest"
    ls -la "$newest" 2>&1
  fi
} > "${OUTDIR}/frognet/debug_dirs.txt"

# ── deployed code versions ───────────────────────────────────────────
# md5 + first/last 5 lines of each critical Python file so we know what's
# actually deployed.  If the file is a shell script (e.g. corruption), the
# first lines will reveal it.
{
  echo "## deployed Python module fingerprints"
  echo "##"
  for f in \
    /opt/frognet_semantic/proxy/transport_semantic.py \
    /opt/frognet_semantic/proxy/proxy_main.py \
    /opt/frognet_semantic/proxy/proxy_metrics.py \
    /opt/frognet_semantic/proxy/netutil.py \
    /opt/frognet_semantic/proxy/cache/semcache_db.py \
    /opt/frognet_semantic/daemon/cache/semcache_db.py \
    /opt/frognet_semantic/daemon/daemon_main.py \
    /opt/frognet_semantic/internet_tunnels_v3/peer.py \
    /opt/frognet_semantic/internet_tunnels_v3/poll.py \
    /usr/local/bin/sync_interfaces.sh \
    /usr/local/bin/fixDefaultRoute \
    /usr/local/bin/runMerge.bash \
    /usr/local/bin/mergeHostsAndResolv.bash \
    /usr/local/bin/propogateNotificationInternal \
    /usr/local/bin/addHostAndPropogate.bash \
    /usr/local/bin/getHosts.php \
    /usr/local/bin/makeHostJson.bash
  do
    if [[ -f "$f" ]]; then
      printf '## ---- %s ----\n' "$f"
      printf 'md5: %s\n' "$(md5sum "$f" | awk '{print $1}')"
      printf 'size: %s, mtime: %s\n' "$(stat -c %s "$f")" "$(stat -c %y "$f")"
      printf 'head:\n'
      head -5 "$f" | sed 's/^/  /'
      printf 'tail:\n'
      tail -5 "$f" | sed 's/^/  /'
      echo
    else
      printf '## NOT PRESENT: %s\n\n' "$f"
    fi
  done
} > "${OUTDIR}/versions/deployed_modules.txt"

# Captures of suspicious files: anything matching frognet_*.bak* in /opt
{
  echo "## backup files in /opt/frognet_semantic:"
  find /opt/frognet_semantic -name '*.bak*' -o -name '*.pre_*' -o -name '*.backup*' 2>/dev/null | sort
} > "${OUTDIR}/versions/backup_files.txt"

# python version + venv
{
  /opt/frognet_semantic/venv/bin/python3 --version 2>&1
  /opt/frognet_semantic/venv/bin/python3 -c 'import sys; print(sys.prefix); print(sys.path)' 2>&1
} > "${OUTDIR}/versions/python.txt"

# frogsim — if present anywhere, capture the directory structure (NOT the
# whole thing, which could be huge).  Capture STATUS.md if it exists.
{
  echo "## locations of any frogsim files:"
  find /opt /home /var/lib /usr/local -maxdepth 4 -name 'frogsim*' -o -name 'STATUS.md' 2>/dev/null | head -50
  echo
  for d in /opt/frogsim /opt/frognet_semantic/frogsim /home/froguser/frogsim; do
    if [[ -d "$d" ]]; then
      echo "## tree of $d (1 level deep):"
      ls -la "$d" 2>&1 | head -40
      echo
      if [[ -f "$d/STATUS.md" ]]; then
        echo "## $d/STATUS.md:"
        cat "$d/STATUS.md"
        echo
      fi
    fi
  done
} > "${OUTDIR}/versions/frogsim_location.txt"

# ── disk / memory ────────────────────────────────────────────────────
run "${OUTDIR}/proc/df.txt"              df -h
run "${OUTDIR}/proc/free.txt"            free -h
run "${OUTDIR}/proc/disk_journal.txt"    du -sh /var/log/journal 2>/dev/null

# ── tarball it ───────────────────────────────────────────────────────
cd /tmp
tar czf "${TARBALL}" "$(basename "${OUTDIR}")" 2>&1
rm -rf "${OUTDIR}"

size=$(stat -c %s "${TARBALL}" 2>/dev/null)
echo "[collect] done.  ${TARBALL} (${size} bytes)" >&2
echo "${TARBALL}"
