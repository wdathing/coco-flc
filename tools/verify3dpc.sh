#!/bin/sh
# Play every animation on every build/3dpc/*.dsk in XRoar and check it
# frame-exact against the reference decoder (uses AUTOPLAY/NODELAY builds).
# Reads the play order from the PREFIX.lst manifests written by mkdisks.py.
cd "$(dirname "$0")/.." || exit 1
for lst in build/3dpc/3DPC.lst build/3dpc/ORIG.lst; do
    case "$lst" in *ORIG*) src=build/3dpc/orig ;; *) src=build/3dpc/conv ;; esac
    while read -r img idx f; do
        make -s AUTOPLAY="$idx" NODELAY=1 >/dev/null 2>&1 </dev/null || { echo "build failed"; exit 1; }
        cp "build/3dpc/$img" build/verify.dsk
        python3 tools/putplayer.py build/verify.dsk build/FLCPLAY.BIN </dev/null || { echo "copy failed"; exit 1; }
        echo "== $img $f"
        timeout 500 python3 tools/xtest.py build/verify.dsk "$src/$f" --frames 2 --ticks 8 \
            --load-timeout 400 2>&1 </dev/null | sed 's/^/   /'
    done < "$lst"
done
make -s >/dev/null 2>&1   # leave a normal build behind
