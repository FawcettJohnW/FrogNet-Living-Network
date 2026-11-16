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
"""[SIM_OWNS_ITS_ENV_V1] The simulator runs from a checkout, not from an installed node.

Import this first. It points the simulation at the checkout's own files and at a scratch blob store, so nothing it
runs reads or writes /opt/frognet_semantic or /usr/local/bin. Those are a node's paths. A clean machine has neither,
and a machine with a node installed must not have the simulator touch them.

  FROGNET_PROXY_ROOT  the checkout's opt/frognet_semantic
  FROGNET_BIN_ROOT    the checkout's usr/local/bin (mapInterfaces is read from here: proxy/netutil.py)
  FROGNET_BLOB_ROOT   a new directory for this run (the semantic wire's blob store; core/blob_store.py, semtpl.hpp)
  FROGNET_DB_CONFIG   $FROGSIM_HOME/DB_CONFIG.json (~/.frogsim): the simulator's own database credential, which
                      setup.sh writes; never the node's /opt/frognet_semantic/DB_CONFIG.json

A value the caller set in the environment is the caller's configuration and is kept. Must be imported before
anything that reads these at import (core.blob_store, proxy.netutil).
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
FS = os.path.dirname(HERE)                                  # opt/frognet_semantic in the checkout
ROOT = os.path.dirname(os.path.dirname(FS))                 # the checkout

os.environ.setdefault("FROGNET_PROXY_ROOT", FS)
os.environ.setdefault("FROGNET_BIN_ROOT", os.path.join(ROOT, "usr", "local", "bin"))
FROGSIM_HOME = os.environ.setdefault("FROGSIM_HOME", os.path.expanduser("~/.frogsim"))
os.environ.setdefault("FROGNET_DB_CONFIG", os.path.join(FROGSIM_HOME, "DB_CONFIG.json"))
if "FROGNET_BLOB_ROOT" not in os.environ:
    # [SIM_TMP_MUST_NOT_MATCH_THE_MERGE_GLOB_V1] not /tmp/frognet*: runMerge.bash removes that glob.
    os.environ["FROGNET_BLOB_ROOT"] = tempfile.mkdtemp(prefix="frogsim_blob_")
os.makedirs(os.environ["FROGNET_BLOB_ROOT"], exist_ok=True)
for p in (FS, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)
pp = os.environ.get("PYTHONPATH", "")
if FS not in pp.split(os.pathsep):
    os.environ["PYTHONPATH"] = FS + (os.pathsep + pp if pp else "")   # the tiers run_all starts as processes
print(f"[SIM_ENV] FROGNET_BIN_ROOT={os.environ['FROGNET_BIN_ROOT']} FROGNET_BLOB_ROOT={os.environ['FROGNET_BLOB_ROOT']} "
      f"FROGNET_DB_CONFIG={os.environ['FROGNET_DB_CONFIG']}", flush=True)
