#!/usr/bin/env python3
"""Boot XRoar on a disk, wait for playback, verify frames and measure speed.

usage: xtest.py DISK REF.flc [--cpu 6809|6309] [--ram 512] [--frames 4] [--ticks 12]
Needs a test build (make AUTOPLAY=n) on DISK.
"""
import argparse
import os
import socket
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from xgrab import Gdb, symbol  # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("disk")
    ap.add_argument("ref")
    ap.add_argument("--cpu", default="6809")
    ap.add_argument("--ram", default="512")
    ap.add_argument("--frames", type=int, default=4)
    ap.add_argument("--ticks", type=int, default=12)
    ap.add_argument("--load-timeout", type=int, default=240)
    a = ap.parse_args()

    subprocess.run(["pkill", "-x", "xroar"])
    while subprocess.run(["pgrep", "-x", "xroar"], capture_output=True).returncode == 0:
        time.sleep(0.2)
    time.sleep(0.5)
    # a fresh port each run: a just-closed XRoar leaves its port in TIME_WAIT
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    log = open(os.path.join(ROOT, "build", "xroar.log"), "w")
    xr = subprocess.Popen(["xroar", "-default-machine", "coco3", "-machine-cpu", a.cpu,
                           "-ram", a.ram, "-tv-input", "rgb", "-ao", "null", "-gdb",
                           "-gdb-port", str(port),
                           "-load-fd0", a.disk, "-type", 'LOADM"FLCPLAY":EXEC\r',
                           "-timeout", "900"], stdout=log, stderr=log)
    try:
        time.sleep(3)
        g = Gdb(port)
        g.halt()
        t0 = time.time()
        g.run_to(symbol("_breakPressed"), timeout=a.load_timeout)   # first frame done
        print(f"[{a.cpu}] boot+load+first frame: {time.time() - t0 + 3:.0f}s wall")
        ticks, fn = symbol("_flc_ticks"), symbol("_doFrame")
        last, deltas = None, []
        for _ in range(a.ticks + 1):
            g.run_to(fn)
            t = g.read(ticks, 1)[0]
            if last is not None:
                deltas.append((t - last) & 0xFF)
            last = t
        g.resume()
        avg = sum(deltas) / len(deltas)
        print(f"[{a.cpu}] ticks/frame {deltas} avg {avg:.2f} = {60 / avg:.1f} fps")
        for i in range(a.frames):
            out = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "xgrab.py"),
                                  os.path.join(ROOT, "build", f"xtest{i}.png"), "--sync",
                                  "--port", str(port),
                                  "--ref", a.ref], capture_output=True, text=True)
            print(f"[{a.cpu}] " + (out.stdout.strip().splitlines() or [out.stderr])[-1])
    finally:
        xr.terminate()
        xr.wait()


if __name__ == "__main__":
    main()
