#!/bin/sh
# Start XRoar (CoCo 3, 512K, RGB) in the background with the GDB stub enabled
# and the test disks inserted; types the LOADM/EXEC for you.
# usage: tools/xrun.sh [extra xroar args...]
cd "$(dirname "$0")/.." || exit 1
pkill -x xroar 2>/dev/null; sleep 0.5
xroar -default-machine coco3 -ram 512 -tv-input rgb -ao null -gdb \
    -load-fd0 build/flcplay.dsk -load-fd1 build/flctest2.dsk \
    -type 'LOADM"FLCPLAY":EXEC\r' -timeout 600 "$@" > build/xroar.log 2>&1 &
