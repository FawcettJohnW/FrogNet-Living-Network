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
frognet_monitor/state.py — Application state container.
"""

from typing import Dict, List, Optional, Tuple

from .config import MODE_DASHBOARD


class AppState:
    def __init__(self):
        self.mode = MODE_DASHBOARD
        # [MERGE_IS_VISIBLE_V1] The result of the last M press. Transient: it
        # is a reply to a keystroke, not state, and it expires so a stale
        # message cannot read as current.
        self.merge_note: str = ""
        self.merge_note_ok: bool = True
        self.merge_note_at: float = 0.0
        self.hosts: List[Tuple[str, str]] = []       # [(ip, name), ...]
        self.results: Dict[str, Dict] = {}
        self.engine: Optional[Dict] = None      # SemanticProxy.Engine jsonData
        self.daemon: Optional[Dict] = None      # SemanticDaemon.Cache jsonData
        self.peer_data: Dict[str, Dict] = {}    # {peer_ip: jsonData}
        self.pipeline_data: Dict[str, Dict] = {} # {peer_ip: pipeline stats}
        # [LINK_POTENTIAL_PING_V1 2026-05-25] SemanticProxy.LinkQuality
        # sensor data — full snapshot dict with "peers" sub-dict
        # keyed by peer_ip.  Loaded once per DB refresh cycle by
        # _bg_db_fetch.  None until first fetch completes.  Per-peer
        # entries can have saturation_ratio_p95 == None if the
        # ladder hasn't run yet for that peer.
        self.link_quality: Optional[Dict] = None
        self.last_probe = 0.0
        self.last_db_fetch = 0.0
        self.probe_running = False
        self.db_fetch_running = False

        # Dashboard selection
        self.peer_cursor = 0

        # Sensor list
        self.sensor_host_name = ""
        self.sensor_host_ip = ""
        self.sensors: List[Dict] = []
        self.sensor_cursor = 0
        self.sensor_scroll = 0
        self.sensor_loading = False

        # Visible content rows of whichever overlay drew last (set at draw time
        # from overlay_content_height). Input handlers page by this instead of a
        # magic constant; only one overlay is active per loop iteration.
        self.overlay_visible_h = 0

        # JSON detail
        self.json_title = ""
        self.json_lines: List[str] = []
        self.json_scroll = 0
        self.json_sensor_name = ""
        self.json_fetched_at = 0.0
        self.json_refreshing = False

    @property
    def peer_count(self) -> int:
        return len(self.hosts)

    def clamp_peer_cursor(self):
        if self.peer_count > 0:
            self.peer_cursor = max(0, min(self.peer_cursor, self.peer_count - 1))
        else:
            self.peer_cursor = 0

    def clamp_sensor_cursor(self):
        n = len(self.sensors)
        if n > 0:
            self.sensor_cursor = max(0, min(self.sensor_cursor, n - 1))
        else:
            self.sensor_cursor = 0

    def selected_host(self) -> Optional[Tuple[str, str]]:
        if 0 <= self.peer_cursor < len(self.hosts):
            return self.hosts[self.peer_cursor]
        return None

    def selected_sensor(self) -> Optional[Dict]:
        if 0 <= self.sensor_cursor < len(self.sensors):
            return self.sensors[self.sensor_cursor]
        return None
