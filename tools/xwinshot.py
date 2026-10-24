#!/usr/bin/env python3
"""Screenshot the XRoar window (X11) to PNG: xwinshot.py OUT.png"""
import struct
import subprocess
import sys

from PIL import Image

tree = subprocess.run(["xwininfo", "-root", "-tree"], capture_output=True, text=True).stdout
wid = None
for line in tree.splitlines():
    if '"XRoar"' in line and "10x10" not in line:
        wid = line.split()[0]
        break
if not wid:
    raise SystemExit("no XRoar window")
raw = subprocess.run(["xwd", "-silent", "-id", wid], capture_output=True).stdout
hdr = struct.unpack(">25I", raw[:100])
hsize, w, h, bpl, ncolors = hdr[0], hdr[4], hdr[5], hdr[12], hdr[19]
off = hsize + ncolors * 12
img = Image.frombuffer("RGBX", (w, h), raw[off:off + bpl * h], "raw", "BGRX", bpl, 1)
img.convert("RGB").save(sys.argv[1])
print(sys.argv[1], w, h)
