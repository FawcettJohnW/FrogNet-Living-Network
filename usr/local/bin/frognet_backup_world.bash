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
# frognet_world_backup.sh
#
# FrogNet "world" backup + restore.
#
# Captures EVERYTHING needed to stand up a FrogNet node on a clean machine:
#   - Filesystem: /etc, /usr/local, /var/www, /opt/frognet_semantic
#   - MySQL 'FrogNet' database (full dump with CREATE DATABASE)
#   - iptables rules (restorable format)
#   - sysctl settings (restorable format)
#   - Python venv dependency manifest (pip freeze)
#   - WireGuard keys + config (/etc/wireguard, already under /etc)
#   - User crontabs (root, www-data)
#   - System state metadata for diagnostics
#
# Usage:
#   sudo ./frognet_world_backup.sh backup  /path/to/frognet_world_YYYYmmdd_HHMMSS.tgz
#   sudo ./frognet_world_backup.sh restore /path/to/frognet_world_YYYYmmdd_HHMMSS.tgz
#
# Restore order (critical for mesh networking):
#   1. Extract filesystem
#   2. Restore sysctl (ip_forward etc. — mesh routing needs this first)
#   3. Restore iptables rules
#   4. Restore MySQL database
#   5. systemd daemon-reload
#   6. Start services: mysql → dnsmasq → NetworkManager → frognet-daemon → frognet-proxy → apache
#
# Notes:
#  - MySQL auth: socket auth (sudo mysql). No credentials needed.
#  - iptables: saved in iptables-save format. Your boot script can reload, but
#    restore applies them immediately so the node is functional right away.
#  - This does NOT install OS packages. See frognet_world_meta/packages_dpkg.txt
#    for the package manifest to replay on a clean machine.

CMD="${1:-}"
ARG="${2:-}"
TMPDIR_CLEANUP=""   # script-scope so EXIT trap can always see it

die() { echo "ERROR: $*" >&2; exit 1; }
log() { echo "[frognet_world] $*" >&2; }
warn() { echo "[frognet_world] WARN: $*" >&2; }

need_root() {
  if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
    die "Must run as root."
  fi
}

have() { command -v "$1" &>/dev/null; }

ts_now() { date +"%Y%m%d_%H%M%S"; }

# -------- Paths to include --------
# /etc covers: wireguard, dnsmasq.d, systemd/system, NetworkManager, sentinels, frognet configs
# No need to list subdirectories separately — they're all under /etc.
INCLUDE_PATHS=(
  "/etc"
  "/usr/local"
  "/var/www"
  "/opt/frognet_semantic"
  "/var/lib/misc/dnsmasq.leases"
  "/var/lib/NetworkManager"
  "/var/lib/dnsmasq.d"
  "/etc/frognet_bundles"
)

# Exclude large/volatile content
EXCLUDE_PATTERNS=(
  "var/log/*"
  "var/cache/*"
  "var/tmp/*"
  "tmp/*"
  "proc/*"
  "sys/*"
  "dev/*"
  "run/*"
  "mnt/*"
  "media/*"
  "*.tar"
  "*.tgz"
  "*.pyc"
  "__pycache__"
  ".git"
  "games"
  # Exclude venv site-packages (will be rebuilt from pip freeze)
  # but keep the venv structure and frognet code
  "opt/frognet_semantic/venv/lib/*/site-packages/*"
  "opt/frognet_semantic/venv/lib64"
)

# -------- State capture helpers --------
capture_state_into_dir() {
  local outdir="$1"
  mkdir -p "$outdir"

  log "Capturing system state into $outdir"

  # ===== IDENTITY =====
  {
    echo "timestamp=$(date -Is)"
    echo "hostname=$(hostname 2>/dev/null || echo UNKNOWN)"
    echo "uname=$(uname -a 2>/dev/null || echo UNKNOWN)"
  } > "${outdir}/identity.txt"

  # ===== NETWORK STATE =====
  if have ip; then
    ip -br addr > "${outdir}/ip_addr_br.txt"    2>&1 || true
    ip r        > "${outdir}/ip_route.txt"       2>&1 || true
    ip rule     > "${outdir}/ip_rule.txt"        2>&1 || true
  fi

  cp -a /etc/hosts       "${outdir}/etc_hosts"    2>/dev/null || true
  cp -a /etc/resolv.conf "${outdir}/resolv.conf"  2>/dev/null || true

  # ===== IPTABLES (restorable format) =====
  if have iptables-save; then
    log "Saving iptables rules (restorable)"
    iptables-save  > "${outdir}/iptables.rules"  2>&1 || warn "iptables-save failed"
  else
    warn "iptables-save not found — firewall rules NOT captured"
  fi

  if have ip6tables-save; then
    ip6tables-save > "${outdir}/ip6tables.rules" 2>&1 || warn "ip6tables-save failed"
  fi

  # ===== SYSCTL (restorable format) =====
  log "Saving sysctl settings (restorable)"
  sysctl -a > "${outdir}/sysctl_all.conf" 2>&1 || warn "sysctl -a failed"
  # Also capture just the non-default/frognet-relevant ones for quick review
  {
    sysctl net.ipv4.ip_forward
    sysctl net.ipv4.conf.all.forwarding
    sysctl net.ipv6.conf.all.forwarding
    sysctl net.ipv4.conf.all.rp_filter
    sysctl net.ipv4.conf.default.rp_filter
  } > "${outdir}/sysctl_frognet.conf" 2>&1 || true

  # ===== MYSQL DATABASE DUMP =====
  if have mysqldump; then
    log "Dumping MySQL 'FrogNet' database"
    # Socket auth — no password needed when running as root
    # --routines: stored procs/functions
    # --triggers: table triggers
    # --events: scheduled events
    # --single-transaction: consistent snapshot without locking (InnoDB)
    # --create-options: include engine, charset, etc.
    if mysqldump \
        --databases FrogNet \
        --routines \
        --triggers \
        --events \
        --single-transaction \
        --create-options \
        > "${outdir}/frognet_db.sql" 2>"${outdir}/mysqldump_stderr.txt"; then
      log "MySQL dump complete: $(wc -c < "${outdir}/frognet_db.sql") bytes"
    else
      warn "mysqldump failed — see ${outdir}/mysqldump_stderr.txt"
      cat "${outdir}/mysqldump_stderr.txt" >&2
    fi
  else
    warn "mysqldump not found — database NOT captured"
  fi

  # Also capture MySQL user grants (needed to recreate DB users on clean machine)
  if have mysql; then
    log "Capturing MySQL user grants"
    mysql -N -e "SELECT CONCAT('-- ', user, '@', host) AS '-- user', \
      CONCAT(\"SHOW GRANTS FOR '\", user, \"'@'\", host, \"';\") AS query \
      FROM mysql.user WHERE user NOT IN ('root','mysql.sys','mysql.session','mysql.infoschema','debian-sys-maint')" \
      2>/dev/null | while IFS=$'\t' read -r comment query; do
        echo "$comment"
        mysql -N -e "$query" 2>/dev/null || true
        echo ""
      done > "${outdir}/mysql_grants.sql" 2>/dev/null || true
  fi

  # ===== PYTHON VENV =====
  local venv_pip="/opt/frognet_semantic/venv/bin/pip"
  if [[ -x "$venv_pip" ]]; then
    log "Capturing Python venv dependencies (pip freeze)"
    "$venv_pip" freeze > "${outdir}/pip_freeze.txt" 2>&1 || warn "pip freeze failed"
    "$venv_pip" --version > "${outdir}/pip_version.txt" 2>&1 || true
    /opt/frognet_semantic/venv/bin/python --version > "${outdir}/python_version.txt" 2>&1 || true
  else
    warn "FrogNet venv pip not found at $venv_pip"
  fi

  # ===== WIREGUARD STATE (diagnostic, not restorable — keys are in /etc/wireguard) =====
  if have wg; then
    log "Capturing WireGuard runtime state"
    wg show all > "${outdir}/wg_show.txt" 2>&1 || true
  fi

  # ===== SYSTEMD SNAPSHOT =====
  if have systemctl; then
    systemctl list-unit-files --type=service > "${outdir}/systemd_unit_files.txt" 2>&1 || true
    systemctl list-units --type=service --all > "${outdir}/systemd_units_all.txt"  2>&1 || true
    systemctl list-timers --all              > "${outdir}/systemd_timers.txt"      2>&1 || true

    # FrogNet-specific service state
    for svc in frognet-proxy frognet-daemon; do
      {
        echo "=== ${svc}.service ==="
        systemctl is-enabled "${svc}.service" 2>&1 || echo "not found"
        systemctl status "${svc}.service" 2>&1 || true
        echo ""
      } >> "${outdir}/frognet_services_detail.txt"
    done
  fi

  # ===== NETWORKMANAGER =====
  if have nmcli; then
    nmcli -t -f NAME,UUID,TYPE,DEVICE connection show > "${outdir}/nm_connections.txt" 2>&1 || true
    nmcli -t device status > "${outdir}/nm_device_status.txt" 2>&1 || true
  fi

  # ===== DNSMASQ =====
  if have dnsmasq; then
    dnsmasq --version > "${outdir}/dnsmasq_version.txt" 2>&1 || true
  fi

  # ===== APACHE / PHP =====
  if have apache2ctl; then
    apache2ctl -V > "${outdir}/apache_version.txt" 2>&1 || true
    apache2ctl -S > "${outdir}/apache_vhosts.txt"  2>&1 || true
  elif have httpd; then
    httpd -V > "${outdir}/apache_version.txt" 2>&1 || true
  fi

  if have php; then
    php -v > "${outdir}/php_version.txt" 2>&1 || true
    php -m > "${outdir}/php_modules.txt" 2>&1 || true
  fi

  # ===== USER CRONTABS =====
  log "Capturing user crontabs"
  mkdir -p "${outdir}/crontabs"
  for user in root www-data frognet; do
    if id "$user" &>/dev/null; then
      crontab -u "$user" -l > "${outdir}/crontabs/${user}.crontab" 2>/dev/null || true
    fi
  done
  # System cron
  if [[ -d /etc/cron.d ]]; then
    tar -C / -cf "${outdir}/cron_etc.tar" etc/cron.d etc/crontab 2>/dev/null || true
  fi

  # ===== PACKAGE MANIFEST =====
  if have dpkg-query; then
    dpkg-query -W -f='${binary:Package}\t${Version}\n' > "${outdir}/packages_dpkg.txt" 2>&1 || true
  elif have rpm; then
    rpm -qa > "${outdir}/packages_rpm.txt" 2>&1 || true
  fi

  # ===== PERMISSION SNAPSHOTS =====
  for dir in /opt/frognet_semantic /usr/local/bin /etc/sentinels /etc/wireguard /var/www; do
    local safename
    safename="$(echo "$dir" | tr '/' '_')"
    ls -la "$dir" > "${outdir}/ls${safename}.txt" 2>/dev/null || true
  done
}

make_exclude_args() {
  local -a args=()
  for p in "${EXCLUDE_PATTERNS[@]}"; do
    args+=( "--exclude=$p" )
  done
  printf "%s\n" "${args[@]}"
}

# -------- Backup --------
do_backup() {
  need_root
  local out_tgz="$1"
  [[ -n "$out_tgz" ]] || die "backup requires an output .tgz path"

  TMPDIR_CLEANUP="$(mktemp -d /tmp/frognet_world.XXXXXX)"
  trap 'rm -rf "$TMPDIR_CLEANUP"' EXIT

  local tmpdir="$TMPDIR_CLEANUP"
  local meta="${tmpdir}/frognet_world_meta"
  capture_state_into_dir "$meta"

  # Build file list relative to /
  local filelist="${tmpdir}/include_paths.txt"
  : > "$filelist"

  for p in "${INCLUDE_PATHS[@]}"; do
    if [[ -e "$p" ]]; then
      echo "${p#/}" >> "$filelist"
    else
      warn "missing path (skipping): $p"
    fi
  done

  # Create tar in two stages: system snapshot + meta
  local tar_tmp="${tmpdir}/world.tar"

  # Stage 1: system paths (from /)
  local -a exargs
  mapfile -t exargs < <(make_exclude_args)

  log "Creating tar archive (stage 1: system paths)"
  tar -C / -cf "$tar_tmp" "${exargs[@]}" -T "$filelist"

  # Stage 2: embed metadata (from tmpdir)
  log "Adding metadata (stage 2)"
  tar -C "$tmpdir" -rf "$tar_tmp" "frognet_world_meta"

  log "Compressing to $out_tgz"
  gzip -c "$tar_tmp" > "$out_tgz"

  local size
  size="$(du -h "$out_tgz" | cut -f1)"
  log "Backup complete: $out_tgz ($size)"
  log ""
  log "Contents summary:"
  log "  Filesystem: /etc, /usr/local, /var/www, /opt/frognet_semantic"
  log "  Database:   frognet_world_meta/frognet_db.sql"
  log "  Firewall:   frognet_world_meta/iptables.rules"
  log "  Sysctl:     frognet_world_meta/sysctl_frognet.conf"
  log "  Python:     frognet_world_meta/pip_freeze.txt"
  log "  Crontabs:   frognet_world_meta/crontabs/"
  log "  Packages:   frognet_world_meta/packages_dpkg.txt"
}

# -------- Restore helpers --------
enable_if_exists() {
  local svc="$1"
  if have systemctl; then
    if systemctl list-unit-files --type=service | awk '{print $1}' | grep -qx "$svc"; then
      log "Enabling service: $svc"
      systemctl enable "$svc" 2>&1 || warn "Failed to enable $svc"
    else
      log "Service unit not found: $svc (skip enable)"
    fi
  fi
}

restart_if_active() {
  local svc="$1"
  if have systemctl; then
    if systemctl list-unit-files --type=service | awk '{print $1}' | grep -qx "$svc"; then
      log "Restarting service: $svc"
      systemctl restart "$svc" 2>&1 || warn "Failed to restart $svc"
    else
      log "Service unit not found: $svc (skip restart)"
    fi
  fi
}

# -------- Restore --------
do_restore() {
  need_root
  local in_tgz="$1"
  [[ -n "$in_tgz" ]] || die "restore requires an input .tgz path"
  [[ -f "$in_tgz" ]] || die "file not found: $in_tgz"

  local meta_dir="/frognet_world_meta"

  # ===== STEP 1: Extract filesystem =====
  log "Step 1/7: Extracting $in_tgz onto /"
  tar -C / -xzf "$in_tgz"
  log "Filesystem extraction complete"

  # ===== STEP 2: Restore sysctl (routing must work before services start) =====
  log "Step 2/7: Restoring sysctl settings"
  if [[ -f "${meta_dir}/sysctl_frognet.conf" ]]; then
    # Apply FrogNet-critical sysctl values
    while IFS='=' read -r key value; do
      key="$(echo "$key" | xargs)"    # trim whitespace
      value="$(echo "$value" | xargs)"
      if [[ -n "$key" && -n "$value" && "$key" != \#* ]]; then
        sysctl -w "${key}=${value}" 2>&1 || warn "sysctl failed: ${key}=${value}"
      fi
    done < "${meta_dir}/sysctl_frognet.conf"
    log "Sysctl settings applied"
  else
    warn "No sysctl_frognet.conf found in backup"
  fi

  # ===== STEP 3: Restore iptables rules =====
  log "Step 3/7: Restoring iptables rules"
  if [[ -f "${meta_dir}/iptables.rules" ]] && have iptables-restore; then
    iptables-restore < "${meta_dir}/iptables.rules" 2>&1 || warn "iptables-restore failed"
    log "iptables rules restored"
  else
    warn "No iptables.rules or iptables-restore not available"
  fi

  if [[ -f "${meta_dir}/ip6tables.rules" ]] && have ip6tables-restore; then
    ip6tables-restore < "${meta_dir}/ip6tables.rules" 2>&1 || warn "ip6tables-restore failed"
  fi

  # ===== STEP 4: Restore MySQL database =====
  log "Step 4/7: Restoring MySQL database"
  if [[ -f "${meta_dir}/frognet_db.sql" ]]; then
    if have mysql; then
      # Check if MySQL/MariaDB is running; start it if not
      if have systemctl; then
        if ! systemctl is-active --quiet mysql.service && \
           ! systemctl is-active --quiet mariadb.service; then
          log "Starting MySQL service for database restore"
          systemctl start mysql.service 2>&1 || \
            systemctl start mariadb.service 2>&1 || \
            warn "Could not start MySQL — database restore may fail"
        fi
      fi

      log "Importing FrogNet database (this may take a moment)"
      if mysql < "${meta_dir}/frognet_db.sql" 2>"${meta_dir}/mysql_restore_stderr.txt"; then
        log "MySQL database restored successfully"
      else
        warn "MySQL restore encountered errors — see ${meta_dir}/mysql_restore_stderr.txt"
        cat "${meta_dir}/mysql_restore_stderr.txt" >&2
      fi

      # Restore grants if present
      if [[ -s "${meta_dir}/mysql_grants.sql" ]]; then
        log "Restoring MySQL user grants"
        mysql < "${meta_dir}/mysql_grants.sql" 2>&1 || warn "Some grants may have failed"
      fi
    else
      warn "mysql client not found — database NOT restored"
      warn "Install mysql-client, then run: sudo mysql < ${meta_dir}/frognet_db.sql"
    fi
  else
    warn "No frognet_db.sql found in backup"
  fi

  # ===== STEP 5: Rebuild Python venv if needed =====
  log "Step 5/7: Checking Python venv"
  local venv_dir="/opt/frognet_semantic/venv"
  local venv_pip="${venv_dir}/bin/pip"
  if [[ -f "${meta_dir}/pip_freeze.txt" ]]; then
    if [[ ! -x "$venv_pip" ]]; then
      log "Venv not functional — attempting rebuild"
      if have python3; then
        python3 -m venv "$venv_dir" 2>&1 || warn "venv creation failed"
        if [[ -x "$venv_pip" ]]; then
          "$venv_pip" install --upgrade pip 2>&1 || true
          log "Installing packages from pip_freeze.txt"
          "$venv_pip" install -r "${meta_dir}/pip_freeze.txt" 2>&1 || \
            warn "Some pip packages failed to install — review manually"
        fi
      else
        warn "python3 not found — cannot rebuild venv"
      fi
    else
      log "Venv exists at $venv_dir — skipping rebuild"
      log "To force reinstall: ${venv_pip} install -r ${meta_dir}/pip_freeze.txt"
    fi
  fi

  # ===== STEP 6: Restore user crontabs =====
  log "Step 6/7: Restoring user crontabs"
  if [[ -d "${meta_dir}/crontabs" ]]; then
    for cfile in "${meta_dir}/crontabs"/*.crontab; do
      [[ -f "$cfile" ]] || continue
      [[ -s "$cfile" ]] || continue  # skip empty files
      local username
      username="$(basename "$cfile" .crontab)"
      if id "$username" &>/dev/null; then
        crontab -u "$username" "$cfile" 2>&1 || warn "Failed to restore crontab for $username"
        log "Restored crontab for $username"
      else
        warn "User $username does not exist — skipping crontab restore"
      fi
    done
  fi

  # ===== STEP 7: Restart services in dependency order =====
  log "Step 7/7: Registering and starting services"

  if have systemctl; then
    log "systemd daemon-reload"
    systemctl daemon-reload 2>&1 || warn "daemon-reload failed"
  fi

  # Order matters: network plumbing → discovery → FrogNet → web
  restart_if_active "NetworkManager.service"
  restart_if_active "dnsmasq.service"

  enable_if_exists "frognet-daemon.service"
  enable_if_exists "frognet-proxy.service"
  restart_if_active "frognet-daemon.service"
  restart_if_active "frognet-proxy.service"

  restart_if_active "apache2.service"

  # Ensure executable bits on /usr/local/bin
  if [[ -d /usr/local/bin ]]; then
    chmod -R a+rx /usr/local/bin 2>&1 || true
  fi

  log ""
  log "============================================"
  log "  Restore complete."
  log "============================================"
  log ""
  log "Post-restore checklist:"
  log "  1. Verify hostname:     hostname"
  log "  2. Check mesh routing:  ip route"
  log "  3. Check forwarding:    sysctl net.ipv4.ip_forward"
  log "  4. Check firewall:      iptables -L -n -v"
  log "  5. Check WireGuard:     wg show"
  log "  6. Check FrogNet:       systemctl status frognet-daemon frognet-proxy"
  log "  7. Check database:      mysql -e 'SHOW TABLES' FrogNet"
  log "  8. Check web:           curl -s http://localhost:8080/"
  log ""
  log "If this is a clean machine, install OS packages first:"
  log "  See: ${meta_dir}/packages_dpkg.txt"
}

# -------- Main --------
case "$CMD" in
  backup)
    [[ -n "$ARG" ]] || die "backup requires output tgz path"
    do_backup "$ARG"
    ;;
  restore)
    [[ -n "$ARG" ]] || die "restore requires input tgz path"
    do_restore "$ARG"
    ;;
  *)
    cat >&2 <<EOF
Usage:
  sudo $0 backup  /path/to/frognet_world_$(ts_now).tgz
  sudo $0 restore /path/to/frognet_world_YYYYmmdd_HHMMSS.tgz

Backup captures:
  Filesystem:  ${INCLUDE_PATHS[*]}
  Database:    MySQL 'FrogNet' (full dump with routines/triggers/events)
  Firewall:    iptables-save (restorable format)
  Sysctl:      All kernel params + FrogNet-critical subset
  Python:      pip freeze from /opt/frognet_semantic/venv
  Crontabs:    root, www-data, frognet
  WireGuard:   Config in /etc/wireguard + runtime state snapshot
  Packages:    dpkg package manifest

Restore order:
  1. Filesystem extraction
  2. Sysctl (routing must work first)
  3. Iptables rules
  4. MySQL database import
  5. Python venv rebuild (if needed)
  6. User crontabs
  7. Services (NetworkManager → dnsmasq → daemon → proxy → apache)

Excludes: ${EXCLUDE_PATTERNS[*]}
EOF
    exit 2
    ;;
esac
