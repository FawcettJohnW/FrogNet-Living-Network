# Vendored Ribbit platform

The parts of the FrogNet Ribbit platform (`opt/frognet_semantic/ribbit`) this program compiles: the frogram wire
client, the RAM client and host, and the default tuple API -- copied from the repo, exactly the files the compiler's
dependency list names, so the Communicator builds with no FrogNet checkout. SOURCE.txt records the commit they came
from. To build against a live tree instead: `./build.sh /path/to/ribbit`.

Copyright (C) 2016-2026 Fawcett Innovations LLC. GPL-2.0-only, as the rest of FrogNet.

## Local changes (to be made upstream)

Two Windows portability fixes, each marked `[COMMS_WINDOWS_PATCH]` in the file; Linux behaviour is unchanged:
- `defaults/api/tuples.cpp` included `<arpa/inet.h>`, `<netdb.h>`, `<sys/socket.h>`, `<unistd.h>` unconditionally and
  closed a socket with `::close`. On Windows: Winsock headers and `closesocket`.
- `cpp/include/semtpl.hpp` called `mkdir(dir, 0755)`; Windows' `mkdir` takes no mode. On Windows: `_mkdir(dir)`.
