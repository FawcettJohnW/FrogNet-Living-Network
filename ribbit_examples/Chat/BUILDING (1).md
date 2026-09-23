# Building FrogChat

FrogChat has three parts: the memory server, the Python client and the C++ client. You only need the server and one client.

## Requirements

- **Memory server and C++ client:** Linux, g++ with C++11 support, and make.
- **Python client:** Python 3.8 or later, on Linux or Windows. Nothing to build or install.

No FrogNet components, databases or third-party libraries are required.

On Debian or Ubuntu:

```
sudo apt-get install build-essential python3
```

## The memory server

```
cd server_cpp
make
./ram_server --listen 0.0.0.0:8788
```

`ram_server` is FNW1 and the memory in one process. It holds everything in RAM, so a restart gives you an empty memory. If it runs on an Internet host, open its port (8788 by default) in the firewall; that's the only port it uses.

## The Python client

Nothing to build:

```
python3 apps/frogchat/client/frogchat.py --store HOST:8788 --me Dave --to Bob
```

On Windows:

```
py -3 apps\frogchat\client\frogchat.py --store HOST:8788 --me Dave --to Bob
```

## The C++ client

```
cd apps/frogchat/client_cpp
make
./frogchat --store HOST:8788 --me Dave --to Bob
```

The binary links libstdc++ statically, so it can be copied to another Linux machine of the same architecture.

To build a Windows executable from Linux with mingw-w64:

```
sudo apt-get install g++-mingw-w64-x86-64-posix
make windows
```

This produces `frogchat.exe`.

## Verify

With the server running:

```
python3 common/probe_ram.py HOST 8788
cp apps/frogchat/client/frogchat.py apps/frogchat/
python3 apps/frogchat/test_frogchat_oracle.py HOST:8788
```

The oracle should end with `PASS (0 failed)`.
