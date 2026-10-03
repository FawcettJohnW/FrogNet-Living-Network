#!/usr/bin/env python3
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
"""
FrogNet site daemon
===================
Serves the static FrogNet site AND the form receiver in one process, so the
"Request a license" and "Contact" forms actually reach a listener.

Why this exists
---------------
The pages POST as JSON:
    license.dc.html  ->  /api/license-request
    contact.dc.html  ->  /api/contact-request
license_endpoint.py already answers both routes (as a Flask blueprint), but a
plain static file server (e.g. `python3 -m http.server`, per README) 404s those
POSTs, and running license_endpoint.py standalone serves the API but none of the
site. Nothing served BOTH. This daemon does: it registers the receiver blueprint
and serves every page/asset from the web root.

The two lines that are yours live in license_endpoint.py:
    NOTIFY_TO = "john@fawcettinnovations.com"
    SENDMAIL  = "/usr/sbin/sendmail"

Run (dev):
    pip install flask
    python3 frognet_site.py                 # http://127.0.0.1:8080
    FROGNET_SITE_HOST=0.0.0.0 FROGNET_SITE_PORT=80 python3 frognet_site.py

Run (production) — put it behind gunicorn + a real front (nginx/Caddy):
    pip install flask gunicorn
    gunicorn -w 4 -b 127.0.0.1:8080 frognet_site:app

Deploy note: keep frognet_site.py and license_endpoint.py together at the web
root. DOCTRINE.txt, CHANGES.txt, checksite.py, and the .py sources are build/
maintenance files — this daemon refuses to serve them even if they are present.
"""

import os
import sys
import mimetypes

# Make sure license_endpoint.py (next to this file) is importable regardless of CWD.
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from flask import Flask, send_from_directory, abort, redirect

from license_endpoint import bp as license_bp  # the form receiver (documented entry point)

# Web root that holds the .dc.html pages, assets/, media/, magnum-figures/, etc.
WEBROOT = os.environ.get("FROGNET_SITE_ROOT", HERE)
HOME_PAGE = "index.dc.html"

# Never serve build/maintenance files or source, even if they were copied in.
BLOCKED_NAMES = {"doctrine.txt", "changes.txt", "checksite.py"}
BLOCKED_EXTS = {".py", ".pyc"}

# .dc.html is a double extension; make sure it is served as HTML.
mimetypes.add_type("text/html", ".html")

app = Flask(__name__, static_folder=None)   # we do our own static serving
app.register_blueprint(license_bp)          # -> /api/license-request, /api/contact-request


def _is_blocked(rel_path: str) -> bool:
    name = os.path.basename(rel_path).lower()
    _, ext = os.path.splitext(name)
    return name in BLOCKED_NAMES or ext in BLOCKED_EXTS


@app.route("/")
def home():
    return send_from_directory(WEBROOT, HOME_PAGE)


@app.route("/<path:req_path>")
def serve(req_path):
    # API routes are owned by the blueprint; this catch-all only handles files.
    if req_path.startswith("api/"):
        abort(404)

    # Bare page name without extension -> the .dc.html page (nice URLs).
    candidate = req_path
    if not os.path.splitext(req_path)[1]:
        dc = req_path + ".dc.html"
        if os.path.isfile(os.path.join(WEBROOT, dc)):
            candidate = dc

    if _is_blocked(candidate):
        abort(404)

    full = os.path.join(WEBROOT, candidate)
    if os.path.isdir(full):
        abort(404)

    # send_from_directory uses safe_join: path traversal escapes -> 404, not a leak.
    try:
        return send_from_directory(WEBROOT, candidate)
    except Exception:
        abort(404)


if __name__ == "__main__":
    host = os.environ.get("FROGNET_SITE_HOST", "127.0.0.1")
    port = int(os.environ.get("FROGNET_SITE_PORT", "8080"))
    print(f"FrogNet site + form receiver on http://{host}:{port}  (web root: {WEBROOT})")
    app.run(host=host, port=port)
