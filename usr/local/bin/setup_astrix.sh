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

# Ensure the script is run as root
if [ "$EUID" -ne 0 ]; then
  echo "Please run this script using sudo or as root."
  exit 1
fi

echo "==============================================="
echo " Configuring Asterisk Extensions 101 and 102  "
echo "==============================================="

# 1. Back up existing configuration files if they haven't been backed up yet
[ ! -f /etc/asterisk/pjsip.conf.bak ] && cp /etc/asterisk/pjsip.conf /etc/asterisk/pjsip.conf.bak
[ ! -f /etc/asterisk/extensions.conf.bak ] && cp /etc/asterisk/extensions.conf /etc/asterisk/extensions.conf.bak

# 2. Write a clean pjsip.conf configuration
echo "Writing /etc/asterisk/pjsip.conf..."
cat << 'EOF' > /etc/asterisk/pjsip.conf
[transport-udp]
type=transport
protocol=udp
bind=0.0.0.0

; === EXTENSION 101 (Real IP Phone) ===
[101]
type=aor
max_contacts=1

[101]
type=auth
auth_type=userpass
username=101
password=YourSecretPassword101

[101]
type=endpoint
context=from-internal
disallow=all
allow=ulaw
allow=alaw
auth=101
aors=101

; === EXTENSION 102 (Android Phone) ===
[102]
type=aor
max_contacts=1

[102]
type=auth
auth_type=userpass
username=102
password=YourSecretPassword102

[102]
type=endpoint
context=from-internal
disallow=all
allow=ulaw
allow=alaw
auth=102
aors=102
EOF

# 3. Append the routing dialplan to extensions.conf
echo "Appending context to /etc/asterisk/extensions.conf..."

# Strip any existing [from-internal] to prevent duplication if re-run
sed -i '/\[from-internal\]/,$d' /etc/asterisk/extensions.conf

cat << 'EOF' >> /etc/asterisk/extensions.conf

[from-internal]
; Dial 101, ring for 20 seconds
exten => 101,1,Dial(PJSIP/101,20)
exten => 101,n,Hangup()

; Dial 102, ring for 20 seconds
exten => 102,1,Dial(PJSIP/102,20)
exten => 102,n,Hangup()
EOF

# 4. Correct permissions just in case
chown -R asterisk:asterisk /etc/asterisk/

# 5. Tell Asterisk to reload the engine configuration
echo "Reloading Asterisk modules..."
asterisk -rx "pjsip reload"
asterisk -rx "dialplan reload"

echo "==============================================="
echo " Setup complete! Next Steps: "
echo " 1. Grab your Pi's IP address by running: hostname -I"
echo " 2. Register Phone 1 (Extension: 101 / Password: YourSecretPassword101)"
echo " 3. Register Phone 2 (Extension: 102 / Password: YourSecretPassword102)"
echo " 4. Check registration status via: asterisk -rx 'pjsip show endpoints'"
echo "==============================================="

