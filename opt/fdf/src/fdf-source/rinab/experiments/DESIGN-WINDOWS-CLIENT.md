# DESIGN-WINDOWS-CLIENT: the FD&F client on Windows (John, 2026-10-07)

**John:** "those are all now just calls to get and set shared memory" ... "the fdf client does that socket thing, by
itself, for that region" ... "Yes. This has some interesting implications."

**What it is** (`[FDF_WINDOWS_CLIENT_V1]`): `fdf-client.exe`, a native Windows FD&F member. It does exactly what the
Linux agent does for membership, and nothing else (no rlite):

1. **connect:** one FNWP session to the region's RAM manager (`RINA_RAM`, given directly, as in direct mode);
2. **announce:** name (computer name), IPv4 addresses, capability (cores, memory: what Windows reports);
3. **alive:** the session stays open on a held read, so the RAM manager counts the machine as alive while it runs;
4. **say so:** if the session ends, it prints why and exits (direct mode has no mapping system to wait on).

Applications on that Windows machine then just get and set in the region.

**Built from:** this tree's own frogram session client (the one the RAM manager is tested with), made portable. Its
futex waits go through a shim (`[FUTEX_PORTABLE_V1]`): the Linux syscall unchanged, Windows
WaitOnAddress/WakeByAddressAll. It is cross-compiled here with mingw-w64 and run under Wine against a real RAM manager.

**Checks:**

- the Windows client joins;
- `fdf-participants` on Linux lists it alive, with its name, IPs and capability;
- when it exits, it is listed not alive;
- the Linux build is unchanged: rlite 30/30.
