#!/bin/bash
# make_core_linux.sh -- [CORE_BUNDLE_LINUX_V1] fdf-core-linux-<arch>-<build>.tgz: the FD&F core for a Linux machine that
# is not a FrogNet node (a laptop, a server, a VM, WSL with systemd), one tarball per processor:
#   fdf-core-<build>/install          looks at the machine, refuses the wrong one (saying which bundle is right), installs
#   fdf-core-<build>/opt/fdf/bin      fdf-start (leaf by invitation, or stand-alone), fdf-member-kt (+ lib/ and runtime/:
#                                     a Java runtime trimmed to java.base, jdk.net, jdk.crypto.ec), fdf-tun (WireGuard in
#                                     user space on a TUN), rina-host (the stand-alone control RAM), fdf-identity
#                        lib/python   fdfram.py (fdf-identity, the add-ins' tools)
#                        templates    fdf.prime (rina-host's primed shapes)
#                        BUILD        package, build, arch, member, built
# The add-ins (the Communicator, the kart race) are separate bundles (make_addin.sh). No var/ ships: the invitation and
# everything else this machine keeps are made there.
# Needs (x86_64 here): gradle (offline cache), JDK 21 (jlink), the static rina-host (rinab tools/build.sh +
# build_static_host.sh), tun/out/fdf-tun-<arch>. aarch64: rinab build_arm64.sh's rina-host, and the runtime from pypi's
# jdk4py linux aarch64 wheel relinked as the Windows bridge's is (windows/make_package.sh).
# usage: make_core_linux.sh OUTDIR [x86_64|aarch64]
set -eu
S=$(cd "$(dirname "$0")" && pwd); A=$(cd "$S/../frognet-android" && pwd); R=$(cd "$S/../fdf-source/rinab" && pwd); F=$(cd "$S/../.." && pwd)
O=$(mkdir -p "${1:?usage: make_core_linux.sh OUTDIR [arch]}" && cd "$1" && pwd); ARCH=${2:-$(uname -m)}
T=$(mktemp -d); trap 'rm -rf $T' EXIT
case "$ARCH" in
  x86_64)  RH=$R/build/bin/rina-host-static ;;
  aarch64) RH=$R/build/arm64/rina-host ;;
  *) echo "make_core_linux: arch $ARCH: x86_64 or aarch64" >&2; exit 2 ;;
esac
[ -x "$RH" ] || { echo "make_core_linux: no $RH (build it first: rinab tools/build.sh, then build_static_host.sh or build_arm64.sh)" >&2; exit 1; }
TUN=$A/tun/out/fdf-tun-$ARCH; [ -x "$TUN" ] || { echo "make_core_linux: no $TUN (tun/build.sh)" >&2; exit 1; }
(cd "$A" && gradle -q --offline -Pfdf.jvmOnly=true :member-cli:installDist 2>&1 | grep -v '^w:\|JAVA_TOOL' || true)
MB=$(sed -n 's/.*MEMBER_BUILD = "\(.*\)".*/\1/p' "$A/member/src/main/kotlin/com/fawcettinnovations/fdf/member/Member.kt")
B=${FDF_CORE_BUILD:-$(date -u +%Y-%m-%d)-${MB#fdf-kt-*-*-*-}}
P=$T/fdf-core-$B; H=$P/opt/fdf; mkdir -p $H/bin $H/lib/python $H/templates
# ---- the runtime
if [ "$ARCH" = "$(uname -m)" ]; then
  jlink --add-modules java.base,jdk.net,jdk.crypto.ec --strip-debug --no-header-files --no-man-pages --compress=zip-6 --output $H/runtime
else
  pip download "jdk4py>=21,<22" --platform manylinux2014_aarch64 --only-binary=:all: --no-deps -d $T/whl -q
  mkdir $T/x && (cd $T/x && unzip -q $T/whl/jdk4py-*.whl); J=$T/x/jdk4py/java-runtime
  jimage extract --dir $T/ex $J/lib/modules; python3 "$A/windows/strip_module_hashes.py" $T/ex/java.base/module-info.class
  jlink --module-path $T/ex --add-modules java.base,jdk.net,jdk.crypto.ec --strip-debug --no-header-files --no-man-pages --compress=zip-6 \
        --disable-plugin generate-jli-classes --disable-plugin generate-cds-archive --output $T/rt
  mkdir -p $H/runtime; cp -a $J/bin $J/conf $J/lib $J/release $H/runtime/; cp $T/rt/lib/modules $H/runtime/lib/modules; cp $T/rt/release $H/runtime/release
fi
# ---- the programs
cp "$S/core/fdf-start" "$S/core/fdf-member-kt" $H/bin/
cp "$A"/member-cli/build/install/fdf-member-kt/lib/*.jar $H/lib/
cp "$TUN" $H/bin/fdf-tun; cp "$RH" $H/bin/rina-host
cp "$F/bin/fdf-identity" $H/bin/; cp "$F/lib/python/fdfram.py" $H/lib/python/; cp "$F/templates/fdf.prime" $H/templates/
cp "$S/core/install" $P/install
chmod 755 $H/bin/* $P/install
JV=$(sed -n 's/JAVA_VERSION="\(.*\)"/\1/p' $H/runtime/release)
printf 'package=fdf-core\nbuild=%s\narch=%s\nmember=%s\njava=%s\nbuilt=%s\n' "$B" "$ARCH" "$MB" "$JV" "$(date -u +%Y-%m-%dT%H:%MZ)" > $H/BUILD
OUT=$O/fdf-core-linux-$ARCH-$B.tgz
tar -C $T -czf "$OUT" "fdf-core-$B"
ls -la "$OUT"; cat $H/BUILD
