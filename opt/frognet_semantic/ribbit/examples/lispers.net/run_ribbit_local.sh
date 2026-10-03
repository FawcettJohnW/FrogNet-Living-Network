#!/bin/sh
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
set -eu
cd "$(dirname "$0")"
RIBBIT=${RIBBIT:-$(cd ../../cpp && pwd)}   # the Ribbit platform (ribbit/cpp): the client library comes from there
g++ -std=c++17 -O2 -pthread -Iribbit_cpp -I"$RIBBIT/include" ribbit_cpp/ribbit_lisp.cpp "$RIBBIT/src/frogram.cpp" -llz4 -lcrypto -o ribbit_cpp/ribbit-lisp
LISP_ALLOW_MUTATION=1 exec python run_conformance.py --adapter command --command './ribbit_cpp/ribbit-lisp' --capabilities system.get,map_cache.list,map_cache.get,map_cache.add,map_cache.delete,map_cache.wait,map_resolver.add,map_resolver.get,map_resolver.delete,map_resolver.wait,database_mapping.add,database_mapping.get,database_mapping.wait,database_mapping.delete,registration.put,registration.delete,resolution.get,resolver.wait,resolver.wait_site,resolver.wait_ddt,resolver.stats,transport.stats,wire.register4,wire.request4,wire.etr_request4,wire.register6,wire.request6,wire.etr_request6,wire.auth_verify,wire.register4_notify,wire.etr_register4,wire.etr_notify4,etr_map_server.add,etr_map_server.get,etr_map_server.delete,etr_map_server.wait,site.add,site.delete,ddt.add,ddt.get,ddt.delete
