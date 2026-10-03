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
import json, subprocess
from typing import Any

class CommandAdapter:
    """Persistent implementation-neutral adapter: JSON-lines request/reply."""
    def __init__(self, command: list[str], capabilities: set[str]):
        self.command, self._capabilities = command, capabilities
        self.p = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True, bufsize=1)
    def capabilities(self): return self._capabilities
    def call(self, operation: str, **kwargs: Any):
        if self.p.poll() is not None:
            raise RuntimeError(f"adapter exited rc={self.p.returncode}: {self.p.stderr.read().strip()}")
        self.p.stdin.write(json.dumps({"operation":operation,"args":kwargs})+"\n"); self.p.stdin.flush()
        line=self.p.stdout.readline()
        if not line: raise RuntimeError(f"adapter EOF: {self.p.stderr.read().strip()}")
        out=json.loads(line)
        if not out.get("ok",False): raise RuntimeError(out.get("error","adapter failure"))
        return out.get("result")
    def close(self):
        if self.p.poll() is None: self.p.terminate()
    def __del__(self):
        try:self.close()
        except Exception:pass
