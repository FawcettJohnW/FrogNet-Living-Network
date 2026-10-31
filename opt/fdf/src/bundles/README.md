# FD&F bundles: one per machine type, add-ins on top

Every file carries its build in its name. Each installer looks at the machine first and refuses the wrong one,
saying which bundle is right.

| machine | bundle | install | starts |
|---|---|---|---|
| region holder (droplet) | `fdf-droplet-<tag>.tgz` | `cd / && tar xvzf - < /tmp/fdf-droplet-<tag>.tgz`, then `fdf-region-create AREA PUBLIC_IP` once | `fdf-hub`, the RAM manager, its member, `fdf-identity.service` |
| FrogNet node (x86_64, Pi) | `fdf-install-<arch>-<tag>.run` | `sudo ./fdf-install-<arch>-<tag>.run fdfinv1.<...>` | the tunnel daemon's hub channel, then `fdf-client` |
| any other Linux, and the first client (a Linux desktop, or WSL 2 on Windows) | `fdf-core-linux-<arch>-<build>.tgz` | `tar xzf` it anywhere, then `sudo ./fdf-core-<build>/install [--invite fdfinv1.<...>]` | `fdf-core.service`: `fdf-start` |
| Windows (x64) | `fdf-windows-<build>.zip` | unzip to `C:\fdf`, add `wintun.dll`, then `setup.cmd fdfinv1.<...>` (Administrator) | scheduled task "FrogNet member" |
| Android | `fdf-android-<build>.apk` (`frognet-android/tools/make_apk.sh`, on a machine with the SDK) | install, paste the invitation, Start | `MemberService`, and on boot |

## Identities

An identity is what `fdf-invite NAME [--leaf]` makes: an edge address, a WireGuard key, and the `fdfinv1` line that
carries them. From any machine connected to the region:

    fdf-identity NAME --leaf          # a phone, a laptop, a Windows PC
    fdf-identity NAME                 # a FrogNet node
    fdf-identity NAME --again         # a new key for an existing NAME (its old one stops working)

There are no credentials. A machine can only connect by invitation, so being connected is the trust.

## What `fdf-start` brings up (the Linux core)

- **leaf.** An invitation is kept (`var/invite`), so the machine joins:
  - the tunnel comes up from the invitation (`fdf-tun`, WireGuard in user space; no kernel module, no wg tools);
  - then the region inside it, with the same member code the phone and Windows run.
- **standalone.** No invitation, so the machine's own control RAM starts on 127.0.0.1:8840. Nothing listens off the
  machine.

Either way it writes `var/control.env`: `FDF_MODE`, `FDF_CONTROL_RAM`, `FDF_NAME`.

## Add-ins

Each add-in is its own bundle: `fdf-addin-<name>-linux-<arch|any>-<build>.tgz`. Install it with
`sudo ./fdf-addin-<name>-<build>/install`. It needs a core on the machine: the Linux core, or a node.

- Each add-in uses its own RAM.
- It writes its own capability tuple to the control RAM, `(pond, where, <machine ns>/<add-in>)`. The tuple names the
  machine by identity (`self:<port>`), and readers resolve that to an address in their own scope.
- When the machine's session ends, the region sweeps the add-in's tuple along with it. When the add-in stops, it
  clears its own tuple.
- On a network event (its session to the control RAM ends), the add-in does what its `ADDIN` says:

| `on-loss=` | meaning |
|---|---|
| `exit` | its servers stop, and it stays stopped until the core starts again |
| `rediscover` | its servers stop and its unit starts it again: the control RAM, the mode and its tuple are found afresh |
| `stay` | its servers keep running, and its tuple is written again when the control RAM is back |

The add-ins today:

| add-in | its RAM and servers | tuple | on-loss |
|---|---|---|---|
| `comms` | `comms-ram` 8800, `comms-media` 8994 | `{"comms":{"ram":"self:8800","media":"self:8994"}}` | rediscover |
| `kart` | the core's `rina-host --api /ram.php` on 8799 | `{"kart":{"race":"self:8799"}}` | stay |

Players start the kart game with `/opt/fdf/addins/kart/kart-play`. It finds the race in the control RAM and resolves
it by identity, so nothing is typed but the name.

## Building

    src/bundles/make_core_linux.sh OUTDIR [x86_64|aarch64]
    src/bundles/make_addin.sh comms|kart OUTDIR [arch]
    src/fdf-source/rinab/tools/make_deliverables.sh OUTDIR       # droplet + node .run files
    src/frognet-android/windows/make_package.sh OUTDIR           # FDF_TUN_PREBUILT=1: the checked-in fdf-tun.exe
    src/frognet-android/tools/make_apk.sh OUTDIR                 # needs the Android SDK
    src/frognet-android/tools/app-typecheck.sh                   # the app's sources, no SDK

The proof is `src/frognet-android/tools/netns-leaf-tunnel-test.sh`, run with `CORE_TGZ` and `ADDINS` set. It runs the
real hub, RAM manager, invitations and bundles in network namespaces.
