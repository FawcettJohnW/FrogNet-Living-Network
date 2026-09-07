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
import socket
import ssl
import urllib.request
from urllib.parse import urlsplit

from . import config
from . import broker_pin


def _pinned_context(url: str) -> ssl.SSLContext:
    """[BROKER_PIN_V1] Build the TLS context and enforce the cert pin.

    urllib does not surface the peer certificate before handing back the
    response body, so the pin is checked out-of-band first: fetch the cert the
    broker is presenting, compare it against the stored fingerprint (learning
    it on first use), and only then let the real request proceed over an
    encrypted channel. A mismatch raises before any request body - and hence
    any secret - is sent.

    This replaces the former CERT_NONE context, which encrypted to whoever
    answered without checking who that was. See broker_pin.py for why pinning
    rather than a CA.
    """
    parts = urlsplit(url)
    if parts.scheme != "https":
        # Plain http has nothing to pin; the broker URL is https in every
        # shipped config, and downgrade is itself a finding, so refuse.
        raise broker_pin.BrokerCertMismatch(
            f"refusing non-https broker URL: {url!r}")
    host = parts.hostname or ""
    port = parts.port or 443

    probe = ssl.create_default_context()
    probe.check_hostname = False
    probe.verify_mode = ssl.CERT_NONE
    with socket.create_connection((host, port), timeout=15) as raw:
        with probe.wrap_socket(raw, server_hostname=host) as tls:
            der = tls.getpeercert(binary_form=True)
    broker_pin.check_and_learn_pin(url, der)   # raises on mismatch
    return broker_pin.make_context(url)


def broker_get(path: str) -> dict:
    trace_enter('broker.broker_get', path=repr(path))
    url = f"{config.BROKER_URL}{path}"
    url += ("&" if "?" in url else "?") + f"group_token={config.GROUP_TOKEN}"
    ctx = _pinned_context(url)
    with urllib.request.urlopen(
            urllib.request.Request(url), timeout=15, context=ctx) as resp:
        return json.loads(resp.read().decode())


def broker_post(path: str, body: dict) -> dict:
    trace_enter('broker.broker_post', path=repr(path), body=repr(body))
    url = f"{config.BROKER_URL}{path}"
    ctx = _pinned_context(url)
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
        return json.loads(resp.read().decode())
