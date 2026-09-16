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
# [INSTRUMENTATION_V2_APPLIED]
from frognet_trace import trace_enter, trace_event
"""
Broker HTTP helpers.
Reads BROKER_URL and GROUP_TOKEN from config at call time (not import time)
so load_config() can populate them before first use.
"""

import json
import ssl
import urllib.request

from . import config


def broker_get(path: str) -> dict:
    trace_enter('broker.broker_get', path=repr(path))
    url = f"{config.BROKER_URL}{path}"
    url += ("&" if "?" in url else "?") + f"group_token={config.GROUP_TOKEN}"
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with urllib.request.urlopen(
            urllib.request.Request(url), timeout=15, context=ctx) as resp:
        return json.loads(resp.read().decode())


def broker_post(path: str, body: dict) -> dict:
    trace_enter('broker.broker_post', path=repr(path), body=repr(body))
    url = f"{config.BROKER_URL}{path}"
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
        return json.loads(resp.read().decode())
