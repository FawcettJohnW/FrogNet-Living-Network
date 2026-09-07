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
"""[BROKER_PIN_V1] Trust-on-first-use certificate pinning for the broker.

WHY THIS EXISTS, and why it is not a CA.

The broker connection is the one place a FrogNet node reaches outside its own
LAN, and it is made before the WireGuard tunnel exists - registration is how
the node gets a tunnel, so it cannot be protected by one. It was made with
ssl.CERT_NONE and check_hostname=False, which keeps TLS encryption but discards
TLS authentication: the node forms an encrypted channel to whoever answers the
broker's name, without checking that it is the broker. On any network the node
does not control (cafe Wi-Fi, a hostile upstream, a poisoned resolver for the
broker's DNS name) an interceptor answers, the node connects to it, and the
registration body - which carries the pond password - is decrypted by the
interceptor. Nothing is "broken into"; the node hands the secret over.

FrogNet is self-forming with no central authority, so CA-signed certificates
are not available: there is no one to sign the broker's cert and no prior
contact over which to distribute a trusted root. TOFU is the standard answer to
exactly that shape (it is what SSH does with host keys, which also have no CA).

  - First successful connection: record the fingerprint of the cert the broker
    presented, under /etc/frognet/broker_pins/<host:port>.
  - Every later connection: require the presented cert to match the pin.

This does not protect the very first connection (an interceptor present at
first enrolment is pinned instead of the broker). That gap is closed by the
SECOND half of the fix, in broker_auth.py: the pond password is never sent as
a field, only proven via HMAC over a broker-supplied nonce, so a first-contact
interceptor who lacks the password cannot complete the exchange as the broker
and cannot replay what it captured. Pinning narrows the window to a single
connection; the HMAC removes the value of winning it.

[NO_FALLBACK_V1] If a pin exists and the presented cert does not match it, the
connection FAILS. There is no "warn and continue", because continue is the
whole attack.
"""

import hashlib
import os
import ssl
import stat
from pathlib import Path
from urllib.parse import urlsplit

try:
    from frognet_trace import trace_event
except Exception:                                   # pragma: no cover
    def trace_event(*_a, **_k):
        return None

PIN_DIR = Path(os.environ.get("FROGNET_BROKER_PIN_DIR", "/etc/frognet/broker_pins"))


def _pin_key(broker_url: str) -> str:
    """Stable filename component for a broker URL: host and port only."""
    parts = urlsplit(broker_url)
    host = (parts.hostname or "").lower()
    port = parts.port or (443 if parts.scheme == "https" else 80)
    # ':' is fine in a filename on ext4 but avoid it for tidiness.
    return f"{host}_{port}"


def _pin_path(broker_url: str) -> Path:
    return PIN_DIR / (_pin_key(broker_url) + ".sha256")


def _fingerprint(der: bytes) -> str:
    return hashlib.sha256(der).hexdigest()


def load_pin(broker_url: str) -> str:
    """Return the stored fingerprint for this broker, or '' if none yet."""
    p = _pin_path(broker_url)
    try:
        return p.read_text().strip().lower()
    except OSError:
        return ""


def save_pin(broker_url: str, fingerprint: str) -> None:
    """Record a fingerprint as the pin for this broker (first use only)."""
    try:
        PIN_DIR.mkdir(parents=True, exist_ok=True)
        os.chmod(PIN_DIR, stat.S_IRWXU)            # 0700; pins are integrity, keep them ours
    except OSError as e:
        trace_event("broker_pin.mkdir_failed", err=repr(e))
        return
    p = _pin_path(broker_url)
    tmp = p.with_suffix(".sha256.tmp")
    try:
        tmp.write_text(fingerprint.lower() + "\n")
        os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)  # 0600
        os.replace(tmp, p)
        trace_event("broker_pin.saved", broker=_pin_key(broker_url), fp=fingerprint[:16])
    except OSError as e:
        trace_event("broker_pin.save_failed", err=repr(e))
        try:
            tmp.unlink()
        except OSError:
            pass


class BrokerCertMismatch(ssl.SSLError):
    """Presented cert did not match the stored pin. Fatal by design."""


def make_context(broker_url: str) -> ssl.SSLContext:
    """SSL context for talking to the broker.

    Encryption is always on. Certificate *validation* is delegated to the pin:
    we still cannot use CA validation (no CA), so the context does not verify a
    chain - but check_and_learn_pin() below turns every connection into a
    fingerprint comparison against first-use. The context alone is therefore
    not the security boundary; it must be paired with check_and_learn_pin()
    after the handshake. broker_get/broker_post do exactly that.
    """
    ctx = ssl.create_default_context()
    ctx.check_hostname = False          # no CA-issued hostname to check against
    ctx.verify_mode = ssl.CERT_NONE     # chain validation impossible without a CA; the pin replaces it
    return ctx


def check_and_learn_pin(broker_url: str, der_cert: bytes) -> None:
    """Compare a just-presented cert against the pin; learn it on first use.

    Raises BrokerCertMismatch if a pin exists and does not match. Records the
    fingerprint if no pin exists yet (trust on first use).
    """
    if not der_cert:
        raise BrokerCertMismatch("broker presented no certificate")
    fp = _fingerprint(der_cert)
    pinned = load_pin(broker_url)
    if pinned == "":
        save_pin(broker_url, fp)
        return
    if fp.lower() != pinned:
        trace_event("broker_pin.mismatch", broker=_pin_key(broker_url),
                    got=fp[:16], want=pinned[:16])
        raise BrokerCertMismatch(
            "broker certificate does not match the pinned fingerprint for "
            f"{_pin_key(broker_url)}: got {fp}, expected {pinned}. Refusing to "
            "connect. If the broker's certificate was legitimately rotated, "
            f"remove {_pin_path(broker_url)} to re-pin on the next connection."
        )
