# RangerTrak on FrogNet: deploying build fdf-2026-11-01-r37

What runs where, and the commands, for the build of 2026-11-01. The design is `PLAN.md` and
`opt/fdf/docs/APP-SETUP-AND-OPERATION.md`.

## The files

| file | for |
| --- | --- |
| `fdf-install-x86_64-fdf-2026-11-01-r37.run` | each x86_64 FrogNet node |
| `fdf-install-aarch64-fdf-2026-11-01-r37.run` | each Pi node |
| `fdf-droplet-x86_64-fdf-2026-11-01-r37.tgz` | an x86_64 region holder (a droplet) |
| `fdf-droplet-aarch64-fdf-2026-11-01-r37.tgz` | a Pi region holder (a LAN region) |
| `fdf-addin-dc-linux-any-dc-2026-11-01-r1.tgz` | the app: the DisasterComm add-in, which carries RangerTrak's pages (`/rt/`) and the post's 309 writer |

Made by `rinab/tools/make_deliverables.sh OUTDIR` and `bundles/make_addin.sh dc OUTDIR`.

## 0. The region holder first

A new region, on the holder (the DigitalOcean machine, or a LAN machine for a region with no Internet; on a Pi,
`fdf-droplet-aarch64-...`):

    cd / && tar xvzf - < /tmp/fdf-droplet-x86_64-fdf-2026-11-01-r37.tgz
    sudo /opt/fdf/bin/fdf-region-create AREA PUBLIC_IP

A region that already exists, on its holder: the same unpack, then its units again:

    cd / && tar xvzf - < /tmp/fdf-droplet-aarch64-fdf-2026-11-01-r37.tgz
    sudo systemctl restart 'fdf-ram-*' fdf-hub 'fdf-client-*'

Then, on the holder, one invitation per node that is not in the region yet (a node already in it keeps its line):

    sudo /opt/fdf/bin/fdf-invite NODENAME

## 1. The droplets: every Linux node that may serve the app's RAMs

    sudo bash /tmp/fdf-install-x86_64-fdf-2026-11-01-r37.run fdfinv1.<this node's invitation>

The join installs `fdf-client`, `fdf-facts` and `fdf-app` (`fdf-app.service`: `fdf-app serve`). `fdf-app serve`
writes the node's capability and configuration tuples for every app installed on it, runs each app RAM's selection,
starts `rina-host` for the RAMs this node is chosen for, and writes `/opt/fdf/var/app-where.json`. Nothing else is
started by hand.

## 2. The app on each node: install, then configure

    cd /tmp && tar xzf fdf-addin-dc-linux-any-dc-2026-11-01-r1.tgz && sudo ./fdf-addin-dc-dc-2026-11-01-r1/install

The pages go to `/opt/fdf/www/dc` and `/opt/fdf/www/rt`, and Apache serves them (`/rt/` opens `index.html`).
`fdf-addin-dc.service` holds the add-in's tuple and runs `rt-post309`, which writes the post's ICS-309 and ICS-213
files to `/opt/fdf/var/rt/`.

Then the admin's configuration, the same line on every node the app is installed on (the admin app is this command
for now; the automatic install of the named remote nodes is not built):

    sudo /opt/fdf/bin/fdf-app config rangertrak general,hospitals --role hospitals --role eoc:default --user kim:<password>=hospitals

- `general,hospitals`: the app's RAMs, the general RAM and one per team that has its own.
- `--role NAME[:default]`: a role; `:default` lets a client take it with no password (an EOC client).
- `--user USER:PASSWORD=ROLE`: an individual, whose role is activated at their login. Only a verifier is kept.
- `--login`: login required. Without it, a client with no role uses the general RAM.

`fdf-app serve` picks the change up at once: it writes the configuration into the network RAM, and serves the RAMs
where its selection says.

## 3. The users

Windows, Android and any laptop: a browser on a node's Wi-Fi, `http://<the node>/rt/`. (The Windows and Android
shells that show the same pages from the device itself are not built yet.)

1. The page connects first. The device is shown by its host name, from the lease the node gave it; a device that
   sent none (many Android phones) is named once on the Radio Log ("This device").
2. A user with a role logs in once (user and password on the Radio Log); an EOC client opens
   `http://<the node>/rt/index.html?role=eoc` and needs no password.
3. The page opens on the role's screen: the Radio Log, a team's messages, or the EOC's Summary.
4. Everything after that is on every post as it is written: entries, marks, positions, messages, the forms.

A post cut off keeps working on its own side; when it is back, every device writes its own again and every screen
has it. No user action.

## What is not in this build

- The Windows and Android shells for the pages (both use the browser for now).
- The admin app as a screen, and the automatic install of the remote nodes.
- The hospital status screen, inbound ambulances, assignments and overdue teams (in the mock-up only).
- Liveness bound to a device's session: a device that has left stays on the map, aging (see `PLAN.md`, the
  Meshtastic merge).
