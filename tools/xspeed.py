#!/usr/bin/env python3
"""Measure playback speed: 60Hz ticks spent per frame (breakpoint on doFrame)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from xgrab import Gdb, symbol  # noqa: E402

n = int(sys.argv[1]) if len(sys.argv) > 1 else 30
g = Gdb(65520)
g.halt()
ticks = symbol("_flc_ticks")
fn = symbol("_doFrame")
last = None
deltas = []
for i in range(n + 1):
    g.run_to(fn)
    t = g.read(ticks, 1)[0]
    if last is not None:
        deltas.append((t - last) & 0xFF)
    last = t
g.resume()
print("ticks/frame:", deltas)
avg = sum(deltas) / len(deltas)
print(f"average {avg:.2f} ticks = {60 / avg:.1f} fps")
