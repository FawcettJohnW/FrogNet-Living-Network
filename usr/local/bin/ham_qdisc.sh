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
# Bound queueing for ham0 to prevent bufferbloat on slow links
# Safe, reversible, no discovery impact


DEV="${1:-ham0}"

# Conservative defaults for low-speed links
TXQLEN="${HAM_TXQLEN:-50}"

# Clear existing qdisc
tc qdisc del dev "$DEV" root 2>/dev/null || true

# Apply fq_codel (works well at low rates)
tc qdisc add dev "$DEV" root fq_codel \
    limit 100 \
    flows 32 \
    target 100ms \
    interval 500ms \
    quantum 300

# Bound kernel transmit queue
ip link set dev "$DEV" txqueuelen "$TXQLEN"

echo "[HAM] qdisc applied to $DEV (txqueuelen=$TXQLEN)"
