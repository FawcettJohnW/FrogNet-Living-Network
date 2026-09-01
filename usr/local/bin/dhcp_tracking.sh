#!/bin/bash
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC              #
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
#
# /usr/local/bin/dhcp_tracking.sh
# Thin wrapper called by dnsmasq via dhcp-script= directive.
# Delegates to dhcp_tracking.bash which handles the actual logic.
#
exec /usr/local/bin/dhcp_tracking.bash "$@"
