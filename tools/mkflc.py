#!/usr/bin/env python3
"""Generate synthetic FLC/FLI test animations and verify them with the reference decoder.

usage: mkflc.py OUTDIR
"""
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import flclib  # noqa: E402


def rainbow_palette():
    pal = []
    for i in range(256):
        if i < 64:          # grey ramp
            v = i * 4
            pal.append((v, v, v))
        else:               # hue wheel
            h = (i - 64) / 192 * 6
            k = int(h) % 6
            f = h - int(h)
            q, t = int(255 * (1 - f)), int(255 * f)
            pal.append([(255, t, 0), (q, 255, 0), (0, 255, t), (0, q, 255),
                        (t, 0, 255), (255, 0, q)][k])
    return pal


def disc(img, cx, cy, r, color):
    h, w = img.shape
    y, x = np.ogrid[:h, :w]
    img[(x - cx) ** 2 + (y - cy) ** 2 <= r * r] = color


def balls(w, h, n):
    frames = []
    for f in range(n):
        t = f / n
        img = np.zeros((h, w), np.uint8)
        # grey sky gradient + hue stripes floor
        for y in range(h):
            img[y, :] = 8 + (y * 50) // h
        floor = int(h * 0.88)
        xs = np.arange(w)
        stripe = 64 + ((xs + f * 6) // 20 % 12) * 16
        img[floor:, :] = stripe.astype(np.uint8)
        # bouncing ball
        bx = int(w * 0.15 + (w * 0.7) * t)
        by = int(floor - 20 - abs(math.sin(t * math.pi * 2)) * h * 0.45)
        disc(img, bx, by, max(6, h // 12), 64 + 2)          # red
        disc(img, bx - 3, by - 3, max(2, h // 40), 63)      # highlight
        # orbiting small balls
        for k in range(3):
            a = t * 2 * math.pi + k * 2.1
            disc(img, int(w / 2 + math.cos(a) * w * 0.3), int(h * 0.3 + math.sin(a) * h * 0.15),
                 max(4, h // 20), 64 + 64 + k * 40)
        frames.append(img)
    return frames


def main():
    outdir = sys.argv[1] if len(sys.argv) > 1 else "."
    os.makedirs(outdir, exist_ok=True)
    pal = rainbow_palette()
    jobs = [
        ("BALLS.FLC", balls(320, 200, 24), "flc", False),
        ("SMALL.FLC", balls(161, 101, 16), "flc", False),
        ("BOUNCE.FLI", balls(320, 200, 12), "fli", True),
        ("LOOP.FLI", balls(255, 150, 10), "fli", False),
    ]
    for name, frames, kind, copy_first in jobs:
        path = os.path.join(outdir, name)
        size = flclib.write_anim(path, frames, [pal] + [None] * (len(frames) - 1),
                                 kind=kind, speed_ms=70, use_copy_first=copy_first)
        decoded = list(flclib.decode(path))
        assert len(decoded) == len(frames) + 1, (len(decoded), len(frames))
        for i, (img, _) in enumerate(decoded):
            ref = frames[i % len(frames)]
            if not np.array_equal(img, ref):
                bad = np.argwhere(img != ref)
                raise SystemExit(f"{name}: frame {i} mismatch at {bad[:5].tolist()}")
        print(f"{name}: {size} bytes, {len(frames)} frames, round-trip OK")


if __name__ == "__main__":
    main()
