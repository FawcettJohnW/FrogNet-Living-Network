# The Communicator in Python

The second implementation of the same Communicator: the same lobby, call, window and rules as the C++ one, and the
same wire, so Python and C++ participants call each other through one media server. It reaches the memory through
`libcomms_tuples` (the platform's client library as a C ABI), which the C++ build produces.

## Setup

```
./build.sh                                   # Linux: builds build/libcomms_tuples.so (and the C++ programs)
pip install -r python/requirements.txt       # PyAV, PySide6, sounddevice, numpy
```

On Windows, `build-windows.ps1` puts `comms_tuples.dll` in `dist\`; then `pip install -r python\requirements.txt`.
The programs find the library in `build/`, `dist/`, or the folder named by `FROGCOMMS_LIB`.

## Run

The memory and the media server are the same C++ ones (`comms-ram`, `comms-media`). Then:

```
python3 python/comms_app.py  --ram HOST:8800 --media HOST:8994 --name Donna [--camera DEV | --video FILE] [--mic N --speaker N]
python3 python/comms_feed.py --ram HOST:8800 --media HOST:8994 --name "Py cam" --video media/stock-720p.mp4 --tone 440
python3 python/comms_app.py  --list-devices
```

Every option of the C++ window and feed is the same (`--bandwidth`, `--jitter` on the feed, `--no-audio`, ...). On
Windows: `python python\comms_app.py ...`, cameras as `--camera "video=<name>"`.

## Proven (tests/py)

- `test_room_interop.py BUILD`: Python and C++ in one comms-ram see each other, read each other's chat; a C++
  invitation reaches Python intact.
- `test_fnav.py FNAV_DIR`: the Python wire against fnav.py with the same vectors that proved the C++ one (frames,
  rate meter, reassembler, bearer, data plane over TCP).
- `test_call_interop.py BUILD`: C++ feed -> Python viewer (1280x720, all audio, the tone at 440 Hz); Python feed ->
  C++ viewer (1280x720).
- The two windows in one call, each receiving the other at 1280x720, 23 fps (screenshots).

## Under the protocol layer's frame; the Security tab (2026-11-25)

`--channel mediahost` puts a program's planes under the frame (`frogcomms/channel.py`, over `libchannel_frame`, which
the C++ build produces beside `libcomms_tuples`). The window has two tabs, Call and Security: the cipher, New key, the
protocol and Change RAM host, each one write by `frogcomms.channel.change`. Every program follows the memory when its
owner moves it (`frogcomms/ram_at.py`, `Room.attach`). `docs/CHANNEL-DESIGN-AND-IMPLEMENTATION.md` has all of it.

