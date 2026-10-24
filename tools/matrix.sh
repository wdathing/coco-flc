#!/bin/sh
# Verify + time every test file on 6809 and 6309 (uncapped NODELAY builds).
cd "$(dirname "$0")/.." || exit 1
run() { # autoplay-index disk ref
    make -s AUTOPLAY=$1 NODELAY=1 >/dev/null 2>&1 || { echo "build failed"; exit 1; }
    for cpu in 6809 6309; do
        echo "== $3 $cpu"
        timeout 150 python3 tools/xtest.py "$2" "$3" --cpu $cpu --frames 3 2>&1 | sed 's/^/   /'
    done
}
run 1 build/flcplay.dsk test/BALLS.FLC
run 2 build/flcplay.dsk test/SMALL.FLC
run 1 build/flctest2.dsk test/BOUNCE.FLI
run 2 build/flctest2.dsk test/LOOP.FLI
run 3 build/flctest2.dsk test/BALLS4.FLI
run 4 build/flctest2.dsk test/LOOP4.FLI
make -s >/dev/null 2>&1   # leave a normal build behind
