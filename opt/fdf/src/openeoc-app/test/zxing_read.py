# Copyright (C) 2016-2026 Fawcett Innovations LLC. SPDX-License-Identifier: GPL-2.0-only
# zxing_read.py -- [FROG_QR_V1] read QR codes from image files with zxing core 3.4.1 (the phone scanner's library, its
# default hints) through QrDecode.java: read(paths) -> {path: text or "FAIL <why>"}. Fetches the jar from Maven Central once.
import os, subprocess, time, urllib.request

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD = os.path.join(APP, "build", "frogqr-test")
JAR = os.path.join(BUILD, "zxing-core-3.4.1.jar")
ENV = dict(os.environ, JAVA_TOOL_OPTIONS="")

def ready():
    os.makedirs(BUILD, exist_ok=True)
    for t in range(6):
        if os.path.exists(JAR) and os.path.getsize(JAR) > 0: break
        try: urllib.request.urlretrieve("https://repo1.maven.org/maven2/com/google/zxing/core/3.4.1/core-3.4.1.jar", JAR)
        except Exception as e:
            print("fetch zxing core:", e)
            if os.path.exists(JAR): os.remove(JAR)
            time.sleep(10 * (t + 1))
    subprocess.run(["javac", "-cp", JAR, "-d", BUILD, os.path.join(APP, "test", "QrDecode.java")], check=True, env=ENV)

def read(paths):
    out = subprocess.run(["java", "-cp", JAR + os.pathsep + BUILD, "QrDecode"] + list(paths), capture_output=True, text=True, env=ENV).stdout
    return dict(l.split("\t", 1) for l in out.splitlines() if "\t" in l)
