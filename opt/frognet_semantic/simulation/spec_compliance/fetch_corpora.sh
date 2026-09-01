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
# fetch_corpora.sh - pull the full public language test suites for box_fullsuite.py.
# Run on a NETWORKED host. Idempotent. Places corpora under ./corpora (or
# $FROGNET_CORPORA). No pipefail (FrogNet house rule).
set -eu

ROOT="${FROGNET_CORPORA:-$(cd "$(dirname "$0")" && pwd)/corpora}"
mkdir -p "$ROOT"
cd "$ROOT"
echo "fetching corpora into: $ROOT"

clone() {  # repo  dir
  if [ -d "$2/.git" ]; then
    echo "  $2 present - pulling"; git -C "$2" pull --ff-only --quiet || true
  else
    echo "  cloning $1 -> $2"; git clone --depth 1 --quiet "$1" "$2"
  fi
}

# JSON - Nicolas Seriot's exhaustive corpus (y_/n_/i_).
clone https://github.com/nst/JSONTestSuite.git JSONTestSuite

# HTML - html5lib tree-construction .dat suites (WHATWG conformance).
clone https://github.com/html5lib/html5lib-tests.git html5lib-tests

# XML - W3C XML Test Suite. Mirror repo (the canonical w3.org tarball may be
# blocked); this mirror carries the xmlconf tree with valid/ and not-wf/.
if [ ! -d xmlts/.git ]; then
  echo "  cloning W3C xmlts mirror -> xmlts"
  git clone --depth 1 --quiet https://github.com/Saxonica/XT-speed.git xmlts 2>/dev/null \
    || git clone --depth 1 --quiet https://github.com/w3c/xml-test-suite.git xmlts 2>/dev/null \
    || echo "  (xmlts mirror unavailable - JSON+HTML still scored; supply xmlts manually if needed)"
fi

echo "done. Optionally: pip install --break-system-packages html5lib  (for WHATWG conformance %)"
echo "then:        python3 -m simulation.spec_compliance.box_fullsuite"
