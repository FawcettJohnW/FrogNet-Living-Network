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
set -x
export BASE_URL="http://databasehost.frognet/api.php"
source "$(dirname ${BASH_SOURCE[0]})/frognet_common.sh"

. /usr/local/bin/mapInterfaces
NetworkName=`hostname`

SensorName="${1:?SensorName}"; shift
SensorType="${1:?SensorType}"; shift
Tags="${1-}"; shift || true

IPAddress=`ip addr show $(ip route | awk '/default/ { print $5 }') | grep "inet" | head -n 1 | awk '/inet/ {print $2}' | cut -d'/' -f1`
SensorNetwork="${IPAddress%.*}.1"

existingSensor=`curl -sS "http://databasehost.frognet/api.php?entity=sensors&action=list&SensorName=$SensorName&SensorType=$SensorType"` 

if [[ $(echo "$existingSensor" | jq '.rows | length') -eq 0 ]]; then
    echo "The JSON array is empty."
    frogID=`uuidgen`
    payload="$(json_payload FrogID ${frogID} SensorAddress ${IPAddress} SensorNetwork ${SensorNetwork} SensorName ${SensorName} SensorType $SensorType Tags ${Tags})"
else
    echo "The JSON array is not empty."
    SensorID=$(echo "$existingSensor" | jq -r '.rows[0].SensorID')
    frogID=$(echo "$existingSensor" | jq -r '.rows[0].FrogID')
    payload="$(json_payload SensorID ${SensorID} FrogID ${frogID} SensorAddress ${IPAddress} SensorNetwork ${SensorNetwork} SensorName ${SensorName} SensorType $SensorType Tags ${Tags})"
fi

api_post "sensors" "create" "$payload"

existingSensor=`curl -sS "http://databasehost.frognet/api.php?entity=sensors&action=list&SensorName=$SensorName&SensorType=$SensorType"` 
if [[ $(echo "$existingSensor" | jq '.rows | length') -eq 0 ]]; then
    frogID=`uuidgen`
else
    SensorID=$(echo "$existingSensor" | jq -r '.rows[0].SensorID')
fi
existingSensorData=`curl -sS "http://databasehost.frognet/api.php?entity=sensor_data&action=list&SensorID=$SensorID"` 
if [[ $(echo "$existingSensorData" | jq '.rows | length') -eq 0 ]]; then
    /usr/local/bin/create_sensor_data.sh $SensorID $frogID "jsonData={}"
fi

# frog known_frognet create FROG-123 10.0.1.50 10.0.1.0/24 TempProbe-1 DS18B20 "lab,thermal"

# curl -s "http://databasehost.frognet/api.php?entity=sensor&action=create&NetworkName=$ourHost&IPAddress=$eth0Address" | jq .
# echo "curl --max-time 5 http://databasehost.frognet/createFrogNet.php?NetworkName=$ourHost&NetworkIP=$eth0Address&Tags=$tags"

# existingFrogNet=`curl --max-time 5 "http://databasehost.frognet/createFrogNet.php?NetworkName=$ourHost&NetworkIP=$eth0Address&Tags=$tags"`

# echo $existingFrogNet

    # frogNetHostName=$(echo "$existingFrogNet" | jq -r '.NetworkName')
    # ipAddress=$(echo "$existingFrogNet" | jq -r '.IPAddress')
    # frogID=$(echo "$existingFrogNet" | jq -r '.FrogNetID')
    # lastHeartbeat=$(echo "$existingFrogNet" | jq -r '.LastHeartbeat')
# 
    # echo "Created FrogNet ID $frogNetHostName"
    # echo "                   $ipAddress"
    # echo "                   $frogID"
    # echo "                   $lastHeartbeat"

# nwVerified=`/usr/local/bin/saveDNSData "$frogNetHostName" "$hostedNetworkPath" "$wlan0gatewayIP" "$wlan1gatewayIP" $eth0Name SDNS-2` 
