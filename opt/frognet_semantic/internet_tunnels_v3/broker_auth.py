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
"""[BROKER_AUTH_V1] Prove the pond password; never send it.

The registration body used to contain the pond password as a literal field:

    {"pond": ..., "pubkey": ..., "pond_password": "<the actual secret>"}

Sent as a token, it can be captured by anyone who terminates the TLS
connection - which, before broker_pin.py, was any on-path interceptor, because
the client did not authenticate the broker. Even with pinning, a token on the
wire is a token that first-contact interception could still lift.

The fix is to stop sending the secret and instead prove possession of it. Both
sides already hold the pond password (it is the pre-shared pond secret). So:

    1. Client asks the broker for a nonce (GET /api/v4/register-challenge).
    2. Client sends the registration WITHOUT the password, plus
           auth = HMAC-SHA256(pond_password, nonce || pond || pubkey || guid)
    3. Broker, which also holds the password, recomputes the HMAC over the
       fields it received and its issued nonce, and admits the node iff they
       match.

An interceptor who does not know the password cannot produce a valid auth, so
it cannot register as the node; and because the password itself is never
transmitted, capturing the exchange yields nothing to replay against a
different pubkey (the pubkey is inside the MAC). Binding the pubkey and guid
into the MAC also stops a captured auth being reused to enrol a DIFFERENT key.

This needs a matching broker side: a /api/v4/register-challenge route that
mints and briefly stores a nonce, and a register handler that verifies the MAC
instead of comparing a plaintext password. The broker source is not in this
tree; [BROKER_AUTH_V1] marks the client half, and the broker half must land
before the old plaintext field is removed from the accepted set. Until then,
send_password_fallback() controls whether the legacy field is still included,
so client and broker can be rolled in either order without a flag day.
"""

import hashlib
import hmac
import os

try:
    from frognet_trace import trace_event
except Exception:                                   # pragma: no cover
    def trace_event(*_a, **_k):
        return None

# [BROKER_AUTH_V1] While the broker still expects the plaintext field, keep
# sending it so a client update does not lock a node out of an un-updated
# broker. Flip to "0" (via this env var or by editing the default) once every
# broker verifies the HMAC. The HMAC is ALWAYS sent regardless, so flipping
# this is a pure removal of the legacy secret from the wire.
def send_password_fallback() -> bool:
    return os.environ.get("FROGNET_BROKER_SEND_PASSWORD", "1") == "1"


def compute_auth(pond_password: str, nonce: str, pond: str,
                 pubkey: str, guid: str) -> str:
    """HMAC-SHA256 proving knowledge of pond_password, bound to this request.

    The message commits to the nonce (freshness / anti-replay), the pond
    (scope), and the pubkey+guid (so a captured MAC cannot enrol a different
    identity). Field separators are newlines, which none of the fields can
    contain.
    """
    msg = "\n".join(["frognet-register-v1", nonce, pond, pubkey, guid]).encode("utf-8")
    key = pond_password.encode("utf-8")
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


def verify_auth(pond_password: str, nonce: str, pond: str, pubkey: str,
                guid: str, presented: str) -> bool:
    """Broker-side check. Constant-time compare of the expected MAC."""
    expected = compute_auth(pond_password, nonce, pond, pubkey, guid)
    return hmac.compare_digest(expected, presented or "")


def build_register_body(base: dict, pond_password: str, nonce: str) -> dict:
    """Return the registration body with proof-of-possession attached.

    `base` must already contain pond, pubkey and guid. The plaintext password
    is included only while send_password_fallback() is true; the HMAC is always
    included.
    """
    pond   = str(base.get("pond", ""))
    pubkey = str(base.get("pubkey", ""))
    guid   = str(base.get("guid", ""))
    body = dict(base)
    body["auth_nonce"] = nonce
    body["auth"] = compute_auth(pond_password, nonce, pond, pubkey, guid)
    if send_password_fallback():
        body["pond_password"] = pond_password
    else:
        body.pop("pond_password", None)
    trace_event("broker_auth.body_built",
                with_password=send_password_fallback(), has_auth=True)
    return body
