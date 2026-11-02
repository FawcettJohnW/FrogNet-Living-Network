#!/bin/bash
# update_zip.sh SINCE OUTDIR -- [RT_ANDROID_UPDATE_IN_PLACE_V1] the files of the Android project's tree (the android
# project, the core, the page, the RAM library) changed since commit SINCE, at their own paths, as
# RangerTrak-android-update-<build>.zip: unzipped over the tree already open in Android Studio (unzip -o), it is that
# tree at this build. No new directory, nothing to import again. A file removed since SINCE is listed in REMOVED.txt.
set -eu
SINCE=${1:?usage: update_zip.sh SINCE_COMMIT OUTDIR}; OUT=${2:?usage: update_zip.sh SINCE_COMMIT OUTDIR}; mkdir -p "$OUT"
R=$(git -C "$(dirname "$0")" rev-parse --show-toplevel); cd "$R"
BUILD=$(sed -n 's/.*BUILD = "\(rt-app-[^"]*\)".*/\1/p' opt/fdf/src/rangertrak-app/core/src/net/frognet/rangertrak/AppCore.java)
P="opt/fdf/src/rangertrak-app/android opt/fdf/src/rangertrak-app/core opt/fdf/src/rangertrak-app/ui opt/fdf/src/rangertrak-app/README.md opt/fdf/src/rangertrak-app/build.sh opt/fdf/src/frognet-ram-java/src opt/fdf/src/frognet-ram-java/build.sh"
Z="$OUT/RangerTrak-android-update-$BUILD.zip"; rm -f "$Z"
CH=$(git diff --name-only --diff-filter=ACMR "$SINCE" -- $P; git ls-files --others --exclude-standard -- $P)
RM=$(git diff --name-only --diff-filter=D "$SINCE" -- $P)
T=$(mktemp -d); [ -z "$RM" ] || { printf '%s\n' $RM > $T/REMOVED.txt; }
[ -n "$CH" ] && zip -q "$Z" $CH; [ -z "$RM" ] || (cd $T && zip -q "$Z" REMOVED.txt); rm -rf $T
echo "$Z:"; unzip -l "$Z" | sed -n '4,$p'
