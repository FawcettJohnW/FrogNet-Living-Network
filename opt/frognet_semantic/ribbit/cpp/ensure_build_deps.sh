#!/bin/bash
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
#
# ensure_build_deps.sh -- [AUTO_INSTALL_BUILD_DEPS_V1] sourced by every Ribbit build and qualify script before it
# compiles. The platform and every example link lz4 and OpenSSL; when a header is missing this installs its package
# with apt-get, then checks again. A package that will not install stops the build with its name -- never a build
# that goes on to fail later with a compiler error.
#
# Uses $CXX if the caller set it (default g++). Needs root or sudo to install.
ribbit_ensure_build_deps() {
  local cxx="${CXX:-g++}" missing=() hp h pkg
  for hp in "lz4frame.h:liblz4-dev" "openssl/hmac.h:libssl-dev"; do
    h=${hp%%:*}; pkg=${hp##*:}
    echo "#include <$h>" | $cxx -x c++ -fsyntax-only - 2>/dev/null || missing+=("$pkg")
  done
  [ ${#missing[@]} -eq 0 ] && return 0
  local sudo=""
  if [ "$(id -u)" -ne 0 ]; then
    command -v sudo >/dev/null || { echo "build FAIL: need ${missing[*]} and not root (no sudo to install them)" >&2; exit 1; }
    sudo="sudo"
  fi
  echo "  installing missing build packages: ${missing[*]}"
  # Refresh the package lists first. Its exit status is reported, not fatal: one unreachable third-party source makes
  # apt-get update exit non-zero while the distribution's own lists refresh fine. What decides the build is whether the
  # packages install and the headers are then present -- both checked below, both fatal.
  $sudo apt-get update -q || echo "  note: apt-get update reported errors (see above); installing from the lists it has"
  $sudo apt-get install -y -q "${missing[@]}" || { echo "build FAIL: apt-get install ${missing[*]} failed" >&2; exit 1; }
  for hp in "lz4frame.h:liblz4-dev" "openssl/hmac.h:libssl-dev"; do
    h=${hp%%:*}; pkg=${hp##*:}
    echo "#include <$h>" | $cxx -x c++ -fsyntax-only - 2>/dev/null \
      || { echo "build FAIL: <$h> still not found after installing $pkg" >&2; exit 1; }
  done
  echo "  installed: ${missing[*]}"
}
