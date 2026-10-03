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
# isMergeProcRunning.bash

SCRIPT_NAME="runMerge.bash"
# Use 'pgrep -f' to search the full command lines, and filter out the current script's PID
# The '$$' is the PID of the current shell script instance
OTHER_PIDS=$(pgrep -f "$SCRIPT_NAME" | grep -v "^$$")

# Count the number of other running instances
COUNT=$(echo "$OTHER_PIDS" | wc -l)

# Check if the count is greater than 0
if [ "$COUNT" -gt 0 ]; then
    echo " $OTHER_PIDS"
    # You can add code here to exit or handle the situation as needed
    exit 1
else
    echo "No other instances of $SCRIPT_NAME are running. Proceeding with execution."
    # The rest of your script goes here
fi

