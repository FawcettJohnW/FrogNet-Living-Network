#!/bin/bash
# fdf-install -- [FDF_INSTALL_V1] one file, one command: joins THIS FrogNet node to the FD&F region.
#   usage:  sudo ./fdf-install-ARCH.run fdfinv1.<...>
# The one argument is this node's invitation: `fdf-invite NAME` on the region's machine printed it
# ([ADMITTED_BY_INVITE_V1]). There is no built-in region. The FrogNet installer runs this file itself with --invite
# (frognet_install.sh ... --invite fdfinv1.<...>).
# What it does, in order, and says so: checks root/arch/python3/wg; unpacks the embedded node bundle to /
# (/opt/fdf, the FrogNet region patch under /opt/frognet_semantic, propogateNotificationInternal); runs
# /opt/fdf/bin/fdf-region-join (key, hub.json, site.env, join.env, daemon restart, fdf-facts + fdf-client units, 15 s report).
# Re-runnable. The FrogNet install itself is left as it is except for the patched modules listed by the unpack.
set -u
# [UMASK_IS_OURS_V1 - T4a] what this lays down under /etc, /opt and /usr must not take its modes from the caller's
# umask (a shell with umask 0000 made /opt/fdf and its bin/ world-writable, in the T4a container).
umask 022
[ "$(id -u)" = 0 ] || { echo "fdf-install: run as root (sudo)"; exit 1; }
[ $# -eq 1 ] || { echo "usage: $0 fdfinv1.<...>   (fdf-invite NAME on the region's machine prints it)"; exit 2; }
case "$1" in fdfinv1.*) INV=$1 ;; *) echo "fdf-install: '$1' is not an invitation (fdfinv1.<...>; fdf-invite NAME prints one)"; exit 2 ;; esac
WANT_ARCH="__ARCH__"; M=$(uname -m)
[ "$M" = "$WANT_ARCH" ] || { echo "fdf-install: this file is for $WANT_ARCH, this machine is $M"; exit 1; }
# inotifywait (inotify-tools): the FrogNet merge watcher waits with it, and runs in region mode too since T4c (the
# NetworkManager hook's merges are requests to it); a node installed before T4c may not have it [WATCHER_HAS_INOTIFY_V1]
for b in python3 wg systemctl tar inotifywait apache2ctl curl; do command -v $b >/dev/null || { echo "fdf-install: $b is required and missing$( [ $b = inotifywait ] && echo ' (apt-get install inotify-tools)')"; exit 1; }; done
[ -d /opt/frognet_semantic/internet_tunnels_v3 ] || { echo "fdf-install: /opt/frognet_semantic/internet_tunnels_v3 not found: this is not a FrogNet node (install FrogNet first)"; exit 1; }
# [NODE_HAS_THE_VENV_V1] the bundle's FrogNet files (runMerge.bash, the tunnel daemon's region code) run on the venv the
# FrogNet installer builds ([SERVICES_ON_THE_VENV_V1]). A node installed before that has none: unpacking would leave its
# merges failing "status=no_python". Stop before anything is written, and say what to do.
# [NODE_HAS_THE_BASE_V1] and /opt/frognet_semantic/BUILD, the release stamp the bundle's web pages read (frognet_node.inc.php)
for need in /opt/frognet_semantic/venv/bin/python3 /opt/frognet_semantic/BUILD; do
  [ -e "$need" ] || { echo "fdf-install: $need not found: this node's FrogNet is older than the files in this bundle (they run on the venv and read BUILD). Nothing was changed. Upgrade FrogNet first: unpack frognet-tree-<tag>.tgz into an empty directory and run  bash usr/local/bin/installer/frognet_install.sh --from-repo --preserve  from it (no git needed), then run this again."; exit 1; }
done
echo "== unpacking the node bundle into /"
LINE=$(awk '/^__PAYLOAD_BELOW__$/ {print NR + 1; exit 0}' "$0")
# [BACKUP_BEFORE_INSTALL_V1] (John 2026-10-25: "This had better work, or fall back if it doesn't") -- before anything is
# written: every file this bundle would replace, and the node's region state (site.env, join.env, hub.json, facts.json,
# the WireGuard key, the fdf units), saved as it is now; and a restore script that puts all of it back, removes what this
# bundle added, and restarts the daemon and the units on the old files. Nothing else does the restoring: you run it.
STAMP=$(date -u +%Y%m%dT%H%M%SZ); BK=/opt/fdf-backup-$STAMP
tail -n +$LINE "$0" | tar tzf - | sed 's|^\./||' | grep -v '/$' > "$BK.list" || { echo "fdf-install: cannot read the bundle"; exit 1; }
STATE="/opt/fdf/var/site.env /opt/fdf/var/join.env /opt/fdf/var/hub.json /opt/fdf/var/facts.json /var/lib/frognet-tunnel/node_private.key /var/lib/frognet-tunnel/node_public.key /etc/systemd/system/fdf-client.service /etc/systemd/system/fdf-facts.service"
( cd / && { cat "$BK.list"; for f in $STATE; do echo "${f#/}"; done; } | sort -u | while read -r f; do [ -e "/$f" ] && echo "$f"; done ) > "$BK.had"
( cd / && { cat "$BK.list"; for f in $STATE; do echo "${f#/}"; done; } | sort -u | while read -r f; do [ -e "/$f" ] || echo "$f"; done ) > "$BK.new"
( cd / && tar czf "$BK.tgz" -T "$BK.had" ) || { echo "fdf-install: backup to $BK.tgz failed: nothing was installed"; exit 1; }
cat > "$BK.restore" <<RESTORE
#!/bin/bash
# $BK.restore -- put this node back exactly as it was before fdf-install ran at $STAMP [BACKUP_BEFORE_INSTALL_V1]
set -u
[ "\$(id -u)" = 0 ] || { echo "run as root"; exit 1; }
systemctl stop fdf-client.service fdf-facts.service frognet-tunnel-daemon-v3 2>/dev/null
cd / && while read -r f; do rm -f "/\$f"; done < "$BK.new" && echo "== removed the \$(wc -l < $BK.new) file(s) the install added"
cd / && tar xzf "$BK.tgz" && echo "== restored the \$(wc -l < $BK.had) file(s) it replaced"
ip route del unreachable 10.253.0.0/16 metric 4253 2>/dev/null
systemctl daemon-reload
for u in fdf-client.service fdf-facts.service; do [ -e /etc/systemd/system/\$u ] && systemctl start \$u; done
systemctl start frognet-tunnel-daemon-v3
echo "== restored; the daemon and the fdf units restarted on the old files: \$(cat /opt/fdf/BUILD 2>/dev/null | tr '\\n' ' ')"
RESTORE
chmod 700 "$BK.restore"
echo "== backup: $BK.tgz ($(wc -l < "$BK.had") files as they were); to undo this install: sudo bash $BK.restore"
echo "== unpacking the node bundle into /"
tail -n +$LINE "$0" | tar xzf - -C / || { echo "fdf-install: unpack failed; to undo: sudo bash $BK.restore"; exit 1; }
cat /opt/fdf/BUILD
# [ONE_MONITOR_TRUTH_V1 - T5] the bundle's sudoers fragment: getSnapshot.php (www-data) reads the hub link's handshake
# with exactly `wg show all latest-handshakes`. sudo wants it 0440; one that visudo refuses is taken out again (a bad
# fragment would stop sudo for everyone) and this install stops.
chmod 0440 /etc/sudoers.d/frognet-snapshot && visudo -cf /etc/sudoers.d/frognet-snapshot >/dev/null \
  || { rm -f /etc/sudoers.d/frognet-snapshot; echo "fdf-install: /etc/sudoers.d/frognet-snapshot refused by visudo; removed"; exit 1; }
# [FDF_WEB_UNDER_APACHE_V1 - T5b] the bundle's vhost (the tree's 000-default.conf) serves /opt/fdf/www/fdf at /fdf/ on
# the node's Apache. The vhost we install must be valid -- a bad one stops the install. But applying it to the running
# Apache, and the web view itself, are independent of region membership: a node is a member whether or not its local
# /fdf/ dashboard renders. So a failed apply is logged and the join goes ahead; the view catches up on the next restart.
apache2ctl configtest >/dev/null 2>&1 || { echo "fdf-install: apache2ctl configtest refuses the vhost:"; apache2ctl configtest; exit 1; }
# restart, not reload: `apachectl graceful` builds a transient mount namespace that systemd refuses on a long-up box
# (status=226/NAMESPACE); a restart recreates the service with a fresh namespace. One mechanism, applied once.
if systemctl restart apache2; then
  C=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/fdf/fdf_dashboard.html)
  [ "$C" = 200 ] && echo "== FD&F region view: http://<this node>/fdf/ (Apache, /opt/fdf/www/fdf)" \
    || echo "fdf-install: the region view answered HTTP $C, not 200; the join goes ahead -- the view is not part of membership (systemctl status apache2)"
else
  echo "fdf-install: systemctl restart apache2 failed; the join goes ahead -- the web view is not part of membership (systemctl status apache2)"
fi
# [WG_IMPL_RECORDED_V1 - T4b] the WireGuard implementation (the FrogNet installer recorded it; an older node records it here, once)
/opt/fdf/bin/fdf-wg-impl --record || { echo "fdf-install: no usable WireGuard implementation (above)"; exit 1; }
echo "== joining the region that invited this node"
exec /opt/fdf/bin/fdf-region-join "$INV"
__PAYLOAD_BELOW__
