#!/bin/bash
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
# get_gps_data.bash -- [ONE_TPV_WITH_ITS_FIX_V1] one gpsd TPV object on stdout, for gpsd_reporter.bash (metric_upsert GPS
# Position). gpsd reports a TPV per device it holds; a node can have more than one (Seattle7, 2026-11-01: /dev/ttyACM1
# had a 3D fix while /dev/ttyACM0 reported a TPV with no position). This printed whatever TPV lines came first, every
# device's and more than one, so the stored reading was the no-fix device's, or several objects the reporter could
# not parse. Now: about 10 s of gpsd's reports; the newest TPV that has a position (mode 2 or 3, lat/lon) is the
# reading; if no device has one, the newest TPV is (an honest "no fix"); if gpsd says nothing, exit 1 and the
# reporter publishes its error.
lines=$(timeout 12 gpspipe -w -n 40 2>/dev/null | tr -d '\r' | grep '"class":"TPV"')
fix=$(printf '%s\n' "$lines" | jq -c 'select((.mode // 0) >= 2 and .lat != null and .lon != null)' 2>/dev/null | tail -1)
if [ -n "$fix" ]; then echo "$fix"; exit 0; fi
last=$(printf '%s\n' "$lines" | grep -v '^$' | tail -1)
if [ -n "$last" ]; then echo "$last"; exit 0; fi
echo "get_gps_data: gpsd reported no TPV in 12 s" >&2
exit 1
