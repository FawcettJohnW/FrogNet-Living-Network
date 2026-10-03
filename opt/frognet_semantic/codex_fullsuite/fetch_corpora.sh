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
# fetch_corpora.sh — pull the full public language test suites for box_fullsuite.py.
# Self-contained: clones into ./corpora next to this script (or $FROGNET_CORPORA).
# Run on a NETWORKED host. Idempotent. No pipefail (FrogNet house rule).
set -eu
export GIT_TERMINAL_PROMPT=0   # fail fast instead of prompting for creds

ROOT="${FROGNET_CORPORA:-$(cd "$(dirname "$0")" && pwd)/corpora}"
mkdir -p "$ROOT"
cd "$ROOT"
echo "fetching corpora into: $ROOT"

clone() {  # repo  dir
  if [ -d "$2/.git" ]; then
    echo "  $2 present — pulling"; git -C "$2" pull --ff-only --quiet || true
  else
    echo "  cloning $1 -> $2"; git clone --depth 1 --quiet "$1" "$2"
  fi
}

# JSON — Nicolas Seriot's exhaustive corpus (y_/n_/i_).
clone https://github.com/nst/JSONTestSuite.git JSONTestSuite

# HTML — html5lib tree-construction .dat suites (WHATWG conformance).
clone https://github.com/html5lib/html5lib-tests.git html5lib-tests

# XML — W3C XML Test Suite (canonical tarball; the suite is NOT on GitHub).
if [ ! -d xmlts/xmlconf ]; then
  echo "  fetching W3C xmlts tarball -> xmlts/"
  mkdir -p xmlts && ( cd xmlts && curl -fsSL https://www.w3.org/XML/Test/xmlts20130923.tar.gz | tar xz ) \
    || echo "  (W3C xmlts fetch failed — JSON+HTML still scored; supply xmlts/xmlconf manually if needed)"
fi

echo "done."
echo "optional (HTML WHATWG conformance %): pip install --break-system-packages html5lib"
echo "then:  python3 box_fullsuite.py"
