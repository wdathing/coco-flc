#!/usr/bin/env python3
"""Statistical profiler: interrupt XRoar over GDB at random moments, record PC,
and attribute samples to the nearest preceding symbol in build/FLCPLAY.map
(and to local @labels in build/flcdec.lst for the decoder).

usage: xprof.py [SAMPLES]   (XRoar must already be running with -gdb)
"""
import bisect
import os
import random
import re
import sys
import time
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from xgrab import MAP, Gdb, symbol  # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def load_symbols():
    syms = {}
    for line in open(MAP):
        m = re.match(r"Symbol: (\S+) \((.*)\) = ([0-9A-F]+)", line)
        if m and not m.group(1).startswith(("funcend_", "funcsize_", "l_", "s_")):
            syms[int(m.group(3), 16)] = m.group(1)
    # local labels from the decoder listing, relocated by the code section base
    base = None
    for line in open(MAP):
        m = re.match(r"Section: code \(build/flcdec.o\) load at ([0-9A-F]+)", line)
        if m:
            base = int(m.group(1), 16)
    lst = os.path.join(ROOT, "build", "flcdec.lst")
    if base is not None and os.path.exists(lst):
        glob = ""
        for line in open(lst):
            # labels start in the listing's source column; instructions are indented
            m = re.match(r"([0-9A-F]{4}) .*?\):\d{5} {9}([^\s;*]\S*)", line)
            if not m:
                continue
            name = m.group(2)
            addr = base + int(m.group(1), 16)
            if name.startswith("@"):
                syms[addr] = glob + name
            else:
                glob = name
                syms.setdefault(addr, name)
    return syms


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    syms = load_symbols()
    addrs = sorted(syms)
    g = Gdb(65520)
    g.halt()
    g.run_to(symbol("_doFrame"), timeout=120)    # wait for playback
    g.resume()
    hits = Counter()
    # Connecting to the stub halts the machine, so each sample is a fresh
    # connection made at a random moment.
    for _ in range(n):
        time.sleep(random.uniform(0.01, 0.05))
        g = Gdb(65520)
        g.halt()
        pc = int(g.cmd("g")[24:28], 16)
        g.resume()
        i = bisect.bisect_right(addrs, pc) - 1
        hits[syms[addrs[i]] if i >= 0 and pc < 0x8000 else f"ROM/{pc >> 12:X}xxx"] += 1
    for name, c in hits.most_common(25):
        print(f"{100 * c / n:5.1f}%  {name}")


if __name__ == "__main__":
    main()
