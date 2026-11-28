import json, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
WORLD = {"ok": True, "participants": [
  {"name": "Seattle7", "ns": "w:g7", "alive": True, "facts": {"role": "node", "guid": "g7", "subnet": "10.170.170.0/24", "capability": {"cores": 4}}},
  {"name": "BAMacBook", "ns": "w:gb", "alive": True, "facts": {"role": "node", "guid": "gb", "subnet": "10.188.188.0/24", "capability": {"cores": 8}}},
  {"name": "Seattle3B", "ns": "w:g3", "alive": True, "facts": {"role": "node", "guid": "g3", "subnet": "10.130.130.0/24"}},
  {"name": "OldBox", "ns": "w:go", "alive": False, "facts": {"role": "node", "guid": "go", "subnet": "10.140.140.0/24", "capability": {"cores": 2}}},
  {"name": "John-Phone", "ns": "w:gp", "alive": True, "facts": {"role": "leaf", "addr": "100.127.255.1/32"}}]}
ROWS = {"rows": [
  {"SensorName": "SD:setting.units", "SensorType": "channel", "data": {"driver": "chorus", "number": 4437418795604890526, "protection": "chacha20-poly1305", "check": "ab"}},
  {"SensorName": "SD:setting.mediahost", "SensorType": "channel", "data": {"driver": "tcp", "number": 0}},
  {"SensorName": "SD:setting.semantic", "SensorType": "channel", "data": {"number": 0}},
  {"SensorName": "SD:at.10.170.170.1:proxy_main.py.811", "SensorType": "channel", "data": {"is": None, "rams": {"units": {"driver": "chorus", "protection": "chacha20-poly1305", "number": 4437418795604890526, "sealed": True, "step": 3}}, "build": "x"}},
  {"SensorName": "SD:at.10.188.188.1:proxy_main.py.77", "SensorType": "channel", "data": {"is": None, "rams": {"units": {"driver": "tcp", "protection": "", "number": 0, "sealed": False, "ignored": {"number": 1, "driver": "chorus", "step": 3}}}}}]}
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, o):
        b = json.dumps(o).encode(); self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0)); self._send(WORLD if "op=rina" in self.path else {})
    def do_GET(self):
        self._send(ROWS if "SensorType=channel" in self.path else {"cells": []})
HTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
