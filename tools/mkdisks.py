#!/usr/bin/env python3
"""Pack FLI/FLC files onto 80-track RS-DOS disk images (decb dskini -8).

usage: mkdisks.py OUTDIR PREFIX PLAYER.BIN FILE... [--tracks 80] [--sides 2]

Every disk gets the player so it is self-contained. Files are packed
first-fit-decreasing by granules. Files too big for the CoCo 3's 416K of
animation RAM or for a disk are skipped and reported.

--sides 2 makes double-sided JVC images. Each side is an ordinary RS-DOS
volume; with the FD-502 double-sided drive table (which the player sets up)
the back of drive 0 is DRIVE 2. A file bigger than one side is split: NAME.FLI
fills the front with whole granules and NAME.FL2 holds the rest on the back.
The player joins them.

Besides PREFIX.txt (human readable) it writes PREFIX.lst: one line per
animation, "image index name", index being its position in the player's list
(front directory first, then back).
"""
import argparse
import math
import os
import subprocess
import tempfile

GRAN = 2304
RAM_LIMIT = 52 * 8192          # pagePool on a 512K CoCo 3
GRANULES = {35: 68, 40: 78, 80: 156}
SPT = 18


def grans(size):
    return math.ceil(size / GRAN)


def decb(*args):
    subprocess.run(["decb", *args], check=True, capture_output=True)


def ds_player(player, out):
    """Copy of the player that starts in double-sided mode."""
    data = bytearray(open(player, "rb").read())
    i = data.find(b"DSCONF")
    if i < 0:
        raise SystemExit(f"{player}: no DSCONF marker")
    data[i + 6] = 1
    open(out, "wb").write(data)
    return out


def rsname(path, ext=None):
    name = os.path.splitext(os.path.basename(path))[0][:8].upper()
    return f"{name}.{ext or (os.path.splitext(path)[1][1:4].upper() or 'FLI')}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("outdir")
    ap.add_argument("prefix")
    ap.add_argument("player")
    ap.add_argument("files", nargs="+")
    ap.add_argument("--tracks", type=int, default=80, choices=sorted(GRANULES))
    ap.add_argument("--sides", type=int, default=1, choices=(1, 2))
    a = ap.parse_args()

    os.makedirs(a.outdir, exist_ok=True)
    g = GRANULES[a.tracks]
    # one granule spare so a bigger (test) build of the player still fits
    caps = [g - grans(os.path.getsize(a.player)) - 1] + [g] * (a.sides - 1)
    files, skipped = [], []
    for f in a.files:
        size = os.path.getsize(f)
        if size > RAM_LIMIT:
            skipped.append(f"{os.path.basename(f)} ({size // 1024}K > 416K RAM)")
        elif grans(size) > sum(caps):
            skipped.append(f"{os.path.basename(f)} ({size // 1024}K > one disk)")
        else:
            files.append(f)
    files.sort(key=lambda f: os.path.getsize(f), reverse=True)

    # disk = {"free": [per side], "sides": [[(src, offset, length, rsname)]]}
    disks = []
    for f in files:
        size = os.path.getsize(f)
        n = grans(size)
        for d in disks:
            side = next((s for s in range(a.sides) if d["free"][s] >= n), None)
            if side is not None:
                d["free"][side] -= n
                d["sides"][side].append((f, 0, size, rsname(f)))
                break
        else:
            d = {"free": list(caps), "sides": [[] for _ in range(a.sides)]}
            if n <= caps[0]:
                d["free"][0] -= n
                d["sides"][0].append((f, 0, size, rsname(f)))
            else:                       # split over both sides
                first = caps[0] * GRAN
                d["free"] = [0, caps[1] - grans(size - first)]
                d["sides"][0].append((f, 0, first, rsname(f)))
                d["sides"][1].append((f, first, size - first, rsname(f, "FL2")))
            disks.append(d)

    flag = {35: "-3", 40: "-4", 80: "-8"}[a.tracks]
    lines, manifest = [], []
    with tempfile.TemporaryDirectory() as tmp:
        player = ds_player(a.player, os.path.join(tmp, "player")) if a.sides == 2 else a.player
        for i, d in enumerate(disks, 1):
            img = os.path.join(a.outdir, f"{a.prefix}{i}.dsk")
            sides = []
            for s, members in enumerate(d["sides"]):
                simg = os.path.join(tmp, f"side{s}.dsk")
                if os.path.exists(simg):
                    os.remove(simg)
                decb("dskini", flag, simg)
                if s == 0:
                    decb("copy", "-2", "-b", player, f"{simg},FLCPLAY.BIN")
                for src, off, ln, name in sorted(members, key=lambda m: m[3]):
                    part = os.path.join(tmp, "part")
                    with open(src, "rb") as fi, open(part, "wb") as fo:
                        fi.seek(off)
                        fo.write(fi.read(ln))
                    decb("copy", "-2", "-b", part, f"{simg},{name}")
                sides.append(open(simg, "rb").read())
            if a.sides == 1:
                data = sides[0]
            else:                       # JVC: header (18 sectors, 2 sides), tracks interleaved
                tl = SPT * 256
                data = bytes((SPT, 2)) + b"".join(
                    sides[s][t * tl:(t + 1) * tl] for t in range(a.tracks) for s in range(2))
            with open(img, "wb") as fh:
                fh.write(data)
            desc = []
            for s, members in enumerate(d["sides"]):
                names = ", ".join(f"{m[3]} {m[2] // 1024}K" for m in sorted(members, key=lambda m: m[3]))
                desc.append((["front", "back"][s] + ": " if a.sides == 2 else "")
                            + (names or "-") + f" ({d['free'][s] * GRAN // 1024}K free)")
            lines.append(f"{os.path.basename(img)}: " + "; ".join(desc))
            idx = 0
            for members in d["sides"]:
                for src, off, ln, name in sorted(members, key=lambda m: m[3]):
                    if not name.endswith(".FL2"):
                        idx += 1
                        manifest.append(f"{os.path.basename(img)} {idx} {os.path.basename(src)}")
    if skipped:
        lines.append("not packed: " + "; ".join(skipped))
    with open(os.path.join(a.outdir, f"{a.prefix}.txt"), "w") as fh:
        fh.write("\n".join(lines) + "\n")
    with open(os.path.join(a.outdir, f"{a.prefix}.lst"), "w") as fh:
        fh.write("\n".join(manifest) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
