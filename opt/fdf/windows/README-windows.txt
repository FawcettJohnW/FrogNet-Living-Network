history: the older Windows client folder for the 8840 demo set-ups (one RAM server; opt/fdf/README.md, "The earlier set-ups").
A Windows PC joins a FrogNet region with the bridge, bridge/README.txt (and docs/OPERATING.md in the FrogNet repository).

FD&F on Windows: the FD&F client, and the kart demo

Needs: nothing for fdf-client.exe (one static program, no DLLs).
       Python 3 and pygame for the kart demo:  pip install pygame
       kartram.dll beside kartdemo.py and ribbit.py: the game's one session to each region (static, no other DLLs).
       The droplet must be fdf-2026-10-19-r24 or later (its kart memory and kart service, fdf-kart-ram.sh).

1. Join the FD&F region (leave this window open; while it runs, this machine is a member):

     fdf-client.exe <droplet-ip>:8840

   It prints:  fdf-client: <COMPUTERNAME> joined the region at <droplet-ip>:8840, announced [...]

2. The kart lobby, in a second window. FD&F says where the kart memory is (the droplet's, port 8799); everyone
   connects there first. The kart service beside it agrees the laps, says when a game launches (all free players
   ready, or someone presses Start), and picks the machine that serves it: a dedicated game-serving machine if the
   network has one, else a player's Linux machine. Windows plays, chats and calls, but does not serve games.

     python kartdemo.py --name <your-name> --fdf <droplet-ip>:8840
     python kartdemo.py --name <your-name> --kart <droplet-ip>:8799     (straight to the kart memory, no FD&F)

   Lobby: type and press Enter to chat; Ready; Start (once ready); - / + the laps you want. Racing: arrow keys or WASD;
   P (or --autodrive) drives your kart. --bots 4 puts four robot karts of yours in each race; --headless, no window.
   Results and records stay in the kart memory across races and restarts.

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
