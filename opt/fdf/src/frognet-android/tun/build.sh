#!/bin/bash
# build.sh -- fdf-tun for every place it runs: the app's jniLibs (libfdftun.so per ABI), out/ (Linux, netns tests) and
# out/fdf-tun.exe (Windows x64: beside wintun.dll from wintun.net).
# Static, no cgo; every module it needs is in third_party (no network). Go >= 1.23.
set -eu; cd "$(dirname "$0")"
J=../app/src/main/jniLibs; mkdir -p out $J/arm64-v8a $J/armeabi-v7a $J/x86_64
b() { GOOS=linux GOARCH=$1 GOARM=${2:-} CGO_ENABLED=0 GOPROXY=off GOFLAGS=-mod=mod go build -trimpath -ldflags="-s -w" -o "$3" .; echo "$3"; }
GOPROXY=off GOFLAGS=-mod=mod go vet . && GOPROXY=off GOFLAGS=-mod=mod go test .
# the app's: -tags fdfandroid = upstream wireguard-go's Android behaviour (no netlink route socket, no sticky sockets)
T=fdfandroid
b() { GOOS=linux GOARCH=$1 GOARM=${2:-} CGO_ENABLED=0 GOPROXY=off GOFLAGS=-mod=mod go build -tags "$T" -trimpath -ldflags="-s -w" -o "$3" .; echo "$3${T:+ ($T)}"; }
b arm64 "" $J/arm64-v8a/libfdftun.so; b arm 7 $J/armeabi-v7a/libfdftun.so; b amd64 "" $J/x86_64/libfdftun.so
T=
b amd64 "" out/fdf-tun-x86_64; b arm64 "" out/fdf-tun-aarch64
GOOS=windows GOARCH=amd64 CGO_ENABLED=0 GOPROXY=off GOFLAGS=-mod=mod go build -trimpath -ldflags="-s -w" -o out/fdf-tun.exe . && echo out/fdf-tun.exe
