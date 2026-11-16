#!/bin/bash
# build_wireguard_go.sh -- [WG_GO_BOTH_ARCHES_V1] wireguard-go 0.0.20230223 (the version the scale bed was first run
# on), static, for x86_64 and aarch64 from the same source, so experiment S uses the same WireGuard on both. The Go
# module proxy is not reachable from the build machine, so the three golang.org/x modules are taken from their GitHub
# mirrors at the exact versions wireguard-go's go.mod pins. Output: build/wg/wireguard-go-{x86_64,aarch64}.
set -eu; cd "$(dirname "$0")/.."; A=$PWD/build/arm-deps; mkdir -p "$A" build/wg
[ -d "$A/wireguard-go" ] || git clone -q https://github.com/WireGuard/wireguard-go.git "$A/wireguard-go"
git -C "$A/wireguard-go" checkout -q 0.0.20230223
for m in sys:v0.2.0 crypto:3147a52a75dd net:27dd8689420f; do r=${m%%:*}
  [ -d "$A/go-$r" ] || git clone -q https://github.com/golang/$r.git "$A/go-$r"; git -C "$A/go-$r" checkout -q ${m#*:}; done
cd "$A/wireguard-go"; trap 'git checkout -q go.mod go.sum' EXIT
go mod edit -replace golang.org/x/sys=../go-sys -replace golang.org/x/crypto=../go-crypto -replace golang.org/x/net=../go-net
# -checklinkname=0: the 2022 x/net links to syscall internals go >= 1.23 hides. Go before 1.23 (Ubuntu 24.04's golang-go
# is 1.22) has no such check and no such flag. [WG_GO_ON_DISTRO_GO_V1]
GOMINOR=$(go env GOVERSION | sed -E 's/^go1\.([0-9]+).*/\1/'); LN=""; [ "$GOMINOR" -ge 23 ] && LN="-checklinkname=0"
for a in amd64:x86_64 arm64:aarch64; do
  GOPROXY=off GOFLAGS=-mod=mod CGO_ENABLED=0 GOOS=linux GOARCH=${a%%:*} \
    go build -trimpath -ldflags "$LN -s -w" -o "$OLDPWD/build/wg/wireguard-go-${a##*:}" .; done
file "$OLDPWD"/build/wg/wireguard-go-*
