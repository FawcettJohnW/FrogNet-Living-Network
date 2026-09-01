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
# clear_tables.sh — Clear templates + sensors from FrogNet DB
#
# Run on any node with local MySQL access.

set -eu

echo "Clearing FrogNet tables on $(hostname)..."

mysql -u root FrogNet << 'SQL'
TRUNCATE TABLE frognet_request_templates;
TRUNCATE TABLE frognet_response_templates;
DELETE FROM SensorData;
DELETE FROM Sensor;
SQL

echo "Done."
