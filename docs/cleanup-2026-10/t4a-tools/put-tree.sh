#!/bin/bash
# put-tree.sh CONTAINER [TREE] -- T4a test harness: copy a checkout (tracked + untracked files, no .git; default the
# working tree /home/claude/clean as it is now, edits included) to /root/FrogNet in the container, the way a
# stranger's `git clone` would look.
set -eu
C=${1:?usage: put-tree.sh CONTAINER [TREE]}; T=${2:-/home/claude/clean}
docker exec "$C" rm -rf /root/FrogNet
docker exec "$C" mkdir -p /root/FrogNet
# (files deleted in the working tree but still in the index are left out, as a clone of the working tree would)
( cd "$T" && git ls-files -co --exclude-standard -z | python3 -c 'import os,sys; sys.stdout.write("".join(f+"\0" for f in sys.stdin.read().split("\0") if f and os.path.lexists(f)))' | tar --null -T - -cf - ) | docker exec -i "$C" tar -C /root/FrogNet -xf -
docker exec "$C" bash -c 'echo "tree in /root/FrogNet: $(find /root/FrogNet -type f | wc -l) files"'
