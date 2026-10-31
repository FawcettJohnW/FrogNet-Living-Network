#!/usr/bin/env bash
# Assemble one tarball per machine type: senz-node-<arch>-<build>.tgz, paths opt/senz/..., installed with
#   cd / && tar xvzf - < /tmp/senz-node-<arch>-<build>.tgz
#   DIST   output of build_portable.sh per arch: $DIST/<arch>/opt/senz/{bin,lib}
#   ENV_x86_64, ENV_aarch64   each architecture's ops runtime prefix (JDK 8 + MongoDB), built at /opt/senz/env
#          x86_64: MongoDB 5.0; aarch64: MongoDB 4.2, which runs on ARMv8.0 (Pi 4) -- 5.0 and later need ARMv8.2
#   JAR    the converted (repaired-order) backend: core-service-1.0.0.0.jar; JARLIB its lib/ directory
#   BUILD  the build tag; OUT where the tarballs go
set -eu
here="$(cd "$(dirname "$0")/.." && pwd)"
: "${DIST:?}" "${ENV_x86_64:?}" "${ENV_aarch64:?}" "${JAR:?}" "${JARLIB:?}" "${BUILD:?}" "${OUT:?}"
for arch in x86_64 aarch64; do
  root="$OUT/stage-$arch"; rm -rf "$root"; mkdir -p "$root/opt/senz"
  cp -a "$DIST/$arch/opt/senz/." "$root/opt/senz/"
  s="$root/opt/senz"; mkdir -p "$s/share/systemd" "$s/share/fixtures" "$s/var"
  install -m 0755 "$here/node/senz" "$here/node/senz-hwtest" "$s/bin/"
  cp "$here/node/systemd/"*.service "$s/share/systemd/"; cp "$here/fixtures/"*.json "$s/share/fixtures/"
  cp "$here/node/backend.properties.in" "$here/HARDWARE.md" "$s/share/"
  envvar="ENV_$arch"; cp -a "${!envvar}" "$s/env"
  mkdir -p "$s/backend/lib"; cp "$JAR" "$s/backend/"; cp "$JARLIB"/*.jar "$s/backend/lib/"
  echo "$BUILD $arch" > "$s/BUILD"
  tar -C "$root" -czf "$OUT/senz-node-$arch-$BUILD.tgz" opt
  echo "$OUT/senz-node-$arch-$BUILD.tgz $(du -h "$OUT/senz-node-$arch-$BUILD.tgz" | cut -f1)"
done
