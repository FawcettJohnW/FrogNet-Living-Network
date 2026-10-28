#!/bin/bash
# make_deliverables.sh -- [ONE_TARBALL_PER_MACHINE_V1] the three r-builds from this tree, every time, all three:
# Each file carries the build tag in its name (fdf-install-x86_64-<tag>.run ...): a re-sent file never lands as "name (1)".
#   fdf-install-aarch64.run   Pi nodes      /opt/fdf (aarch64 binaries) + the FrogNet region patch, behind header.sh
#   fdf-install-x86_64.run    x86 nodes     /opt/fdf (x86 static binaries) + the FrogNet region patch, behind header.sh
#   fdf-droplet.tgz           the droplet   /opt/fdf (x86 static binaries, rina-host static) + fdf-hub.service; the
#                                           machine's region (site key, hub.env, its RAM manager and member units) is
#                                           then made by /opt/fdf/bin/fdf-region-create AREA PUBLIC_IP, once, there
# Each carries /opt/fdf/BUILD (package=, build=, built=, for=). Tarred from / (paths are absolute-from-root).
# Never shipped: /opt/fdf/var (the machine's own state: join.env, site.env, area.env, facts.json, area.perm, the site
# key) and /opt/fdf/hub/hub.env (the region holder's hub settings, made by fdf-region-create -- shipping one would move
# the hub's port under its members). /opt/fdf/fdf.env ships and holds no operator setting (it sources var/area.env), so
# an update cannot overwrite one [OPERATOR_SETTINGS_SURVIVE_AN_UPDATE_V1]. No __pycache__.
# The FrogNet side of the node bundle (the "FrogNet region patch") is taken from the repo tree itself -- REPO, the
# directory holding opt/frognet_semantic, usr, etc and var -- never from a copy of it [MIRROR_IS_THE_TREE_V1]. The files
# it carries are listed here, explicitly (FROGNET_FILES); a file not listed is not shipped, a listed one missing stops
# the build.
# usage: tools/make_deliverables.sh OUTDIR      (after build.sh, build_static_host.sh, build_arm64.sh, the x86 clients)
set -eu
H=$(cd "$(dirname "$0")/.." && pwd); TOP=$(cd "$H/.." && pwd); OUT=${1:?usage: make_deliverables.sh OUTDIR}; mkdir -p "$OUT"
REPO=$(cd "$TOP/../../../.." && pwd)       # fdf-source is REPO/opt/fdf/src/fdf-source
for d in opt/frognet_semantic usr/local/bin etc var/www/html; do
  [ -d "$REPO/$d" ] || { echo "make_deliverables: $REPO/$d missing: $REPO is not the FrogNet repo top" >&2; exit 1; }; done
FROGNET_FILES=(
  etc/NetworkManager/dispatcher.d/90-frognet-merge
  etc/apache2/sites-available/000-default.conf
  etc/sudoers.d/frognet-snapshot
  opt/frognet_semantic/core/build_id.py
  opt/frognet_semantic/core/frognet_role_elect.py
  opt/frognet_semantic/core/frognet_service_hosts.py
  opt/frognet_semantic/core/not_frognet.py
  opt/frognet_semantic/core/region_capability.py
  opt/frognet_semantic/core/role_publish.py
  opt/frognet_semantic/discovery/dbhost_switch_sim.py
  opt/frognet_semantic/discovery/descend.py
  opt/frognet_semantic/discovery/discovery.py
  opt/frognet_semantic/discovery/elect.py
  opt/frognet_semantic/discovery/hosts.py
  opt/frognet_semantic/discovery/live.py
  opt/frognet_semantic/discovery/mapinterfaces.py
  opt/frognet_semantic/discovery/orchestrate.py
  opt/frognet_semantic/discovery/real_backends.py
  opt/frognet_semantic/discovery/resolv.py
  opt/frognet_semantic/discovery/sources.py
  opt/frognet_semantic/discovery/test_region_backends_oracle.py
  opt/frognet_semantic/internet_tunnels_v3/__main__.py
  opt/frognet_semantic/internet_tunnels_v3/config.py
  opt/frognet_semantic/internet_tunnels_v3/gen.py
  opt/frognet_semantic/internet_tunnels_v3/graph_apply.py
  opt/frognet_semantic/internet_tunnels_v3/poll.py
  opt/frognet_semantic/internet_tunnels_v3/region.py
  opt/frognet_semantic/internet_tunnels_v3/test_region_oracle.py
  opt/frognet_semantic/internet_tunnels_v3/tests/__init__.py
  opt/frognet_semantic/internet_tunnels_v3/tests/graph_apply_oracle.py
  opt/frognet_semantic/internet_tunnels_v3/wg.py
  usr/local/bin/frognet-merge-watcher.sh
  usr/local/bin/frognet_dbhost_elector.py
  usr/local/bin/frognet_monitor
  usr/local/bin/frognet_monitor_py/__main__.py
  usr/local/bin/frognet_monitor_py/discovery.py
  usr/local/bin/frognet_monitor_py/draw_dashboard.py
  usr/local/bin/frognet_monitor_py/draw_ponds.py
  usr/local/bin/frognet_monitor_py/identity.py
  usr/local/bin/frognet_monitor_py/main_loop.py
  usr/local/bin/frognet_monitor_py/once.py
  usr/local/bin/frognet_monitor_py/probes.py
  usr/local/bin/frognet_monitor_py/state.py
  usr/local/bin/mergeHostsAndResolv.bash
  usr/local/bin/propogateNotificationInternal
  usr/local/bin/runMerge.bash
  var/www/html/fln_dashboard32.html
  var/www/html/frognet_echo.php
  var/www/html/frognet_node.inc.php
  var/www/html/getHosts.php
  var/www/html/getSnapshot.php
  var/www/html/index.php
)
for f in "${FROGNET_FILES[@]}"; do [ -f "$REPO/$f" ] || { echo "make_deliverables: $REPO/$f missing" >&2; exit 1; }; done
TAG=$(sed -n 's/^#define FDF_BUILD_TAG "\(.*\)"/\1/p' "$H/src/fdf_version.hpp"); NOW=$(date -u +%Y-%m-%dT%H:%MZ)
for f in "$TOP/fdf/bin/fdf-facts" "$TOP/fdf/bin/fdf-hub" "$H/tools/fdf-hub" "$TOP/fdf/lib/python/fdf_graph.py" \
         "$REPO/opt/frognet_semantic/internet_tunnels_v3/region.py"; do
  grep -q "BUILD_TAG = \"$TAG\"" "$f" || { echo "make_deliverables: $f does not say BUILD_TAG = \"$TAG\"" >&2; exit 1; }; done
cmp -s "$TOP/fdf/bin/fdf-hub" "$H/tools/fdf-hub" || { echo "make_deliverables: fdf/bin/fdf-hub and rinab/tools/fdf-hub differ" >&2; exit 1; }
for b in "$H/build/static/"{fdf-client,fdf-events,fdf-participants,fdf-attention,fdf-where,ram} "$H/build/bin/rina-host-static" "$H/build/arm64/"{fdf-client,fdf-events,fdf-participants,fdf-attention,fdf-where,ram,rina-host}; do
  [ -x "$b" ] || { echo "make_deliverables: $b missing (build first)" >&2; exit 1; }
  [ "${b##*/}" = ram ] || strings "$b" | grep -q "$TAG" || { echo "make_deliverables: $b is not $TAG" >&2; exit 1; }; done
# [ADDIN_V1] the Communicator's servers are an add-in now (src/bundles/make_addin.sh comms), not in the node bundles
W=$(mktemp -d); trap 'rm -rf "$W"' EXIT
fdf_tree() {   # $1 = root, $2 = binary dir, $3.. = binary names (as installed: rina-host-static -> rina-host)
  local R=$1 BD=$2; shift 2; mkdir -p "$R/opt"
  cp -a "$TOP/fdf" "$R/opt/fdf"; rm -rf "$R/opt/fdf/var" "$R/opt/fdf/hub/hub.env" "$R/opt/fdf/www/dc"; find "$R" -name __pycache__ -prune -exec rm -rf {} +
  for b in "$@"; do cp "$BD/${b%%:*}" "$R/opt/fdf/bin/${b##*:}"; done
  chmod 755 "$R/opt/fdf/bin/"*; }      # every /opt/fdf/bin entry runs (the installer execs fdf-region-join)
frognet_patch() { local R=$1 f; for f in "${FROGNET_FILES[@]}"; do mkdir -p "$R/$(dirname "$f")"; cp -p "$REPO/$f" "$R/$f"; done; }
build_file() { printf 'package=%s\nbuild=%s\nbuilt=%s\nfor=%s\n' "$1" "$TAG" "$NOW" "$2" > "$3/opt/fdf/BUILD"; }
# [FILES_ONLY_TAR_V1] files only, no directory entries: extracting at / must not reset the owner or mode of /etc, /usr,
# /var/www/html or any other existing directory (a missing one is created).
tarit() { (cd "$1" && find . \( -type f -o -type l \) | sed 's|^\./||' | sort | tar --owner=0 --group=0 --no-recursion -czf - -T -); }
# ---- x86 node
R=$W/node; fdf_tree "$R" "$H/build/static" fdf-client fdf-events fdf-participants fdf-attention fdf-where ram; cp "$H/build/bin/rina-host-static" "$R/opt/fdf/bin/rina-host"
frognet_patch "$R"; build_file fdf-node.tgz "x86_64 FrogNet node or leaf" "$R"
tarit "$R" > "$W/fdf-node.tgz"; sed 's/__ARCH__/x86_64/' "$TOP/deliverables/header.sh" | cat - "$W/fdf-node.tgz" > "$OUT/fdf-install-x86_64-$TAG.run"
# ---- arm64 node
R=$W/arm; fdf_tree "$R" "$H/build/arm64" fdf-client fdf-events fdf-participants fdf-attention fdf-where ram rina-host
frognet_patch "$R"; build_file fdf-node-arm64.tgz "aarch64 FrogNet node (Pi)" "$R"
tarit "$R" > "$W/fdf-node-arm64.tgz"; sed 's/__ARCH__/aarch64/' "$TOP/deliverables/header.sh" | cat - "$W/fdf-node-arm64.tgz" > "$OUT/fdf-install-aarch64-$TAG.run"
# ---- droplet
R=$W/drop; fdf_tree "$R" "$H/build/static" fdf-client fdf-events fdf-participants fdf-attention fdf-where ram; cp "$H/build/bin/rina-host-static" "$R/opt/fdf/bin/rina-host"
mkdir -p "$R/etc"; cp -a "$TOP/deliverables/etc-droplet/systemd" "$R/etc/"
build_file fdf-droplet.tgz "region holder (droplet or LAN machine): RAM manager + hub, made by fdf-region-create AREA PUBLIC_IP" "$R"
tarit "$R" > "$OUT/fdf-droplet-$TAG.tgz"
# [ADDIN_V1] the kart service, its race hosts and its players are the kart add-in, DisasterComm (/dc) the dc add-in, and
# the Communicator's servers the comms add-in: neither is in these bundles.
# ---- [FROGNET_TREE_TARBALL_V1] the FrogNet base for a node with no git: the tree's node file set (the manifest's
# frognet_node_files, what frognet_install.sh --from-repo copies), laid out as the checkout is. On the node:
#   mkdir /tmp/fntree && cd /tmp/fntree && tar xzf /tmp/frognet-tree-<tag>.tgz && bash usr/local/bin/installer/frognet_install.sh --from-repo --preserve
RR=$(cd "$TOP/../../../.." && pwd); [ -f "$RR/usr/local/lib/frognet_world_manifest.sh" ] || { echo "make_deliverables: no FrogNet tree at $RR" >&2; exit 1; }
grep -qx "build=$TAG" "$RR/opt/frognet_semantic/BUILD" || { echo "make_deliverables: $RR/opt/frognet_semantic/BUILD does not say build=$TAG" >&2; exit 1; }
# [TREE_IS_WHAT_GIT_TRACKS_V1] only the files the checkout tracks: a tree where examples were built or tested (their
# build/, bin/, results/ -- ignored by git) would otherwise ship those too (seen: 1,148 extra files, 30 -> 51 MB)
git -C "$RR" ls-files > "$W/tracked.list" || { echo "make_deliverables: $RR is not a git checkout: the tree tarball is what it tracks" >&2; exit 1; }
( . "$RR/usr/local/lib/frognet_world_manifest.sh"; frognet_node_files "$RR" ) | grep -Fxf "$W/tracked.list" > "$W/tree.list"
tar -C "$RR" --owner=0 --group=0 -czf "$OUT/frognet-tree-$TAG.tgz" -T "$W/tree.list"
chmod +x "$OUT"/*.run
# [DIST_IN_THE_TREE_V1] FDF_DIST=<dir> (the tree's opt/fdf/dist): the same three files under their fixed names, which is
# where frognet_install.sh --from-repo --invite takes the node installer from. Delivered files keep their tagged names.
if [ -n "${FDF_DIST:-}" ]; then mkdir -p "$FDF_DIST"
  cp "$OUT/fdf-install-x86_64-$TAG.run" "$FDF_DIST/fdf-install-x86_64.run"; cp "$OUT/fdf-install-aarch64-$TAG.run" "$FDF_DIST/fdf-install-aarch64.run"
  cp "$OUT/fdf-droplet-$TAG.tgz" "$FDF_DIST/fdf-droplet.tgz"; echo "dist: $FDF_DIST holds $TAG under the fixed names"; fi
for f in "$OUT"/fdf-install-x86_64-$TAG.run "$OUT"/fdf-install-aarch64-$TAG.run "$OUT"/fdf-droplet-$TAG.tgz "$OUT"/frognet-tree-$TAG.tgz; do printf '%s  %s bytes  sha256 %s\n' "${f##*/}" "$(stat -c %s "$f")" "$(sha256sum "$f" | cut -c1-16)"; done
echo "built $TAG at $NOW"
