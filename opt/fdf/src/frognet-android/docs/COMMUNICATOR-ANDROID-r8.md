# Communicator for Android r8 (comms-android-2026-11-27-r8): the secret by QR code; Reset this phone

John, 2026-11-27: "Import secret kinda sucks. Let's have the Windows app display a QR code for the Android to import
from. Either the QR code or a file." ... "I also need a way to reset both the Android and Windows machines so I can do
a demo" ... "The QR code should be a FrogNet QR code".

## What the phone does now (Security tab)

- **Scan secret…** opens the back camera (`SecretScanActivity`). Point it at the QR code a machine that holds the
  Role's secret shows (the desktop Communicator, Security tab, "Show the secret as a QR code..."). The phone keeps the
  secret as that machine keeps it (the network's default, or a Role's own at its step) in the app's private files
  and says which secret and 8 characters of its hash, which is what the other machine shows under its code.
- **Import file…** is what "Import secret…" was: the chosen file becomes this phone's default secret.
- **Reset this phone** (it asks first) writes over and removes every secret the phone holds and clears the
  Communicator's settings: it speaks the frame, the picture fills the screen, front camera. The name it joined under
  is kept. The phone stays joined to FrogNet and the Role's setting in the memory is not changed.

## The code

It is a FrogNet line, as an invitation (`fdfinv1.`) and a join line (`fdfjoin1.`) are:
`fdfsec1.<base64url, no padding, of {"v":1,"name":NAME,"step":STEP,"secret":BASE64URL}>`. The line is read by
`QrRead` (ZXing core 3.5.3, Maven Central, pure Java: `app/build.gradle.kts`), parsed and kept by `core/SecretQr` and
`ChannelFrame.placeSecret`, and dropped. It is not logged, shown, put in a tuple, or handed to another screen: the
scanner returns only what a screen may say.

## Proof

`communicator-test/run_tests.sh <communicator dir>`, six steps, all pass (2026-11-27). Step 6 (new): the desktop
draws its code for a default secret and for a Role's own secret at step 7, and the desktop window's own dialog is
pictured; the phone's reader reads each out of a camera frame (off centre, with noise, and a quarter turn round);
the phone keeps it and says the hash the desktop says; then the reset leaves it holding none. Step 5 presses
Reset this phone on the app's own Security tab. `tools/app-typecheck.sh` passes.

## Not done

The scanner has not run on a phone: Robolectric has no camera, so the camera, its preview and how fast a code is
found are untested. No APK was built here.
