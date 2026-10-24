#!/usr/bin/env python3
"""Grab the CoCo 3 GIME 320x200x16 screen from a running XRoar via its GDB stub.

usage: xgrab.py OUT.png [--port 65520] [--text]

Halts the emulator, reads palette + MMU state, banks each screen page into
slot 4 ($8000) to read it, restores the MMU and resumes.  --text dumps the
32x16 text screen at $0400 instead of the graphics screen.
"""
import argparse
import os
import re
import socket
import sys
import time

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import flclib  # noqa: E402

MAP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "build", "FLCPLAY.map")


def symbol(name):
    for line in open(MAP):
        m = re.match(r"Symbol: (\S+) \(.*\) = ([0-9A-F]+)", line)
        if m and m.group(1) == name:
            return int(m.group(2), 16)
    raise SystemExit(f"symbol {name} not in map")


class Gdb:
    def __init__(self, port):
        self.s = socket.create_connection(("127.0.0.1", port), timeout=2)
        self.buf = b""

    def _recv_packet(self):
        while True:
            if self.buf.startswith(b"-"):
                raise TimeoutError("NAK")
            while b"#" not in self.buf or len(self.buf) < self.buf.index(b"#") + 3:
                self.buf += self.s.recv(65536)
            start = self.buf.find(b"$")
            end = self.buf.index(b"#", start)
            pkt = self.buf[start + 1:end]
            self.buf = self.buf[end + 3:]
            self.s.sendall(b"+")
            return pkt.decode()

    def cmd(self, body):
        csum = sum(body.encode()) & 0xFF
        self.s.sendall(f"${body}#{csum:02x}".encode())
        reply = self._recv_packet()
        # a stop reply left over from halt() retries is not our answer
        while body != "?" and re.fullmatch(r"[ST][0-9a-f]{2}.*", reply):
            reply = self._recv_packet()
        return reply

    def halt(self):
        # XRoar stops the machine shortly after a GDB client connects; packets
        # that arrive before that are NAKed or dropped, so poll until it answers.
        for _ in range(20):
            time.sleep(0.2)
            self.buf = b""
            try:
                return self.cmd("?")
            except (TimeoutError, socket.timeout):
                continue
        raise SystemExit("emulator did not stop")

    def read(self, addr, n):
        out = b""
        while n:
            k = min(n, 1024)
            out += bytes.fromhex(self.cmd(f"m{addr:x},{k:x}"))
            addr += k
            n -= k
        return out

    def write(self, addr, data):
        assert self.cmd(f"M{addr:x},{len(data):x}:{data.hex()}") == "OK"

    def run_to(self, addr, timeout=60):
        assert self.cmd(f"Z0,{addr:x},1") == "OK"
        self.s.sendall(b"$c#63")
        self.s.settimeout(timeout)
        reply = self._recv_packet()
        self.s.settimeout(2)
        self.cmd(f"z0,{addr:x},1")
        return reply

    def resume(self):
        self.s.sendall(b"$D#44")
        time.sleep(0.2)
        self.s.close()


def gime_rgb(v):
    r = ((v >> 4) & 2) | ((v >> 2) & 1)
    g = ((v >> 3) & 2) | ((v >> 1) & 1)
    b = ((v >> 2) & 2) | (v & 1)
    return (r * 85, g * 85, b * 85)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--port", type=int, default=65520)
    ap.add_argument("--text", action="store_true")
    ap.add_argument("--page", type=lambda s: int(s, 0), default=0x30)
    ap.add_argument("--sync", action="store_true",
                    help="run until a frame is complete (breakpoint on breakPressed)")
    ap.add_argument("--ref", help="FLC/FLI file: report which frame is on screen and mismatches")
    a = ap.parse_args()

    g = Gdb(a.port)
    g.halt()
    if a.sync:
        g.run_to(symbol("_breakPressed"))
    regs = g.cmd("g")
    r = bytes.fromhex(regs)
    print("CC=%02x A=%02x B=%02x DP=%02x X=%04x Y=%04x U=%04x S=%04x PC=%04x" % (
        r[0], r[1], r[2], r[3], *(int.from_bytes(r[i:i + 2], "big") for i in (4, 6, 8, 10, 12))))
    if a.text:
        scr = g.read(0x400, 512)
        g.resume()
        for row in range(16):
            line = ""
            for b in scr[row * 32:row * 32 + 32]:
                c = b & 0x3F
                line += chr(c + 64) if c < 32 else chr(c)
            print(line)
        return
    pal = g.read(0xFFB0, 16)
    slot4 = g.read(0xFFA4, 1)
    data = b""
    for p in range(4):
        g.write(0xFFA4, bytes((a.page + p,)))
        data += g.read(0x8000, 0x2000)
    g.write(0xFFA4, slot4)
    g.resume()

    colours = [gime_rgb(v & 0x3F) for v in pal]
    img = Image.new("RGB", (320, 200))
    px = img.load()
    for y in range(200):
        row = data[y * 160:(y + 1) * 160]
        for x in range(160):
            b = row[x]
            px[2 * x, y] = colours[b >> 4]
            px[2 * x + 1, y] = colours[b & 15]
    img.resize((640, 400), Image.NEAREST).save(a.out)
    print("palette", pal.hex())
    if a.ref:
        compare(data, a.ref)


def compare(data, ref):
    """For each reference frame, count pixels whose FLC index maps to more than
    one on-screen colour (a correct frame maps each index to exactly one)."""
    nib = np.frombuffer(data[:32000], np.uint8).reshape(200, 160)
    screen = np.empty((200, 320), np.uint8)
    screen[:, 0::2] = nib >> 4
    screen[:, 1::2] = nib & 15
    frames = [f for f, _ in flclib.decode(ref)]
    h, w = frames[0].shape
    x0, y0 = ((320 - w) // 2) & ~1, (200 - h) // 2
    view = screen[y0:y0 + h, x0:x0 + w]
    results = []
    for i, f in enumerate(frames):
        pair = f.astype(np.int32) * 16 + view
        counts = np.bincount(pair.ravel(), minlength=4096).reshape(256, 16)
        bad = int(counts.sum() - counts.max(axis=1).sum())
        results.append((bad, i))
    results.sort()
    bad, i = results[0]
    print(f"best match: frame {i} with {bad} inconsistent pixels "
          f"(next best: frame {results[1][1]} with {results[1][0]})")


if __name__ == "__main__":
    main()
