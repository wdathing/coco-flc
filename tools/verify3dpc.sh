#!/bin/sh
# Play every animation on every build/3dpc/*.dsk in XRoar and check it
# frame-exact against the reference decoder (uses AUTOPLAY/NODELAY builds).
cd "$(dirname "$0")/.." || exit 1
for img in build/3dpc/3DPC*.dsk build/3dpc/ORIG*.dsk; do
    case "$img" in *ORIG*) src=build/3dpc/orig ;; *) src=build/3dpc/conv ;; esac
    i=0
    for f in $(decb dir "$img" | awk '$2=="FLI"||$2=="FLC" {print $1"."$2}'); do
        i=$((i + 1))
        make -s AUTOPLAY=$i NODELAY=1 >/dev/null 2>&1 || { echo "build failed"; exit 1; }
        cp "$img" build/verify.dsk
        decb copy -2 -b -r build/FLCPLAY.BIN build/verify.dsk,FLCPLAY.BIN
        echo "== $(basename "$img") $f"
        timeout 400 python3 tools/xtest.py build/verify.dsk "$src/$f" --frames 2 --ticks 8 \
            --load-timeout 300 2>&1 | sed 's/^/   /'
    done
done
make -s >/dev/null 2>&1   # leave a normal build behind
