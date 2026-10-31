#!/usr/bin/env python3
"""Copy a player build into an RS-DOS image as FLCPLAY.BIN (replacing it).

usage: putplayer.py IMAGE PLAYER.BIN

Handles single-sided images and the double-sided JVC images from
mkdisks.py --sides 2 (the player lives on the front side, set to start in
double-sided mode), which decb can't read.
"""
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mkdisks import ds_player  # noqa: E402

SPT, TL = 18, 18 * 256


def main():
    img, player = sys.argv[1:3]
    data = open(img, "rb").read()
    hdr = len(data) % 256
    if hdr < 2 or data[1] != 2:
        subprocess.run(["decb", "copy", "-2", "-b", "-r", player, f"{img},FLCPLAY.BIN"], check=True)
        return
    body = data[hdr:]
    tracks = len(body) // (2 * TL)
    front = b"".join(body[2 * t * TL:(2 * t + 1) * TL] for t in range(tracks))
    with tempfile.TemporaryDirectory() as tmp:
        player = ds_player(player, os.path.join(tmp, "player"))
        side = os.path.join(tmp, "front.dsk")
        open(side, "wb").write(front)
        subprocess.run(["decb", "copy", "-2", "-b", "-r", player, f"{side},FLCPLAY.BIN"], check=True)
        front = open(side, "rb").read()
    out = bytearray(data)
    for t in range(tracks):
        out[hdr + 2 * t * TL:hdr + (2 * t + 1) * TL] = front[t * TL:(t + 1) * TL]
    open(img, "wb").write(out)


if __name__ == "__main__":
    main()
