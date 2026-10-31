#!/usr/bin/env python3
"""Convert an FLI/FLC into a CoCo 3 friendly 16-colour FLI.

    coco16.py IN.flc OUT.FLI [--dither] [--pack4] [--preview DIR] [--frames A:B] [--step N]

* One palette for the whole animation: a histogram of every frame is binned
  into the GIME 4x4x4 colour cube and 16 colours are picked the same way the
  player does (most used first, then weighted farthest-point).
* Every pixel becomes one of those 16 GIME-exact colours, so the player's own
  colour analysis maps them 1:1.
* Deltas are re-encoded (BYTE_RUN first frame, DELTA_FLI after, ring frame).
  Pixels that only changed by palette noise collapse to "no change", which
  shrinks the file and speeds up playback.
* --pack4 writes the CoCo-only packed format instead: header depth 4, a
  320x200 canvas of GIME screen bytes, a 16-entry COLOR_64 palette and PACK4
  deltas (flclib.enc_pack4). Smaller, and several times faster to play.
* Sources bigger than 320x200 (640x480 etc.) are box-filtered down by an
  integer factor in RGB, then centre-cropped to 320x200.
* --frames A:B keeps source frames A..B-1, --step N every Nth one (the frame
  delay is multiplied by N), to fit long animations into RAM.
* --cmp picks the 16 colours from the GIME's composite palette instead of
  the RGB cube (more shades of the hues a composite monitor does well, e.g.
  ten oranges/browns instead of four) and stores their composite codes in
  the header at CMP_OFS. The COLOR_64 palette gets the nearest RGB colours,
  so the file still plays on an RGB monitor.
* --dither uses a 4x4 ordered (Bayer) dither: better gradients, and because
  the pattern is fixed to the screen, still areas stay still between frames.
"""
import argparse
import os
import struct
import sys

import math

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import flclib  # noqa: E402

LEVELS = np.array([0, 85, 170, 255])
BAYER4 = (np.array([[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]]) + 0.5) / 16 - 0.5


def cube_index(rgb):
    lv = np.clip((rgb.astype(np.int32) + 42) // 85, 0, 3)
    return lv[..., 0] * 16 + lv[..., 1] * 4 + lv[..., 2]


def cube_rgb(q):
    q = np.asarray(q)
    return np.stack([LEVELS[q >> 4], LEVELS[(q >> 2) & 3], LEVELS[q & 3]], -1)


def cmp_rgb(c):
    """Approximate RGB of GIME composite palette code c (MAME's model): bits
    5-4 intensity, 3-0 hue (0 grey). Hue shifts a little with intensity."""
    if c in (0, 16, 32, 48, 63):
        return {0: (0, 0, 0), 16: (47, 47, 47), 32: (120, 120, 120)}.get(c, (255, 255, 255))
    br = -20 + (c // 16 + 1) * 70
    off = c % 16 - 1 + c // 16 * 15
    return tuple(max(0, min(255, int(math.cos(.4195456981879 * 1.01 * (off + k)) * 92 + br)))
                 for k in (9.2, 14.2, 19.2))


CMP_RGB = np.array([cmp_rgb(c) for c in range(64)])
CMP_OFS = 100           # header: b"CMP" + 16 composite codes


def nearest(rgb, cands):
    """index of the nearest candidate colour for each rgb (..., 3)"""
    return ((rgb[..., None, :].astype(np.int32) - cands) ** 2).sum(-1).argmin(-1)


def cmp_table():
    """RGB cube index -> nearest composite code (cmpTab in src/main.c)"""
    return [int(c) for c in nearest(cube_rgb(np.arange(64)), CMP_RGB)]


def choose16(weights, pts=None):
    """weights: pixel counts per candidate colour (default the 64 cube
    colours, pts their RGB) -> up to 16 candidate indices."""
    pts = (cube_rgb(np.arange(64)) if pts is None else pts) / 85
    chosen = []
    while len(chosen) < 16:
        if chosen:
            d = ((pts[:, None, :] - pts[None, chosen, :]) ** 2).sum(-1).min(1)
        else:
            d = np.ones(len(pts))
        score = weights * d
        if score.max() <= 0:
            break
        chosen.append(int(score.argmax()))
    return chosen or [0]


# Encoder tuning. On the CoCo a packet costs roughly as much as 7 literal
# pixels, so it pays to resend a few unchanged pixels inside a span (GAP) and
# to only use fills for longer runs (MINFILL).
GAP = 8
MINFILL = 6
DISK_BYTES = 150 * 2304     # one 80-track disk side after FLCPLAY.BIN


def enc_lc_fast(prev, cur):
    """DELTA_FLI encoder using numpy to find changed spans."""
    h, w = cur.shape
    rows = np.flatnonzero((prev != cur).any(1))
    if rows.size == 0:
        return None
    y0, y1 = int(rows[0]), int(rows[-1]) + 1
    out = bytearray(struct.pack("<HH", y0, y1 - y0))
    for y in range(y0, y1):
        a, b = prev[y], cur[y]
        idx = np.flatnonzero(a != b)
        pk = bytearray()
        npk = 0
        if idx.size:
            # merge changed pixels separated by small gaps (a new packet costs 2 bytes)
            br = np.flatnonzero(np.diff(idx) > GAP)
            starts = idx[np.r_[0, br + 1]]
            ends = idx[np.r_[br, idx.size - 1]] + 1
            x = 0
            for s, e in zip(starts.tolist(), ends.tolist()):
                skip = s - x
                while skip > 255:
                    pk += b"\xff\x00"          # skip 255, empty literal
                    npk += 1
                    skip -= 255
                seg = b[s:e]
                # runs inside the span
                cut = np.flatnonzero(np.diff(seg) != 0) + 1
                rs = np.r_[0, cut]
                rl = np.diff(np.r_[rs, seg.size])
                first = True
                lit = []

                def flush_lit():
                    nonlocal first, npk
                    i = 0
                    while i < len(lit):
                        c = min(127, len(lit) - i)
                        pk.extend(bytes((skip if first else 0, c)))
                        pk.extend(bytes(lit[i:i + c]))
                        first = False
                        npk += 1
                        i += c
                    lit.clear()

                for st, ln in zip(rs.tolist(), rl.tolist()):
                    if ln >= MINFILL:
                        flush_lit()
                        while ln > 0:
                            c = min(128, ln)
                            pk.extend(bytes((skip if first else 0, (256 - c) & 255, int(seg[st]))))
                            first = False
                            npk += 1
                            st += c
                            ln -= c
                    else:
                        lit.extend(seg[st:st + ln].tolist())
                flush_lit()
                x = e
        if npk > 255:   # pathological row: send it as plain literals
            pk = bytearray()
            npk = 0
            x = 0
            while x < w:
                c = min(127, w - x)
                pk += bytes((0, c)) + b[x:x + c].tobytes()
                npk += 1
                x += c
        out.append(npk)
        out += pk
    return flclib._chunk(flclib.DELTA_FLI, bytes(out))


def convert(src, dst, dither=False, preview=None, pack4=False, sel=None, step=1, cmp=False):
    frames, pals = [], []
    for img, pal in flclib.decode(src):
        frames.append(img)
        pals.append(np.array(pal, np.int32))
    hdr = open(src, "rb").read(128)
    nframes = struct.unpack_from("<H", hdr, 6)[0]
    frames, pals = frames[:nframes], pals[:nframes]   # drop the source ring frame
    if struct.unpack_from("<H", hdr, 4)[0] == 0xAF11:
        speed_ms = struct.unpack_from("<H", hdr, 16)[0] * 1000 // 70
    else:
        speed_ms = struct.unpack_from("<I", hdr, 16)[0]
    a, b = sel or (0, nframes)
    frames, pals = frames[a:b:step], pals[a:b:step]
    speed_ms *= step

    weights = np.zeros(64, np.int64)
    for img, pal in zip(frames, pals):
        counts = np.bincount(img.ravel(), minlength=256)
        np.add.at(weights, nearest(pal, CMP_RGB) if cmp else cube_index(pal), counts)
    if cmp:
        chosen = choose16(weights, CMP_RGB)
        crgb = CMP_RGB[chosen].astype(np.int32)
    else:
        chosen = choose16(weights)
        crgb = cube_rgb(chosen).astype(np.int32)

    out = []
    sh, sw = frames[0].shape
    k = max(1, -(-sw // 320), -(-sh // 400))     # 640x480 -> 320x240
    h, w = min(200, sh // k), min(320, sw // k)
    y0, x0 = (sh // k - h) // 2, (sw // k - w) // 2
    thr = np.tile(BAYER4, (h // 4 + 1, w // 4 + 1))[:h, :w, None] * 85
    for img, pal in zip(frames, pals):
        rgb = pal[img].astype(np.float32)
        if k > 1:
            rgb = rgb[:sh // k * k, :sw // k * k].reshape(sh // k, k, sw // k, k, 3).mean((1, 3))
        rgb = rgb[y0:y0 + h, x0:x0 + w]
        if dither:
            rgb = rgb + thr
        d = ((rgb[:, :, None, :] - crgb[None, None, :, :]) ** 2).sum(-1)
        out.append(d.argmin(-1).astype(np.uint8))

    # what goes in the file's palette: RGB cube colours (for --cmp the nearest ones)
    prgb = cube_rgb(cube_index(crgb)) if cmp else crgb
    pal16 = [tuple(int(v) for v in c) for c in prgb] + [(0, 0, 0)] * (256 - len(chosen))
    if pack4:
        # the margin around a smaller animation gets the colour of FLC index 0
        bg = int(((pals[0][0][None, :] - crgb) ** 2).sum(-1).argmin())
        h, w = out[0].shape
        y0, x0 = (200 - h) // 2, ((320 - w) // 2) & ~1
        canv = []
        for f in out:
            c = np.full((200, 320), bg, np.uint8)
            c[y0:y0 + h, x0:x0 + w] = f
            canv.append(c)
        out = canv
        body = encode_pack4(out, pal16[:16])
        depth = 4
    else:
        global GAP, MINFILL
        body = encode(out, pal16)
        if 128 + len(body) > DISK_BYTES and (GAP, MINFILL) != (3, 3):
            GAP, MINFILL = 3, 3     # too big for a disk: fall back to the compact encoding
            body = encode(out, pal16)
        depth = 8
    h, w = out[0].shape
    newhdr = bytearray(128)
    struct.pack_into("<IHHHHHHH", newhdr, 0, 128 + len(body), 0xAF11, len(out), w, h, depth, 3,
                     max(1, round(speed_ms * 70 / 1000)))
    newhdr[26:58] = hdr[26:58]          # keep the 3DPC tag + title
    if cmp:
        newhdr[CMP_OFS:CMP_OFS + 3 + len(chosen)] = b"CMP" + bytes(chosen)
    with open(dst, "wb") as f:
        f.write(newhdr + body)

    if preview:
        from PIL import Image
        os.makedirs(preview, exist_ok=True)
        base = os.path.splitext(os.path.basename(dst))[0]
        Image.fromarray(crgb.astype(np.uint8)[out[min(3, len(out) - 1)]]).save(
            os.path.join(preview, base + ".png"))
    return 128 + len(body), len(chosen)


def encode(out, pal16):
    """Palette + BYTE_RUN, DELTA_FLI frames, ring frame back to frame 1."""
    chunks_out = []
    seq = list(range(len(out))) + [0]
    prev = np.zeros_like(out[0])
    for n, i in enumerate(seq):
        chunks = []
        if n == 0:
            chunks.append(flclib.enc_color(pal16, six_bit=True))
            chunks.append(flclib.enc_brun(out[i]))
        else:
            c = enc_lc_fast(prev, out[i])
            if c:
                chunks.append(c)
        chunks_out.append(flclib._frame(chunks))
        prev = out[i]
    return b"".join(chunks_out)


LAM = 4.0          # PACK4: decode cycles one byte of file is worth
LAM_COMPACT = 12.0  # ... when the result would not fit on a disk


def encode_pack4(out, pal16):
    """COLOR_64 + PACK4 from black, PACK4 deltas, ring frame back to frame 1."""
    scr = [flclib.pack4(f) for f in out]

    def run(lam):
        chunks_out, cycles = [], []
        prev = np.zeros(32000, np.uint8)
        for n, i in enumerate(list(range(len(scr))) + [0]):
            chunks = [flclib.enc_color(pal16, six_bit=True)] if n == 0 else []
            c, cyc = flclib.enc_pack4(prev, scr[i], lam)
            if c:
                chunks.append(c)
            cycles.append(cyc)
            chunks_out.append(flclib._frame(chunks))
            prev = scr[i]
        return b"".join(chunks_out), cycles

    body, cycles = run(LAM)
    if 128 + len(body) > DISK_BYTES:
        body, cycles = run(LAM_COMPACT)
    encode_pack4.cycles = cycles[1:]
    return body


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--dither", action="store_true")
    ap.add_argument("--pack4", action="store_true", help="CoCo packed 4bpp format")
    ap.add_argument("--preview")
    ap.add_argument("--frames", help="A:B, keep source frames A..B-1")
    ap.add_argument("--cmp", action="store_true", help="choose colours for a composite monitor")
    ap.add_argument("--step", type=int, default=1, help="keep every Nth frame")
    ap.add_argument("--verify", action="store_true", help="decode the output and compare")
    a = ap.parse_args()
    sel = tuple(int(v) if v else None for v in a.frames.split(":")) if a.frames else None
    size, ncol = convert(a.src, a.dst, a.dither, a.preview, a.pack4, sel, a.step, a.cmp)
    msg = f"{os.path.basename(a.dst)}: {os.path.getsize(a.src) // 1024}K -> {size // 1024}K ({ncol} colours)"
    if a.verify:
        n = sum(1 for _ in flclib.decode(a.dst))
        msg += f", {n} frames decode OK"
    if a.pack4:
        cyc = encode_pack4.cycles
        msg += f", est. {1.79e6 * len(cyc) / max(1, sum(cyc)):.1f} fps"
    print(msg)


if __name__ == "__main__":
    main()
