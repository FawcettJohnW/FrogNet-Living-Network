#!/opt/frognet_semantic/venv/bin/python
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
# core/blob_store.py

import os
import hashlib
import threading
from typing import Optional

BLOB_ROOT = os.environ.get("FROGNET_BLOB_ROOT", "/opt/frognet_semantic/blob_cache")
os.makedirs(BLOB_ROOT, exist_ok=True)

_lock = threading.RLock()


class BlobStore:
    """
    Content-addressed immutable blob store.
    Stored by sha256 of bytes. blob_id = "sha256-<hex>".
    """

    @staticmethod
    def _path(blob_id: str) -> str:
        return os.path.join(BLOB_ROOT, blob_id)

    @staticmethod
    def id_for(data: bytes) -> str:
        h = hashlib.sha256(data).hexdigest()
        return f"sha256-{h}"

    @staticmethod
    def store(data: bytes) -> str:
        blob_id = BlobStore.id_for(data)
        path = BlobStore._path(blob_id)

        if os.path.exists(path):
            return blob_id

        with _lock:
            if not os.path.exists(path):
                tmp = path + ".tmp"
                with open(tmp, "wb") as f:
                    f.write(data)
                os.replace(tmp, path)

        return blob_id

    @staticmethod
    def load(blob_id: str) -> bytes:
        path = BlobStore._path(blob_id)
        with open(path, "rb") as f:
            return f.read()

    @staticmethod
    def exists(blob_id: str) -> bool:
        return os.path.exists(BlobStore._path(blob_id))

    @staticmethod
    def try_load(blob_id: str) -> Optional[bytes]:
        path = BlobStore._path(blob_id)
        if not os.path.exists(path):
            return None
        with open(path, "rb") as f:
            return f.read()
