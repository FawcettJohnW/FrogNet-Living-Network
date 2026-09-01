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
# create_sensor_data.sh - create a row in sensor_data
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/frognet_common.sh"
SensorID="${1:?SensorID}"; shift
FrogID="${1:?FrogID}"; shift
jsonData="${1:?jsonData}"; shift
payload="$(json_payload "SensorID" "${SensorID}" "FrogID" "${FrogID}" "jsonData" "${jsonData}")"
api_post "sensor_data" "create" "$payload"
