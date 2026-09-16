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
"""core/db_credentials.py - ONE place the local MySQL credential comes from.

[ONE_CREDENTIAL_ONE_SOURCE_V1] The FrogUser password lived in five files at
once: var/www/html/config.php, opt/frognet_semantic/DB_CONFIG.json, and a
hardcoded default inside each of daemon/cache/semcache_db.py,
proxy/cache/semcache_db.py and daemon/engine/data_cache.py. Nothing kept them in
agreement except the installer's phase C1b, which rewrites all five from the one
password the operator typed.

That works exactly once. Any path that does not run C1b -- a file restored from
a build tar, a partial reinstall, a module updated on its own, a unit started
without FROGNET_DB_PASS in its environment -- leaves a stale literal behind, and
the node then connects with a password MySQL has never heard of:

    [Warning] Access denied for user 'FrogUser'@'localhost' (using password: YES)

repeated for as long as the service retries, which is forever. Two of those five
shipped a real, working pond password as their default -- a live credential in
the source tree, under
a comment stating it had been removed for exactly that reason.

So: the credential is read from DB_CONFIG.json, at call time, by everybody.
There is no default and no fallback. A node whose config is missing or still
tokenized fails loudly with a message that says which file to look at, instead
of failing obscurely against the auth log.

Environment still wins where it is set (FROGNET_DB_*), because a unit file
overriding one process is a deliberate act. An UNSET environment is not a
licence to guess.
"""
from __future__ import annotations

import json
import os
import threading
from typing import Any, Dict

DB_CONFIG_PATH = os.environ.get("FROGNET_DB_CONFIG",
                               "/opt/frognet_semantic/DB_CONFIG.json")

#: What the build ships before the installer injects. Reaching MySQL with this
#: is not a password problem, it is an install that did not finish.
TOKEN = "__FROGNET_DB_PASS__"

_cache: Dict[str, Any] | None = None
_lock = threading.Lock()


class DBCredentialUnavailable(RuntimeError):
    """No usable credential. Deliberately fatal -- see the module docstring."""


def _load() -> Dict[str, Any]:
    global _cache
    if _cache is not None:
        return _cache
    with _lock:
        if _cache is None:
            try:
                with open(DB_CONFIG_PATH) as f:
                    _cache = json.load(f)
            except FileNotFoundError:
                raise DBCredentialUnavailable(
                    f"{DB_CONFIG_PATH} does not exist. This file is the single "
                    f"source of the FrogUser credential; nothing else carries "
                    f"it. Re-run the installer or restore it from the build."
                ) from None
            except (OSError, ValueError) as e:
                raise DBCredentialUnavailable(
                    f"{DB_CONFIG_PATH} is unreadable or not valid JSON: {e}"
                ) from e
        return _cache


def reload() -> None:
    """Drop the cached read. For a long-lived process after the password is
    rotated, so it does not need a restart to notice."""
    global _cache
    with _lock:
        _cache = None


def host() -> str:
    return os.environ.get("FROGNET_DB_HOST") or _load().get("host", "127.0.0.1")


def port() -> int:
    return int(os.environ.get("FROGNET_DB_PORT") or _load().get("port", 3306))


def user() -> str:
    return os.environ.get("FROGNET_DB_USER") or _load().get("user", "FrogUser")


def database() -> str:
    return (os.environ.get("FROGNET_DB_NAME")
            or _load().get("database", "FrogNet"))


def password() -> str:
    """The FrogUser password. Raises rather than returning a guess."""
    env = os.environ.get("FROGNET_DB_PASS")
    if env:
        if env == TOKEN:
            raise DBCredentialUnavailable(
                "FROGNET_DB_PASS is still the build placeholder "
                f"{TOKEN!r}. The installer's secret injection did not run for "
                "whatever set this environment."
            )
        return env
    pw = _load().get("password")
    if not pw:
        raise DBCredentialUnavailable(
            f"{DB_CONFIG_PATH} carries no password. MySQL will refuse every "
            f"connection; this is not a permissions problem to chase in the "
            f"auth log."
        )
    if pw == TOKEN:
        raise DBCredentialUnavailable(
            f"{DB_CONFIG_PATH} still contains the build placeholder {TOKEN!r}. "
            f"The installer's secret injection (phase C1b) did not run on this "
            f"node, so no reader here has a real credential."
        )
    return pw


def connect_kwargs() -> Dict[str, Any]:
    """host/port/user/password/database, ready to splat into a connector."""
    return {"host": host(), "port": port(), "user": user(),
            "password": password(), "database": database()}


# ---------------------------------------------------------------------------
# [CREDENTIAL_FAILURE_IS_FATAL_V1] A bad credential is not a runtime condition.
#
# The proxy and the daemon both wrapped every DB call in `_with_retry`, which
# retries ONCE on any exception, and the callers above that caught and printed.
# So a password MySQL has never heard of produced two Access-denied lines per
# operation, a printed warning, and then business as usual -- for as long as the
# service ran, which with Restart=always is forever. Hours of auth log, no
# working cache, and a process that looks up.
#
# There is nothing to retry. The password will not become correct. The only
# honest responses are to fix it or to stop, and a service that cannot reach its
# own database is not serving. So: stop.
#
# os._exit, not sys.exit: these calls happen on worker threads, where SystemExit
# unwinds one thread and leaves the process running -- exactly the limp this
# exists to prevent. The message is flushed first because it is the only thing
# that will say why.
#
# Paired with StartLimitBurst on the units. Without it Restart=always turns a
# crater into a one-second loop, which is the same failure with more logging.
# With it, systemd gives up after a few attempts and leaves the unit `failed`:
# stopped, visible to `systemctl status`, and not pretending.
FATAL_MYSQL_ERRNOS = frozenset((
    1045,   # ER_ACCESS_DENIED_ERROR  - wrong user or password
    1044,   # ER_DBACCESS_DENIED_ERROR - user exists, no rights to the database
    1049,   # ER_BAD_DB_ERROR          - the database does not exist
    1698,   # ER_ACCESS_DENIED_NO_PASSWORD_ERROR - auth plugin refuses
))


def is_fatal(exc: BaseException) -> bool:
    """True for a failure that CANNOT succeed on retry.

    Deliberately narrow. A refused connection, a timeout, a dropped socket, a
    restarting MySQL -- all transient, all still retried. This is only for a
    credential or schema that is wrong, where the next attempt is guaranteed to
    fail exactly the same way.
    """
    if isinstance(exc, DBCredentialUnavailable):
        return True
    return getattr(exc, "errno", None) in FATAL_MYSQL_ERRNOS


def fatal(exc: BaseException, who: str = "") -> None:
    """Say why, then stop the process. Never returns."""
    import sys
    import traceback
    tag = who or "frognet"
    msg = (
        f"\n[{tag}] FATAL: the database credential is wrong and retrying cannot "
        f"fix it.\n"
        f"[{tag}]   {type(exc).__name__}: {exc}\n"
        f"[{tag}]   credential source: {DB_CONFIG_PATH}\n"
        f"[{tag}]   MySQL will log 'Access denied for user' for every attempt; "
        f"that is a symptom, not the cause.\n"
        f"[{tag}] Stopping rather than running without a database.\n"
    )
    for stream in (sys.stderr, sys.stdout):
        try:
            stream.write(msg)
            stream.flush()
        except Exception:
            pass
    try:
        traceback.print_exc(file=sys.stderr)
        sys.stderr.flush()
    except Exception:
        pass
    os._exit(78)          # EX_CONFIG
