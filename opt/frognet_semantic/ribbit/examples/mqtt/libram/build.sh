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
# build.sh -- libmosquitto.so.1 on the memory ([CLIENT_ON_THE_MEMORY_V1]): this directory's client, the platform's session
# (ribbit/cpp), the memory itself (ram_host: a process may hold it, MQTT_MEMORY=here), and Mosquitto's own pure helpers unchanged (libcommon: topics, properties, strings, UTF-8; lib/helpers.c:
# subscribe_simple and subscribe_callback, written on the public API). The stock version script: the same symbols.
set -euo pipefail
cd "$(dirname "$0")"
S=../stock; R=../../../cpp
[ -f $S/libcommon/libmosquitto_common.a ] || make -C $S/libcommon > /dev/null
g++ -O2 -g -std=c++20 -fPIC -Wall -Wextra -Werror -Wno-free-nonheap-object -I$R/include -I$S/include -c libmosquitto_ram.cpp -o libmosquitto_ram.o
g++ -O2 -g -std=c++20 -fPIC -I$R/include -c $R/src/frogram.cpp -o frogram.o
g++ -O2 -g -std=c++20 -fPIC -I$R/include -c $R/src/ram_host.cpp -o ram_host.o   # [NODE_IS_ITS_OWN_RAM_V1] MQTT_MEMORY=here
g++ -O2 -g -std=c++20 -fPIC -I$R/include -c $R/src/lf_ebr.cpp -o lf_ebr.o
g++ -O2 -g -std=c++20 -fPIC -Wall -Wextra -Werror -Wno-free-nonheap-object -I$R/include -c $R/src/ribbit_space.cpp -o ribbit_space.o   # [SPACE_V1]
g++ -O2 -g -std=c++20 -fPIC -Wall -Wextra -Werror -Wno-free-nonheap-object -I$R/include -c $R/src/ribbit_pp.cpp -o ribbit_pp.o   # [API_PLUS_PLUS_V2]
gcc -O2 -g -fPIC -I$S -I$S/include -I$S/lib -I$S/libcommon -I$S/deps -c $S/lib/helpers.c -o helpers.o
g++ -shared -fPIC -Wl,--version-script=linker.version -Wl,-soname,libmosquitto.so.1 -o libmosquitto.so.1 \
    libmosquitto_ram.o ribbit_space.o ribbit_pp.o frogram.o ram_host.o lf_ebr.o helpers.o -Wl,--whole-archive $S/libcommon/libmosquitto_common.a -Wl,--no-whole-archive \
    -llz4 -lcrypto -lpthread
ln -sf libmosquitto.so.1 libmosquitto.so
missing=$(comm -23 <(nm -D --defined-only $S/lib/libmosquitto.so.1 | awk '$2=="T"{print $3}' | sed 's/@.*//' | sort) \
                   <(nm -D --defined-only libmosquitto.so.1 | awk '$2=="T"{print $3}' | sed 's/@.*//' | sort))
if [ -n "$missing" ]; then echo "build FAIL: stock exports this library lacks:"; echo "$missing"; exit 1; fi
echo "build PASS: libmosquitto.so.1 on the memory, every stock export present"
