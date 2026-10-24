"""Minimal FLI/FLC encoder + reference decoder used to build and check test files.

Frames are numpy uint8 arrays of shape (h, w); palettes are lists of 256 (r, g, b)
tuples with 8-bit components.
"""
import struct
import numpy as np

# Chunk types
COLOR_256 = 4
DELTA_FLC = 7    # "SS2"
COLOR_64 = 11
DELTA_FLI = 12   # "LC"
BLACK = 13
BYTE_RUN = 15    # "BRUN"
FLI_COPY = 16
PSTAMP = 18
FRAME_TYPE = 0xF1FA


def _chunk(ctype, data):
    if len(data) & 1:
        data += b"\0"
    return struct.pack("<IH", len(data) + 6, ctype) + data


def _frame(chunks):
    body = b"".join(chunks)
    return struct.pack("<IHH8x", len(body) + 16, FRAME_TYPE, len(chunks)) + body


# ---------------------------------------------------------------- encoders

def enc_color(pal, six_bit=False):
    out = bytearray(struct.pack("<HBB", 1, 0, 0))  # 1 packet, skip 0, count 0 (=256)
    for r, g, b in pal:
        if six_bit:
            out += bytes((r >> 2, g >> 2, b >> 2))
        else:
            out += bytes((r, g, b))
    return _chunk(COLOR_64 if six_bit else COLOR_256, bytes(out))


def _runs(row):
    """Split a row into (kind, start, length) with kind 'fill' or 'lit'."""
    n = len(row)
    i = 0
    out = []
    lit_start = None
    while i < n:
        j = i
        while j < n and row[j] == row[i]:
            j += 1
        if j - i >= 3:
            if lit_start is not None:
                out.append(("lit", lit_start, i - lit_start))
                lit_start = None
            out.append(("fill", i, j - i))
            i = j
        else:
            if lit_start is None:
                lit_start = i
            i = j
    if lit_start is not None:
        out.append(("lit", lit_start, n - lit_start))
    return out


def enc_brun(frame):
    out = bytearray()
    for row in frame:
        pk = bytearray()
        npk = 0
        for kind, s, ln in _runs(row):
            while ln > 0:
                if kind == "fill":
                    c = min(ln, 127)
                    pk += bytes((c, row[s]))
                else:
                    c = min(ln, 128)
                    pk += bytes(((256 - c) & 255,)) + bytes(row[s:s + c])
                s += c
                ln -= c
                npk += 1
        out.append(min(npk, 255))
        out += pk
    return _chunk(BYTE_RUN, bytes(out))


def enc_copy(frame):
    return _chunk(FLI_COPY, frame.tobytes())


def enc_lc(prev, cur):
    """DELTA_FLI (byte oriented)."""
    h, w = cur.shape
    changed = [y for y in range(h) if not np.array_equal(prev[y], cur[y])]
    if not changed:
        return None
    y0, y1 = changed[0], changed[-1] + 1
    out = bytearray(struct.pack("<HH", y0, y1 - y0))
    for y in range(y0, y1):
        a, b = prev[y], cur[y]
        pk = bytearray()
        npk = 0
        x = 0
        while x < w:
            # find next differing pixel
            sx = x
            while x < w and a[x] == b[x]:
                x += 1
            if x >= w:
                break
            skip = x - sx
            while skip > 255:  # long skip: empty literal packet
                pk += bytes((255, 0))
                npk += 1
                skip -= 255
            # extent of the differing span (allow small equal gaps)
            e = x
            while e < w and (a[e] != b[e] or (e + 1 < w and a[e + 1] != b[e + 1])):
                e += 1
            seg = b[x:e]
            first = True
            for kind, s, ln in _runs(seg):
                while ln > 0:
                    if kind == "fill":
                        c = min(ln, 128)
                        body = bytes(((256 - c) & 255, seg[s]))
                    else:
                        c = min(ln, 127)
                        body = bytes((c,)) + bytes(seg[s:s + c])
                    pk += bytes((skip if first else 0,)) + body
                    first = False
                    npk += 1
                    s += c
                    ln -= c
            x = e
        out.append(npk)
        out += pk
    return _chunk(DELTA_FLI, bytes(out))


def enc_ss2(prev, cur):
    """DELTA_FLC (word oriented; handles odd widths with the last-pixel opcode)."""
    h, w = cur.shape
    we = w & ~1
    lines = []
    pending_skip = 0
    for y in range(h):
        a, b = prev[y], cur[y]
        if np.array_equal(a, b):
            pending_skip += 1
            continue
        words_a = a[:we].reshape(-1, 2) if we else np.zeros((0, 2), np.uint8)
        words_b = b[:we].reshape(-1, 2) if we else np.zeros((0, 2), np.uint8)
        nw = len(words_b)
        pk = bytearray()
        npk = 0
        i = 0
        while i < nw:
            si = i
            while i < nw and np.array_equal(words_a[i], words_b[i]):
                i += 1
            if i >= nw:
                break
            skip = (i - si) * 2
            while skip > 255:
                pk += bytes((254, 0))  # skip 254 pixels, 0 literal words
                npk += 1
                skip -= 254
            e = i
            while e < nw and (not np.array_equal(words_a[e], words_b[e])
                              or (e + 1 < nw and not np.array_equal(words_a[e + 1], words_b[e + 1]))):
                e += 1
            first = True
            j = i
            while j < e:
                k = j
                while k < e and np.array_equal(words_b[k], words_b[j]):
                    k += 1
                if k - j >= 2:
                    c = min(k - j, 127)
                    pk += bytes((skip if first else 0, (256 - c) & 255)) + bytes(words_b[j])
                    j += c
                else:
                    k = j
                    while k < e and k - j < 127 and not (k + 1 < e and np.array_equal(words_b[k], words_b[k + 1])):
                        k += 1
                    if k == j:
                        k = j + 1
                    c = k - j
                    pk += bytes((skip if first else 0, c)) + words_b[j:k].tobytes()
                    j = k
                first = False
                npk += 1
            i = e
        entry = bytearray()
        if pending_skip:
            entry += struct.pack("<H", (-pending_skip) & 0xFFFF)
            pending_skip = 0
        if w & 1 and a[w - 1] != b[w - 1]:
            entry += struct.pack("<H", 0x8000 | int(b[w - 1]))
        entry += struct.pack("<H", npk) + pk
        lines.append(bytes(entry))
    if not lines:
        return None
    return _chunk(DELTA_FLC, struct.pack("<H", len(lines)) + b"".join(lines))


def write_anim(path, frames, palettes, kind="flc", speed_ms=66, use_copy_first=False):
    """frames: list of arrays; palettes: list (per frame) of palette or None."""
    h, w = frames[0].shape
    fli = kind == "fli"
    blank = np.zeros_like(frames[0])
    out_frames = []
    seq = list(range(len(frames))) + [0]  # ring frame back to frame 0
    prev = blank
    prev_pal = None
    for n, idx in enumerate(seq):
        cur = frames[idx]
        pal = palettes[idx] if palettes[idx] is not None else prev_pal
        chunks = []
        if pal is not None and pal != prev_pal:
            chunks.append(enc_color(pal, six_bit=fli))
        if n == 0:
            chunks.append(enc_copy(cur) if use_copy_first else enc_brun(cur))
        else:
            d = enc_lc(prev, cur) if fli else enc_ss2(prev, cur)
            if d:
                chunks.append(d)
        out_frames.append(_frame(chunks))
        prev = cur
        prev_pal = pal
    body = b"".join(out_frames)
    hdr = bytearray(128)
    struct.pack_into("<IHHHHHH", hdr, 0, 128 + len(body), 0xAF11 if fli else 0xAF12,
                     len(frames), w, h, 8, 3)
    if fli:
        struct.pack_into("<H", hdr, 16, max(1, round(speed_ms * 70 / 1000)))
    else:
        struct.pack_into("<I", hdr, 16, speed_ms)
        struct.pack_into("<HH", hdr, 38, 1, 1)
        struct.pack_into("<I", hdr, 80, 128)
        struct.pack_into("<I", hdr, 84, 128 + len(out_frames[0]))
    with open(path, "wb") as f:
        f.write(hdr + body)
    return 128 + len(body)


# ---------------------------------------------------------------- decoder

def decode(path):
    """Yield (frame_array, palette) for each frame including the ring frame."""
    data = open(path, "rb").read()
    size, magic, nframes, w, h = struct.unpack_from("<IHHHH", data, 0)
    assert magic in (0xAF11, 0xAF12), hex(magic)
    off = 128
    if magic == 0xAF12:
        o1 = struct.unpack_from("<I", data, 80)[0]
        if o1:
            off = o1
    screen = np.zeros((h, w), np.uint8)
    pal = [(0, 0, 0)] * 256
    while off + 16 <= len(data):
        fsize, ftype, nchunks = struct.unpack_from("<IHH", data, off)
        if fsize < 16:
            break
        if ftype == FRAME_TYPE:
            p = off + 16
            for _ in range(nchunks):
                csize, ctype = struct.unpack_from("<IH", data, p)
                c = data[p + 6:p + csize]
                if ctype in (COLOR_256, COLOR_64):
                    pal = list(pal)
                    npk = struct.unpack_from("<H", c, 0)[0]
                    q, i = 2, 0
                    for _ in range(npk):
                        i += c[q]
                        n = c[q + 1] or 256
                        q += 2
                        for _ in range(n):
                            r, g, b = c[q:q + 3]
                            if ctype == COLOR_64:
                                r, g, b = r << 2, g << 2, b << 2
                            pal[i & 255] = (r, g, b)
                            i += 1
                            q += 3
                elif ctype == BLACK:
                    screen[:] = 0
                elif ctype == FLI_COPY:
                    screen[:] = np.frombuffer(c[:w * h], np.uint8).reshape(h, w)
                elif ctype == BYTE_RUN:
                    q = 0
                    for y in range(h):
                        q += 1
                        x = 0
                        while x < w:
                            n = c[q] if c[q] < 128 else c[q] - 256
                            q += 1
                            if n >= 0:
                                screen[y, x:x + n] = c[q]
                                q += 1
                                x += n
                            else:
                                n = -n
                                screen[y, x:x + n] = np.frombuffer(c[q:q + n], np.uint8)
                                q += n
                                x += n
                elif ctype == DELTA_FLI:
                    y, nl = struct.unpack_from("<HH", c, 0)
                    q = 4
                    for _ in range(nl):
                        npk = c[q]
                        q += 1
                        x = 0
                        for _ in range(npk):
                            x += c[q]
                            n = c[q + 1] if c[q + 1] < 128 else c[q + 1] - 256
                            q += 2
                            if n > 0:
                                screen[y, x:x + n] = np.frombuffer(c[q:q + n], np.uint8)
                                q += n
                                x += n
                            elif n < 0:
                                screen[y, x:x - n] = c[q]
                                q += 1
                                x -= n
                        y += 1
                elif ctype == DELTA_FLC:
                    nl = struct.unpack_from("<H", c, 0)[0]
                    q = 2
                    y = 0
                    for _ in range(nl):
                        last = None
                        while True:
                            wd = struct.unpack_from("<H", c, q)[0]
                            q += 2
                            if wd & 0xC000 == 0xC000:
                                y += 0x10000 - wd
                            elif wd & 0xC000 == 0x8000:
                                last = wd & 0xFF
                            else:
                                npk = wd
                                break
                        x = 0
                        for _ in range(npk):
                            x += c[q]
                            n = c[q + 1] if c[q + 1] < 128 else c[q + 1] - 256
                            q += 2
                            if n > 0:
                                screen[y, x:x + 2 * n] = np.frombuffer(c[q:q + 2 * n], np.uint8)
                                q += 2 * n
                                x += 2 * n
                            elif n < 0:
                                screen[y, x:x - 2 * n] = np.tile(np.frombuffer(c[q:q + 2], np.uint8), -n)
                                q += 2
                                x -= 2 * n
                        if last is not None:
                            screen[y, w - 1] = last
                        y += 1
                p += csize
            yield screen.copy(), list(pal)
        off += fsize
