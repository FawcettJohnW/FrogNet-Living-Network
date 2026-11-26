# The Communicator on Windows (Visual Studio)

The window (`comms-app`) and the unattended feed (`comms-feed`) build natively with Visual Studio. The memory
(`comms-ram`) and the media server (`comms-media`) run on the Linux side, e.g. FrogNetHost.

## Build

Needs Visual Studio 2022 with the **Desktop development with C++** workload (it includes MSVC, CMake, Ninja and
vcpkg). In PowerShell:

```
cd $HOME\Downloads
tar xzf frognet-communicator-cpp.tgz
cd comms
powershell -ExecutionPolicy Bypass -File .\build-windows.ps1
```

The script finds Visual Studio, has vcpkg build the libraries listed in `vcpkg.json` (Qt, FFmpeg, PortAudio, Opus,
LZ4, OpenSSL), builds the two programs, and assembles `dist\` with every DLL and Qt plugin. **The first run compiles
Qt and FFmpeg from source: one to two hours, once**; later runs take a minute. `dist\` runs on any Windows machine.

**Updating.** Always the whole tree, never a few files laid over an old one (2026-10-10: an r3 overlay of 8 files on an
older `F:\Temp\comms` failed to configure -- its CMakeLists.txt named `src/pyabi/comms_tuples.cpp` and
`tools/room_probe.cpp`, which that tree did not have). The archive's paths start with `comms\`, so extract it from the
folder ABOVE `comms` (from inside, it makes `comms\comms\` and the old tree is unchanged). `vcpkg-*` and `build-msvc`
are not in the archive: they stay, and the rebuild takes minutes.

```
cd F:\Temp
tar -xf $HOME\Downloads\communicator-r5-full-20261126.zip
cd comms
powershell -ExecutionPolicy Bypass -File .\build-windows.ps1
```

What runs says itself: the first console line is `[app] build comms-app-2026-10-10-r3`, and the window title carries it.

## Run

```
.\dist\comms-app.exe --list-devices
```

That prints each camera with the exact `--camera` value to use, and the numbered microphones and speakers. Then:

```
.\dist\comms-app.exe --ram 10.251.251.1:8800 --media 10.251.251.1:8994 --name John --camera "video=<name from the list>" --mic N
```

Leave out `--mic`/`--speaker` for the Windows defaults. Windows Firewall may ask about network access the first time.

## Under the frame: cipher, key, protocol and the RAM host switched from the call (r5, 2026-11-26)

Add `--channel mediahost` and the window's planes are under the protocol layer's frame: the row under the picture
(Cipher, New key, Protocol, Memory) and the Security tab change the Role's setting while the call runs, and the
overlay shows the call, the link and the channel. Without `--channel` the window is as it was and cannot join a
sealed call.

A sealed call needs the Role's secret on this machine: the same file the other machines have in
`/etc/frognet/channel_secrets` (named `default`, or `mediahost`). Copy it into a folder and name the folder in
`FROGNET_CHANNEL_SECRETS`:

```
mkdir $HOME\frognet-secrets
copy <the file named default from a Linux machine> $HOME\frognet-secrets\default
$env:FROGNET_CHANNEL_SECRETS = "$HOME\frognet-secrets"
.\dist\comms-app.exe --ram 10.251.251.1:8800 --media 10.251.251.1:8994 --name John --channel mediahost --camera "video=<name from the list>" --mic N
```

With the variable not set the window looks in `\etc\frognet\channel_secrets` on the drive it was started from.
The first console line is `[app] build comms-app-2026-11-26-r5`.

r5 on Windows, what is proven: every source file the Windows build compiles (the vendored memory client, the frame,
fnav, the codec, the comms library, `comms-feed`, and the Qt window `src/app/main.cpp`) compiles with MinGW-w64 13
against the Windows headers (2026-11-26, compile only: no link, no run). It has NOT been compiled with MSVC or run on
Windows; the last tree confirmed on Windows with MSVC is r3. `build-windows.ps1` and `vcpkg.json` (which already
names OpenSSL, the frame's one new library) are unchanged since r4.

## What is and is not proven

- The code compiles for Windows with MinGW-w64 against the Windows headers, apart from the Qt window, and builds on
  Linux with GCC 11-13; it has NOT been compiled with MSVC (none in the build environment). The first run of
  `build-windows.ps1` is the first MSVC compile: paste the first error if there is one.
- Known gap: Windows has no measure of how much a socket's send buffer holds (Linux: SIOCOUTQ), so fnav's room check
  before each frame cannot run there; the socket's "would block" decides, and the send buffer is 64 KiB
  ([THE_GUARD_MUST_WORK_WHERE_THE_CLIENT_RUNS_V1]).
- `build-windows.sh` (MSYS2) is the alternative to Visual Studio.
- 2026-10-10: `build-windows.ps1` runs on John's machine with MSVC (Visual Studio 2022 Community 14.39; vcpkg: Qt 6.11.2,
  FFmpeg 9.0.2, libvpx 1.16.0, opus 1.6.1, portaudio 19.7); after the full r3 tree he reported it good.
