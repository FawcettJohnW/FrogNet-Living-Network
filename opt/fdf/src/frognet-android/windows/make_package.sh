#!/bin/bash
# make_package.sh -- [BRIDGE_WINDOWS_V1] fdf-windows.zip: fdf-bridge.cmd, fdf-tun.exe, the member's jars, and a Java runtime
# trimmed to what the member uses (java.base, jdk.net, jdk.crypto.ec: 12 MB of modules instead of 64).
# The runtime: OpenJDK 21 for Windows x64 from pypi's jdk4py wheel (no JDK download site is reachable from the build
# machine); its module image is exploded with this machine's JDK 21 `jimage`, java.base's ModuleHashes attribute dropped
# (strip_module_hashes.py: jlink cannot hash exploded modules), and relinked by this machine's `jlink` -- a Windows
# image, since java.base says windows-amd64. The Windows natives of only those modules are kept.
# Needs: JDK 21 (jimage, jlink), Go (tun/build.sh), gradle, pip, zip. usage: windows/make_package.sh OUTDIR
set -eu; A=$(cd "$(dirname "$0")/.." && pwd); O=${1:?usage: make_package.sh OUTDIR}; mkdir -p "$O"; T=$(mktemp -d); trap 'rm -rf $T' EXIT
"$A/tun/build.sh"; (cd "$A" && /opt/gradle/bin/gradle -q --offline -Pfdf.jvmOnly=true :member-cli:installDist)
pip download "jdk4py>=21,<22" --platform win_amd64 --only-binary=:all: --no-deps -d $T/whl -q
mkdir $T/x && (cd $T/x && unzip -q $T/whl/jdk4py-*.whl); S=$T/x/jdk4py/java-runtime
jimage extract --dir $T/ex $S/lib/modules; python3 "$A/windows/strip_module_hashes.py" $T/ex/java.base/module-info.class
jlink --module-path $T/ex --add-modules java.base,jdk.net,jdk.crypto.ec --strip-debug --no-header-files --no-man-pages --compress=zip-6 \
      --disable-plugin generate-jli-classes --disable-plugin generate-cds-archive --output $T/rt
W=$T/pkg/fdf-windows; mkdir -p $W/lib $W/etc $W/var; R=$W/runtime; mkdir -p $R
cp -a $S/bin $S/conf $S/lib $R/; cp $T/rt/lib/modules $R/lib/modules; cp $T/rt/release $R/release; rm -f $R/bin/server/classes*.jsa
mkdir -p $R/legal; for m in java.base jdk.net jdk.crypto.ec; do cp -a $S/legal/$m $R/legal/; done
(cd $R/bin && rm -f awt.dll fontmanager.dll freetype.dll javajpeg.dll lcms.dll mlib_image.dll splashscreen.dll jawt.dll jdwp.dll attach.dll \
   management*.dll instrument.dll prefs.dll j2gss.dll j2pcsc.dll j2pkcs11.dll sunmscapi.dll w2k_lsa_auth.dll dt_socket.dll dt_shmem.dll \
   rmi.dll jsound.dll jaas.dll sspi_bridge.dll javaaccessbridge.dll *.exe.tmp jabswitch.exe jfr.exe jcmd.exe jrunscript.exe rmiregistry.exe \
   kinit.exe klist.exe ktab.exe jinfo.exe jmap.exe jps.exe jstack.exe jstat.exe jwebserver.exe keytool.exe)
cp "$A/windows/fdf-bridge.cmd" "$A/windows/README.txt" $W/; cp "$A/windows/etc/ponds.txt" $W/etc/; cp "$A/tun/out/fdf-tun.exe" $W/
cp "$A"/member-cli/build/install/fdf-member-kt/lib/*.jar $W/lib/
B=$(sed -n 's/.*MEMBER_BUILD = "\(.*\)".*/\1/p' "$A/member/src/main/kotlin/com/fawcettinnovations/fdf/member/Member.kt")
F=$(sed -n 's/^const build = "\(.*\)"/\1/p' "$A/tun/main.go")
echo "$B + $F, Java $(sed -n 's/JAVA_VERSION="\(.*\)"/\1/p' $R/release) (java.base, jdk.net, jdk.crypto.ec)" > $W/BUILD.txt
for f in $W/fdf-bridge.cmd $W/README.txt $W/etc/ponds.txt $W/BUILD.txt; do sed -i 's/\r$//; s/$/\r/' $f; done
(cd $T/pkg && rm -f "$O/fdf-windows.zip" && zip -qr9 "$O/fdf-windows.zip" fdf-windows); ls -la "$O/fdf-windows.zip"; cat $W/BUILD.txt
