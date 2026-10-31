# An organization on FrogNet: setup, levels, login, and the RAM manager

2026-10-31. John Fawcett, with Claude.

An organization stands up its own FrogNet system with nobody from Fawcett Innovations in the room. An EOC is the first
case; a hospital group, a company or a game is the same. The organization describes itself in its own words, its
people log in, and its applications (RangerTrak, the EOC screens, the Communicator) read and write cells without
knowing about networks, servers, accounts or sync.

None of this is EOC code. Setup, levels and login are platform pieces, in the ++ libraries beside API++
(`opt/frognet_semantic/ribbit/docs/API-PLUS-PLUS.md`); every Ribbit application gets them.

## 1. Setup: the manager, once, on their own droplet

The manager rents a droplet, logs in to it, installs the one droplet bundle, and runs the wizard:

    cd / && tar xvzf - < /tmp/fdf-droplet-<build>.tgz
    /opt/fdf/bin/fdf-setup

The wizard asks before it changes anything, and fixes what is missing (wireguard-go, the firewall) once the manager
says yes. In order:

1. **The RAM manager.** The organization's name and the droplet's public address (found, shown, confirmed); then what
   `fdf-region-create` does today.
2. **Levels.** The manager describes their organization in their own words. For an EOC: First Responders,
   Headquarters, Hospitals, Ambulances. For others: Watcher, Player, Host; Viewer, Commenter, Editor, Admin. The
   wizard shows examples and takes whatever names the manager gives. For each level: a password or none, and its reach
   (section 2).
3. **Users and passwords**, for the levels that have them, stored encrypted in tuple space.
4. **The keys.** The generic WireGuard credential, and one join line per level (the RAM host's address, the credential,
   the level's login port), printed and shown as a QR, to hand out or put into an install.
5. **First login.** The manager's own device logs in with its level's line, and the wizard shows it arrive.

The wizard can be run again to add levels or users; what exists is shown, not remade. A standby droplet is set up by
running the wizard there with the same levels and users.

## 2. Levels and reach

A level is however the customer thinks of their own organization. It is theirs, it lasts as long as their need does,
and the platform attaches no meaning to its name.

What a level has:

| | |
| --- | --- |
| a name | the customer's |
| a login port and a session port | random, picked by the wizard, on the RAM host |
| a password, or none | none: a device is admitted at once, named by its device ID, with a nickname the person can set |
| its reach | what a device at that level can get to |

**Admission defines reach.** A level's reach is one of:

- **RAM only**, the default. Most levels: the system's shared memory and nothing else on the network.
- **A list**: the RAM regions and endpoints the manager names for that level. Hospitals might reach the
  hospital-capacity region; Headquarters every region plus a server at a post.
- **Open**: everything the network serves.

What the regions and endpoints mean is the customer's. The platform only applies the reach: `fdf-hub` builds each
device's peer (its allowed addresses and routes) from its level's reach, the way it applies the region-wide reach
(`net.json`, `(pond, config, net)`, `none` / `listed` / `all`) today.

## 3. Install and login: each device

At install the device is given its level's join line. If the level has a password, the person types a user and
password.

Login, once per RAM manager on the device's list:

1. Tunnel up on the generic credential. The login ports are reachable only inside it.
2. Connect to the level's login port and log in. The user and password are checked against tuple space; that gives
   the level.
3. The RAM host admits the device's own WireGuard peer (its own key, its own address) at that level's reach, and
   returns it with the level's session port.
4. Generic tunnel down, the device's own tunnel up, and a session to the RAM manager on the session port.

Every later start goes straight to step 4.

The generic credential is one WireGuard peer, so it carries one exchange and nothing after: two devices logging in at
the same instant collide (the RAM host's endpoint for that peer goes to whichever handshook last), and the library
logs in again by itself. A lost device is revoked by the manager; its person logs in again and is a new member with a
new key. No key is ever re-issued.

On a node's Wi-Fi none of this applies: a phone there is a DHCP client and talks to the node's RAM directly.

## 4. Which RAM manager: the system RAM

This is the **system** RAM manager only. Services can have their own RAM managers anywhere; nothing here touches them.

- **The WAN RAM host** is a machine outside the FrogNet network that every host can reach: the manager's droplet, and
  any standbys, in order, in the device's config.
- **databasehost-control** is unchanged: always the machine with the highest IP address in the network.
- **Every node decides for itself.** When the LAN connects to the Internet (an event, so a merge, section 5), each node
  walks its configured RAM managers top to bottom and connects to the first that answers. If none answers, the system
  RAM is databasehost-control.
- **Every node has its own connection** to the RAM manager: its own login, its own tunnel to the RAM manager's address,
  its own session. The connection carries only the RAM manager's address and changes no routes. Nodes A and B on one
  LAN each have their own uplink and their own connection, and A↔B stays on the LAN. Node C behind them leaves through
  whichever of A or B is its exit, and its connection is still its own; if its exit moves, its session reconnects
  through the other, to the same RAM manager.

## 5. Run

Each node writes only its own facts, through its own session, and holds one read on the RAM (`fdf-events`). A change
fires an event: participants changed, a mapping changed, or the region is gone. A merge is the node applying the
event (`_one("event ...")`, `internet_tunnels_v3/poll.py`): it reads the world from the RAM, derives its own routes and
channels, and writes its facts back, one write per event. Split and merge are the same operation.

## 6. Failover and failback

**Failover.** The session to the current RAM manager drops (the 5 s keepalive). The node switches at once to the first
standby: it logged in there already, so the switch is a handshake and a session. If no configured RAM manager answers,
its tunnels come down and the system RAM is databasehost-control.

**Failback.** A RAM manager coming back is an event, and the event runs the merge. The merge walks the list top to
bottom; if the answer is not the RAM manager the node started the merge on, it switches.

Either way, memory is not moved. Applications are told the RAM host changed, re-arm their held reads and write their
own truth again.

## 7. What exists and what is built

| piece | in the tree today | built for this |
| --- | --- | --- |
| a device joining with its own key and edge | `fdf-invite`, `admitted/NAME.json`, `fdf-hub` peers it; `Invite.kt`, the Windows bridge, the Android app join from an `fdfinv1` line | unchanged: login ends in exactly this |
| asking the RAM host for an identity through cells | `fdf-identity` (ask, answer and got cells, a held read each side, the secret in RAM only between the two) | login uses the same exchange, on the level's login port |
| reach | region-wide: `net.json`, applied by `fdf-hub` per peer | per level: the admission record carries the level, `fdf-hub` applies that level's reach |
| levels, users, passwords | none | in tuple space, encrypted with a key that never leaves the RAM host |
| the generic credential and the login ports | none | made by the wizard; the login ports reachable only inside the generic tunnel |
| session ports | `rina-host` listens on one port (`ram_server.cpp`) | one per level |
| the RAM manager list | one invitation, one hub (`/opt/fdf/var/hub.json`, `region.my_channels()`) | one login per configured RAM manager, walked top to bottom in the merge; databasehost-control when none answers |
| the wizard | `fdf-region-create` does section 1 step 1 | `fdf-setup`, all five steps |

## 8. How it is proven

In network namespaces on one machine, against a real region made by `fdf-region-create`, the way the r36 netns tests
are:

- two devices logging in at the same instant on the generic credential, both ending on their own tunnels;
- a level with a password and one without; a wrong password refused;
- reach: a RAM-only device reaches the RAM and nothing else; a listed device reaches its list; an open one, everything;
- failover: kill the primary, every node is on the standby; kill both, databasehost-control;
- failback: bring the primary back, the merge moves every node to it;
- a node behind another (C behind A and B): kill A's uplink, C's session follows to B, same RAM manager.

Then the same on hardware: the manager's droplet, a standby droplet, and the nodes.
