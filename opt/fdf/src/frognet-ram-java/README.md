# frognet-ram-java -- [RAM_LIBRARY_JAVA_V1] the RAM session for Java apps (desktop and Android)

Abstracted from the kart app (`rinab/kart/android/src/net/frognet/kart/core`) into a library, so apps use it rather
than copy it (John, 2026-11-01: "If that other thing is useful, abstract it for use in this environment as a library
item, but don't just grab it"). Java 8 language level, no dependencies: it runs on Android and on any JVM.

- `RamSession`: one FNW1 session to a RAM host (rina-host): request and return sockets, REQ_RAW frames, replies matched
  by sequence; held reads simply answer later. A write with `"own":true` makes the tuple this session's: when the
  session ends the host writes it `{}` ([OWNED_CELL_GOES_WITH_THE_SESSION_V1]).
- `Follower`: one service of one RAM, read once then held; every change comes back once (`{}` = gone, its writer's
  session ended); writes posted on the session, `own` to go with it.
- `Json`: a small JSON reader/writer.

The kart app still has its own copy; it moves onto this library when it is next built (not yet).
Build: `./build.sh` -> `build/frognet-ram.jar`. Test: `test/OwnedCells.java` against a rina-host.
