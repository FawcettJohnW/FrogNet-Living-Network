#!/bin/bash
# Copyright (C) 2016-2026 Fawcett Innovations LLC
# SPDX-License-Identifier: GPL-2.0-only
#
# install_wrapper.sh REDIS_TREE [RIBBIT_BINARY]   put Ribbit behind src/redis-server
# install_wrapper.sh --restore REDIS_TREE          put stock Redis back
#
# Redis's test suite always launches src/redis-server. This keeps the stock binary
# as src/redis-server.stock and installs a small script in its place that runs
# ribbit-redis with the same arguments (plus $RIBBIT_ARGS, e.g. "--io-threads 4").
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
if [ "$1" = "--restore" ]; then
  T="$2"; [ -f "$T/src/redis-server.stock" ] || { echo "no src/redis-server.stock in $T" >&2; exit 1; }
  mv -f "$T/src/redis-server.stock" "$T/src/redis-server"; echo "stock Redis restored in $T"; exit 0
fi
T="$1"; R="$(cd "$(dirname "${2:-$HERE/../ribbit-redis}")" && pwd)/$(basename "${2:-ribbit-redis}")"
[ -n "$T" ] && [ -d "$T/src" ] || { echo "usage: $0 REDIS_TREE [RIBBIT_BINARY]" >&2; exit 1; }
[ -x "$R" ] || { echo "ribbit-redis not found at $R (run make first)" >&2; exit 1; }
if [ ! -f "$T/src/redis-server.stock" ]; then
  head -c 2 "$T/src/redis-server" | grep -q '#!' && { echo "$T/src/redis-server is already a wrapper" >&2; exit 1; }
  mv "$T/src/redis-server" "$T/src/redis-server.stock"
fi
cat > "$T/src/redis-server" <<W
#!/bin/sh
# Redis-on-Ribbit wrapper: the test suite launches src/redis-server; this runs ribbit-redis instead.
exec "\${RIBBIT_REDIS:-$R}" "\$@" \${RIBBIT_ARGS}
W
chmod +x "$T/src/redis-server"
echo "src/redis-server in $T now runs $R  (stock kept as src/redis-server.stock)"
