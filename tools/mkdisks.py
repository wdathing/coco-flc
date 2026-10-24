#!/usr/bin/env python3
"""Pack FLI/FLC files onto 80-track RS-DOS disk images (decb dskini -8).

usage: mkdisks.py OUTDIR PREFIX PLAYER.BIN FILE... [--tracks 80]

Every disk gets the player so it is self-contained. Files are packed
first-fit-decreasing by granules. Files too big for the CoCo 3's 416K of
animation RAM or for a single disk are skipped and reported.
"""
import argparse
import math
import os
import subprocess

GRAN = 2304
RAM_LIMIT = 52 * 8192          # pagePool on a 512K CoCo 3
GRANULES = {35: 68, 40: 78, 80: 156}


def grans(path):
    return math.ceil(os.path.getsize(path) / GRAN)


def decb(*args):
    subprocess.run(["decb", *args], check=True, capture_output=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("outdir")
    ap.add_argument("prefix")
    ap.add_argument("player")
    ap.add_argument("files", nargs="+")
    ap.add_argument("--tracks", type=int, default=80, choices=sorted(GRANULES))
    a = ap.parse_args()

    os.makedirs(a.outdir, exist_ok=True)
    cap = GRANULES[a.tracks] - grans(a.player)
    files, skipped = [], []
    for f in a.files:
        size = os.path.getsize(f)
        if size > RAM_LIMIT:
            skipped.append(f"{os.path.basename(f)} ({size // 1024}K > 416K RAM)")
        elif grans(f) > cap:
            skipped.append(f"{os.path.basename(f)} ({size // 1024}K > one {a.tracks}-track disk)")
        else:
            files.append(f)
    files.sort(key=grans, reverse=True)
    disks = []                                   # [free, [files]]
    for f in files:
        for d in disks:
            if d[0] >= grans(f):
                d[0] -= grans(f)
                d[1].append(f)
                break
        else:
            disks.append([cap - grans(f), [f]])

    flag = {35: "-3", 40: "-4", 80: "-8"}[a.tracks]
    lines = []
    for i, (free, members) in enumerate(disks, 1):
        img = os.path.join(a.outdir, f"{a.prefix}{i}.dsk")
        if os.path.exists(img):
            os.remove(img)
        decb("dskini", flag, img)
        decb("copy", "-2", "-b", a.player, f"{img},FLCPLAY.BIN")
        for f in sorted(members):
            name = os.path.splitext(os.path.basename(f))[0][:8].upper()
            ext = os.path.splitext(f)[1][1:4].upper() or "FLI"
            decb("copy", "-2", "-b", f, f"{img},{name}.{ext}")
        lines.append(f"{os.path.basename(img)}: " + ", ".join(
            f"{os.path.basename(f)} {os.path.getsize(f) // 1024}K" for f in sorted(members))
            + f"  ({free * GRAN // 1024}K free)")
    if skipped:
        lines.append("not packed: " + "; ".join(skipped))
    with open(os.path.join(a.outdir, f"{a.prefix}.txt"), "w") as fh:
        fh.write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
