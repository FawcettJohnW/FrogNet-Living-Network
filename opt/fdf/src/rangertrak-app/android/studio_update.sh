#!/bin/bash
# studio_update.sh BASE_ZIP OUTDIR -- [RT_ANDROID_UPDATE_IN_PLACE_V1] the Android Studio project exported now
# (studio_project.sh) against the export the user has open (BASE_ZIP, as delivered): the files that differ, at their
# paths in the project, as RangerTrak-Android-Studio-update-<build>.zip. Unzipped in the project's own folder
# (unzip -o), that project is this build: no new directory, no new import. Files gone since are listed in REMOVED.txt.
set -eu
D=$(cd "$(dirname "$0")" && pwd); APP=$(dirname "$D"); BASE=${1:?usage: studio_update.sh BASE_ZIP OUTDIR}; OUT=${2:?usage: studio_update.sh BASE_ZIP OUTDIR}
mkdir -p "$OUT"; OUT=$(cd "$OUT" && pwd); BASE=$(cd "$(dirname "$BASE")" && pwd)/$(basename "$BASE")
BUILD=$(sed -n 's/.*BUILD = "\(rt-app-[^"]*\)".*/\1/p' $APP/core/src/net/frognet/rangertrak/AppCore.java)
T=$(mktemp -d); "$D/studio_project.sh" $T/new >/dev/null; (cd $T/new && unzip -q *.zip); (mkdir $T/old && cd $T/old && unzip -q "$BASE")
N=$(ls -d $T/new/RangerTrak-Android-Studio*/); O=$(ls -d $T/old/RangerTrak-Android-Studio*/)
R=RangerTrak-Android-Studio; ZD=$T/z/$R; mkdir -p $ZD     # [RT_ANDROID_STABLE_ROOT_V1] the update's entries under the project's root
CH=$(cd $N && find . -type f | sed 's|^\./||' | sort | while read f; do cmp -s "$N/$f" "$O/$f" || echo "$f"; done)
RM=$(cd $O && find . -type f | sed 's|^\./||' | sort | while read f; do [ -e "$N/$f" ] || echo "$f"; done)
Z="$OUT/RangerTrak-Android-Studio-update-$BUILD.zip"; rm -f "$Z"
[ -z "$CH" ] || (cd $N && for f in $CH; do mkdir -p "$ZD/$(dirname $f)"; cp -p "$f" "$ZD/$f"; done)
[ -z "$RM" ] || printf '%s\n' $RM > $ZD/REMOVED.txt
# [RT_ANDROID_UPDATE_APPLIES_ITSELF_V1] the project this zip is made over: a project older than that cannot take it
OB=$(sed -n 's/.*BUILD = "\(rt-app-[^"]*\)".*/\1/p' $O/shared/src/core/net/frognet/rangertrak/AppCore.java); echo "$OB" > $ZD/BASE.txt; (cd $T/z && zip -qr "$Z" $R)
echo "$Z (over $(basename "$BASE")):"; unzip -l "$Z" | sed -n '4,$p'; rm -rf $T
