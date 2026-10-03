#!/usr/local/bin/frognet_env/bin/python3
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
import json
import time
import socket
import requests
from sense_hat import SenseHat
from datetime import datetime

# Initialize the Sense HAT
sense = SenseHat()

hostName = socket.gethostname()
ipAddress = socket.gethostbyname(hostName)

# while True:
    # Get sensor data
temperature_humidity = sense.get_temperature_from_humidity()  # Temperature from humidity sensor
temperature_pressure = sense.get_temperature_from_pressure()  # Temperature from pressure sensor
humidity = sense.get_humidity()
pressure = sense.get_pressure()
orientation = sense.get_orientation_degrees() # Orientation in degrees
accelerometer_raw = sense.get_accelerometer_raw() # Raw accelerometer data
compass_raw = sense.get_compass_raw() # Raw compass data

# Create a dictionary to hold the sensor data
sense_data = {
    "SensorID": "SenseHat",
    "HostName": hostName,
    "IPAddress": ipAddress,
    "timestamp": datetime.now().isoformat(),  # Current time in ISO 8601 format
    "temperature": {
        "from_humidity": round(temperature_humidity, 2),  # Round to 2 decimal places
        "from_pressure": round(temperature_pressure, 2), # Round to 2 decimal places
    },
    "humidity": round(humidity, 2),
    "pressure": round(pressure, 2),
    "orientation": { # Pitch, roll, yaw
        "pitch": round(orientation["pitch"], 2),
        "roll": round(orientation["roll"], 2),
        "yaw": round(orientation["yaw"], 2),
    },
    "accelerometer_raw": { # x, y, z G-forces
        "x": round(accelerometer_raw["x"], 2),
        "y": round(accelerometer_raw["y"], 2),
        "z": round(accelerometer_raw["z"], 2),
    },
    "compass_raw": { # x, y, z magnetic field in microteslas
        "x": round(compass_raw["x"], 2),
        "y": round(compass_raw["y"], 2),
        "z": round(compass_raw["z"], 2),
    }
}

# Serialize the dictionary to a JSON string
json_output = json.dumps(sense_data, indent=4)  # Use indent for pretty printing
print(json_output)

# # Print the JSON output
# url = "http://databasehost.frognet/saveSensorData.php?jsonData="  # Replace with your target URL
# url = url + json.dumps(sense_data)
# response = requests.post(url, json=json_output)
# print(json_output)

# time.sleep(10)

