history: the older Windows client folder for the 8840 demo set-ups (one RAM server; opt/fdf/README.md, "The earlier set-ups").
A Windows PC joins a FrogNet region with the bridge, bridge/README.txt (and docs/OPERATING.md in the FrogNet repository).

FD&F on Windows: the FD&F client, and the kart demo

Needs: nothing for fdf-client.exe (one static program, no DLLs).
       Python 3 and pygame for the kart demo:  pip install pygame

1. Join the FD&F region (leave this window open; while it runs, this machine is a member):

     fdf-client.exe <droplet-ip>:8840

   It prints:  fdf-client: <COMPUTERNAME> joined the region at <droplet-ip>:8840, announced [...]

2. Race, in a second window:

     python kartdemo.py --name <your-name> --race-host <droplet-ip>:8799

   Arrow keys or WASD. You see the other players' karts and their call tiles.

If the droplet restarts, start fdf-client.exe again.

3. Calls (the real Communicator). Each other player's tile has a Call button; it opens comms-app.exe straight into a
   call to them, and their game opens theirs, where the call rings. Tell the game where comms-app.exe is:

     python kartdemo.py --name <your-name> --race-host <race-ip>:8799 --comms-app <path>\comms-app.exe

   The race machine runs the Communicator's memory and media server (fdf-comms.sh), ports 8800 and 8994.

4. The browser terminal: log in to any machine in the network that runs fdf-term-serve, from this machine's browser.
   Needs Python 3 only (nothing to pip install). In a window of its own:

     python fdf-browser.py <droplet-ip>:8840

   It opens http://127.0.0.1:8091/ in the default browser: the network's machines on the left; click a green one that
   says nothing after its IP (one that does not run fdf-term-serve says "no terminal"), log in, and work. Ctrl-C in
   this window closes its sessions. (fdf-browser.py, fdfram.py, term\ and fdf-watch.exe must stay together.)

5. The monitor: every member of the network and everything the shared space holds about them, printed once
   (Windows Python has no curses, so it prints instead of running full screen):

     python fdf-monitor.py <droplet-ip>:8840
