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
# FrogNet developer-license receiver.
# Runs on the droplet that also hosts the website and the broker — a deliberate
# single-box deployment. This is website form-handling: store the request and
# email John, over local sendmail at /usr/sbin/sendmail. Co-located with the
# broker by choice, not coupled to it — no shared state or data path with node
# introduction or tunnel minting; a larger deployment could split the hosts.
# Mount into the existing app: `from license_endpoint import bp; app.register_blueprint(bp)`
# or run standalone: `python3 license_endpoint.py` (dev only).

import json, os, re, uuid, subprocess, datetime
from flask import Blueprint, request, jsonify

# ---- the two lines that are yours ----
NOTIFY_TO   = "john@fawcettinnovations.com"
SENDMAIL    = "/usr/sbin/sendmail"          # or swap send_mail() for your SMTP/API
# --------------------------------------

STORE_DIR = "/tmp/frognet_license_requests"          # survives review; /tmp clears on reboot
STORE_LOG = os.path.join(STORE_DIR, "requests.jsonl") # append-only log, greppable

bp = Blueprint("license", __name__)

REQUIRED = ("name", "email", "org", "country")
ATTEST   = ("attest_noncommercial", "attest_party", "attest_enduse")

MAXLEN   = {"name": 120, "email": 254, "org": 160, "country": 80, "use": 4000}
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
TRUE     = (True, "true", "on", "1", 1)
CTRL_RE  = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")   # control chars except tab/newline

def validate(data):
    """Return (clean_dict, errors[]). Server-side; never trusts the client."""
    errs, clean = [], {}
    for fld in REQUIRED:
        v = (data.get(fld) or "").strip()
        if not v:
            errs.append(f"{fld} is required"); continue
        if len(v) > MAXLEN[fld]:
            errs.append(f"{fld} too long (max {MAXLEN[fld]})"); continue
        if CTRL_RE.search(v):
            errs.append(f"{fld} has invalid characters"); continue
        clean[fld] = v
    if "email" in clean and not EMAIL_RE.match(clean["email"]):
        errs.append("email is not a valid address")
    use = (data.get("use") or "").strip()
    if len(use) > MAXLEN["use"]:
        errs.append(f"use too long (max {MAXLEN['use']})")
    clean["use"] = CTRL_RE.sub("", use)
    for a in ATTEST:
        if data.get(a) not in TRUE:
            errs.append("all three attestations must be confirmed"); break
    return clean, errs

def send_mail(subject, body):
    msg = f"To: {NOTIFY_TO}\nSubject: {subject}\nContent-Type: text/plain; charset=utf-8\n\n{body}\n"
    subprocess.run([SENDMAIL, "-t", "-oi"], input=msg.encode("utf-8"), check=True)

@bp.route("/api/license-request", methods=["POST"])
def license_request():
    if request.content_length and request.content_length > 64_000:
        return jsonify(ok=False, error="payload too large"), 413
    data = request.get_json(silent=True) or request.form.to_dict()
    if not isinstance(data, dict):
        return jsonify(ok=False, error="malformed request"), 400
    clean, errs = validate(data)
    if errs:
        return jsonify(ok=False, error="; ".join(errs)), 400

    rec = {
        "id": uuid.uuid4().hex[:12],
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "ip": request.headers.get("X-Forwarded-For", request.remote_addr),
        "name": clean["name"], "email": clean["email"],
        "org": clean["org"], "country": clean["country"],
        "use": clean["use"],
        "attest_noncommercial": True, "attest_party": True, "attest_enduse": True,
    }

    os.makedirs(STORE_DIR, exist_ok=True)
    # per-request file for easy review, plus one-line-per-record log
    with open(os.path.join(STORE_DIR, f"{rec['ts'].replace(':','').replace('-','')}_{rec['id']}.json"), "w") as f:
        json.dump(rec, f, indent=2)
    with open(STORE_LOG, "a") as f:
        f.write(json.dumps(rec) + "\n")

    body = ("New FrogNet developer-license request\n"
            "-------------------------------------\n"
            f"id       {rec['id']}\nwhen     {rec['ts']}\nfrom ip  {rec['ip']}\n\n"
            f"name     {rec['name']}\nemail    {rec['email']}\norg      {rec['org']}\n"
            f"country  {rec['country']}\n\nwants:\n{rec['use'] or '(none given)'}\n\n"
            "attestations: non-commercial [x]  not-a-barred-party [x]  end-use/re-export [x]\n\n"
            ">> These are the applicant's claims. Screen the party against the lists before granting access.\n"
            f">> Stored: {STORE_DIR}\n")
    try:
        send_mail(f"[FrogNet] license request — {rec['name']} ({rec['country']})", body)
    except Exception as e:
        # request is already persisted; mail failure must not lose it
        with open(os.path.join(STORE_DIR, "mail_errors.log"), "a") as f:
            f.write(f"{rec['ts']} {rec['id']} {e!r}\n")

    return jsonify(ok=True, id=rec["id"]), 200

CONTACT_REQUIRED = ("name", "email", "build")
CONTACT_MAXLEN   = {"name": 120, "email": 254, "org": 160, "use": 80, "build": 4000, "where": 4000}

def validate_contact(data):
    """Same posture as validate(): server-side, never trusts the client."""
    errs, clean = [], {}
    for fld in ("name", "email", "org", "use", "build", "where"):
        v = (data.get(fld) or "").strip()
        if fld in CONTACT_REQUIRED and not v:
            errs.append(f"{fld} is required"); continue
        if len(v) > CONTACT_MAXLEN[fld]:
            errs.append(f"{fld} too long (max {CONTACT_MAXLEN[fld]})"); continue
        if fld in ("name", "email", "org", "use") and CTRL_RE.search(v):
            errs.append(f"{fld} has invalid characters"); continue
        clean[fld] = CTRL_RE.sub("", v)
    if clean.get("email") and not EMAIL_RE.match(clean["email"]):
        errs.append("email is not a valid address")
    return clean, errs

@bp.route("/api/contact-request", methods=["POST"])
def contact_request():
    if request.content_length and request.content_length > 64_000:
        return jsonify(ok=False, error="payload too large"), 413
    data = request.get_json(silent=True) or request.form.to_dict()
    if not isinstance(data, dict):
        return jsonify(ok=False, error="malformed request"), 400
    clean, errs = validate_contact(data)
    if errs:
        return jsonify(ok=False, error="; ".join(errs)), 400

    rec = {
        "id": uuid.uuid4().hex[:12],
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "ip": request.headers.get("X-Forwarded-For", request.remote_addr),
        "kind": "contact",
        "name": clean["name"], "email": clean["email"],
        "org": clean.get("org", ""), "use": clean.get("use", ""),
        "build": clean["build"], "where": clean.get("where", ""),
    }

    os.makedirs(STORE_DIR, exist_ok=True)
    with open(os.path.join(STORE_DIR, f"{rec['ts'].replace(':','').replace('-','')}_{rec['id']}_contact.json"), "w") as f:
        json.dump(rec, f, indent=2)
    with open(STORE_LOG, "a") as f:
        f.write(json.dumps(rec) + "\n")

    body = ("New FrogNet developer-access request (contact page)\n"
            "----------------------------------------------------\n"
            f"id       {rec['id']}\nwhen     {rec['ts']}\nfrom ip  {rec['ip']}\n\n"
            f"name     {rec['name']}\nemail    {rec['email']}\norg      {rec['org'] or '(none)'}\n"
            f"intended {rec['use'] or '(none)'}\n\n"
            f"wants to build:\n{rec['build']}\n\n"
            f"where it would run:\n{rec['where'] or '(none given)'}\n\n"
            f">> Stored: {STORE_DIR}\n")
    try:
        send_mail(f"[FrogNet] contact request — {rec['name']}", body)
    except Exception as e:
        with open(os.path.join(STORE_DIR, "mail_errors.log"), "a") as f:
            f.write(f"{rec['ts']} {rec['id']} {e!r}\n")

    return jsonify(ok=True, id=rec["id"]), 200

if __name__ == "__main__":
    from flask import Flask
    app = Flask(__name__); app.register_blueprint(bp)
    app.run(host="127.0.0.1", port=8137)
