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
import importlib.util
import pathlib
from typing import Any

class ControlAdapter:
    """Adapter to Dino Farinacci's lispers.net Python API. No implementation internals are asserted."""
    def __init__(self, source: str, host: str, user: str, password: str, port: int = 8080, https: bool = True):
        p = pathlib.Path(source) / "lisp" / "lispapi.py"
        spec = importlib.util.spec_from_file_location("control_lispapi", p)
        if spec is None or spec.loader is None: raise RuntimeError(f"cannot load {p}")
        m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
        self.api = m.api_init(host, user, password, port=(port if https else -port))
        self._ops = {
            "system.get": "get_system",
            "map_cache.list": "get_map_cache",
            "map_cache.get": "get_map_cache_entry",
            "site_cache.list": "get_site_cache",
            "site_cache.get": "get_site_cache_entry",
            "map_resolver.get": "get_itr_map_resolver",
            "map_resolver.add": "add_itr_map_resolver",
            "map_resolver.delete": "delete_itr_map_resolver",
            "map_server.get": "get_etr_map_server",
            "map_server.add": "add_etr_map_server",
            "map_server.delete": "delete_etr_map_server",
            "database_mapping.add": "add_etr_database_mapping",
            "database_mapping.delete": "delete_etr_database_mapping",
            "map_cache.add": "add_itr_map_cache",
            "map_cache.delete": "delete_itr_map_cache",
        }
    def capabilities(self): return set(self._ops)
    def call(self, operation: str, **kwargs: Any):
        if operation not in self._ops: raise NotImplementedError(operation)
        return getattr(self.api, self._ops[operation])(**kwargs)
