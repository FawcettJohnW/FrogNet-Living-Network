################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
################################################################
"""fdf_appram -- [APP_SERVICES_V1] where an app's AppRam is served now, for the programs on a node (rt-post309, rt-sim):
its AppHost, the line the election writes into /etc/hosts (FDF_HOSTS) -- <app>Host.frognet for the general AppRam,
<team>Host.<app> for a Team's -- and its port, in the app's one configuration tuple (this machine's copy in FDF_APPS,
or the tuple in databasehost-control when the app is not installed here). HOSTS is the file whose writing is the event."""
import json
import os

import fdfram

HOSTS = os.environ.get("FDF_HOSTS", "/etc/hosts")
APPS = os.environ.get("FDF_APPS", "/opt/fdf/apps")


def host_name(app, ram):
    return "%sHost.frognet" % app if ram == "general" else "%sHost.%s" % (ram, app)


def host_of(name):
    try:
        for line in open(HOSTS):
            p = line.split("#", 1)[0].split()
            if len(p) >= 2 and name in p[1:]:
                return p[0]
    except FileNotFoundError:
        pass
    return ""


def config_of(app):
    """the app's configuration: this machine's copy, or the one tuple in databasehost-control; {} when neither"""
    try:
        with open(os.path.join(APPS, app + ".json")) as f:
            return json.load(f)
    except (OSError, ValueError):
        pass
    try:
        for c in fdfram.control_ram().read("fdf-app", "config", app):   # [ROLES_ON_API_V1] api.php
            return json.loads(str((c.get("bag") or {}).get("config") or "{}"))
    except (fdfram.RamError, ValueError):
        pass
    return {}


def rams_of(app):
    return list(config_of(app).get("rams") or [])


def port_of(app, ram):
    return int((config_of(app).get("ports") or {}).get(ram) or 0)


def where(app, ram):
    """host:port of APP's AppRam RAM now, or "" """
    h, p = host_of(host_name(app, ram)), port_of(app, ram)
    return "%s:%d" % (h, p) if h and p else ""
