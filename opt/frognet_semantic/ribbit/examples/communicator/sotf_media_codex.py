#!/usr/bin/env python3
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
"""
sotf_media_codex.py - SHIM. The SotF media/chat codec was SUBSUMED into the one SotF
handler (core/sotf_handler.py). There is no separate codex now: the handler IS the codec
- it packs/unpacks live AV frames, owns the control-plane LiveStream convergence over
tuple space, and converges the envelope - invoked like every other handler. This module
re-exports from the handler so existing
`from sotf_media_codex import pack_frame, unpack_frame, SotFMediaCodex` callers keep
working. New code imports from core.sotf_handler, or calls SOTF_MEDIA_HANDLER.pack_frame /
SOTF_MEDIA_HANDLER.open_stream(...).
"""
import os
import sys

_CORE = "/opt/frognet_semantic"
if os.path.isdir(_CORE) and _CORE not in sys.path:
    sys.path.insert(0, _CORE)

from core.sotf_handler import pack_frame, unpack_frame, _FRAME_HDR  # noqa: F401
from core import sotf_handler as _h

# Control-plane surface - present when the media substrate is on path (it is here).
if getattr(_h, "_HAVE_MEDIA_CODEX", False):
    SotFMediaCodex = _h.SotFMediaCodex          # noqa: F401
    LIVESTREAM_TYPE = _h.LIVESTREAM_TYPE         # noqa: F401
    CONTROL_FIELDS = _h.CONTROL_FIELDS           # noqa: F401
    CONTROL_FRESHNESS = _h.CONTROL_FRESHNESS     # noqa: F401
    _flag_key = _h._flag_key                     # noqa: F401
    _ls_key = _h._ls_key                         # noqa: F401
