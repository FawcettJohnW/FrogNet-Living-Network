history: the older Windows client folder for the 8840 demo set-ups (one RAM server; opt/fdf/README.md, "The earlier set-ups").
A Windows PC joins a FrogNet region with the bridge, bridge/README.txt (and docs/OPERATING.md in the FrogNet repository).

FD&F on Windows: the FD&F client, and the kart demo

Needs: nothing for fdf-client.exe (one static program, no DLLs).
       Python 3 and pygame for the kart demo:  pip install pygame
       kartram.dll beside kartdemo.py and ribbit.py: the game's one session to each region (static, no other DLLs).
       The main memory (the droplet's rina-host, FD&F or the kart region) must be fdf-2026-10-18-r22 or later.

1. Join the FD&F region (leave this window open; while it runs, this machine is a member):

     fdf-client.exe <droplet-ip>:8840

   It prints:  fdf-client: <COMPUTERNAME> joined the region at <droplet-ip>:8840, announced [...]

2. The kart lobby, in a second window (FD&F's memory is the lobby; a race starts when enough players are there, or
   when someone presses Start, and runs on a player's Linux machine -- Windows plays but does not host):

     python kartdemo.py --name <your-name> --fdf <droplet-ip>:8840

   Start (or S / Enter) in the lobby; arrow keys or WASD to drive. --bots 4 puts four robot karts of yours in each
   race; --autodrive (or P) drives your own kart; --min-players N (default 2); --headless runs with no window.
   Results and records stay in FD&F's memory across races and restarts.

If the droplet restarts, start fdf-client.exe again.

3. Calls (the real Communicator). Each other player's tile has a Call button; it opens comms-app.exe straight into a
   call to them, and their game opens theirs, where the call rings. Tell the game where comms-app.exe is:

     python kartdemo.py --name <your-name> --fdf <droplet-ip>:8840 --comms-app <path>\comms-app.exe

   The droplet runs the Communicator's memory and media server (fdf-services.sh), ports 8800 and 8994.

4. The browser terminal: log in to any machine in the network that runs fdf-term-serve, from this machine's browser.
   Needs Python 3 only (nothing to pip install). In a window of its own:

     python fdf-browser.py <droplet-ip>:8840

   It opens http://127.0.0.1:8091/ in the default browser: the network's machines on the left; click a green one that
   says nothing after its IP (one that does not run fdf-term-serve says "no terminal"), log in, and work. Ctrl-C in
   this window closes its sessions. (fdf-browser.py, fdfram.py, term\ and fdf-watch.exe must stay together.)

5. The monitor: every member of the network and everything the shared space holds about them, printed once
   (Windows Python has no curses, so it prints instead of running full screen):

     python fdf-monitor.py <droplet-ip>:8840
