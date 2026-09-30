#!/bin/sh
set -eu
root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
[ -f "$root/DOCTRINE.txt" ]
[ -f "$root/CONTRACT.md" ]
if find "$root" \( -name '*.pyc' -o -name __pycache__ \) -print | grep -q .; then
  echo 'bytecode found' >&2; exit 1
fi
# A site password must never be serialized into a cell. The participant sources are named directly: the --exclude
# '*.cpp' / '*.hpp' this check used to pass applies to named files too, so it never read one.
if grep -H '"password".*q(pass)' "$root/ribbit_cpp/ribbit_lisp.cpp" "$root/ribbit_cpp/lisp_engine.hpp" \
     "$root/ribbit_cpp/lisp_handler.hpp" "$root/ribbit_cpp/unrest_handler.hpp" >/dev/null; then
  echo 'shared password serialization pattern found' >&2; exit 1
fi
echo 'package_check=PASS'
