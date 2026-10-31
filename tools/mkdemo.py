#!/usr/bin/env python3
"""Convert a set of animations to PACK4 and pack them onto demo disks.

usage: mkdemo.py LIST OUTDIR [--src DIR] [--prefix P] [--player BIN] [--jobs N]

LIST is either a text file with one animation per line,

    NAME   SOURCE   [coco16.py options, e.g. --frames 0:180 --step 2]

('#' starts a comment; SOURCE is relative to --src), or a directory, in which
case every *.FLI / *.FLC in it is used with default options and its own name.

Each animation is converted to OUTDIR/conv/NAME.FLI, unless that is already
up to date. One that is too big for a disk side is retried with --step 2, 3,
4 (a lower frame rate) unless the line sets --step itself. The results are
packed by mkdisks.py onto single-sided 80-track disks OUTDIR/PREFIX1.dsk, ...
(PREFIX defaults to the list's name in capitals), with PREFIX.txt listing them.
"""
import argparse
import os
import shlex
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

TOOLS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TOOLS)
from mkdisks import GRAN, GRANULES, grans  # noqa: E402


def read_list(path, src):
    if os.path.isdir(path):
        return [(os.path.splitext(f)[0][:8].upper(), os.path.join(path, f), [])
                for f in sorted(os.listdir(path)) if f.upper().endswith((".FLI", ".FLC"))]
    items = []
    for line in open(path):
        words = shlex.split(line, comments=True)
        if words:
            items.append((words[0].upper()[:8], os.path.join(src, words[1]), words[2:]))
    return items


def convert(name, source, opts, conv, cap):
    out = os.path.join(conv, name + ".FLI")
    stamp = out + ".opt"
    key = f"{os.path.abspath(source)} {' '.join(opts)}"
    if (os.path.exists(out) and os.path.getmtime(out) >= os.path.getmtime(source)
            and os.path.exists(stamp) and open(stamp).read() == key
            and (os.path.getsize(out) <= cap or "--step" in opts)):    # the player may have grown
        return f"{name}: up to date ({os.path.getsize(out) // 1024}K)"
    tries = [opts] if "--step" in opts else [opts] + [opts + ["--step", str(n)] for n in (2, 3, 4)]
    for t in tries:
        r = subprocess.run([sys.executable, "-I", os.path.join(TOOLS, "coco16.py"), "--pack4",
                            source, out, *t], capture_output=True, text=True)
        if r.returncode:
            return f"{name}: FAILED\n{r.stderr.strip()}"
        if os.path.getsize(out) <= cap:
            break
    with open(stamp, "w") as f:
        f.write(key)
    return f"{name} ({' '.join(t) or 'as is'}): {r.stdout.strip()}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("list")
    ap.add_argument("outdir")
    ap.add_argument("--src", default=".", help="directory the list's SOURCE names are in")
    ap.add_argument("--prefix")
    ap.add_argument("--player", default=os.path.join(TOOLS, "..", "build", "FLCPLAY.BIN"))
    ap.add_argument("--jobs", type=int, default=os.cpu_count())
    a = ap.parse_args()

    prefix = a.prefix or os.path.splitext(os.path.basename(a.list.rstrip("/")))[0].upper()[:6]
    items = read_list(a.list, a.src)
    conv = os.path.join(a.outdir, "conv", prefix)
    os.makedirs(conv, exist_ok=True)
    # what mkdisks.py leaves for files on the side that holds the player
    cap = (GRANULES[80] - grans(os.path.getsize(a.player)) - 1) * GRAN
    with ThreadPoolExecutor(a.jobs) as ex:
        for msg in ex.map(lambda it: convert(*it, conv, cap), items):
            print(msg, flush=True)
    files = [os.path.join(conv, name + ".FLI") for name, _, _ in items]
    for f in os.listdir(a.outdir):        # disks left over from a bigger set
        if f.startswith(prefix) and f.endswith(".dsk") and f[len(prefix):-4].isdigit():
            os.remove(os.path.join(a.outdir, f))
    subprocess.run([sys.executable, os.path.join(TOOLS, "mkdisks.py"), a.outdir, prefix,
                    a.player, *[f for f in files if os.path.exists(f)]], check=True)


if __name__ == "__main__":
    main()
