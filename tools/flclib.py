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
PACK4 = 0x80     # CoCo extension: 4bpp screen-byte delta (see enc_pack4)
FRAME_TYPE = 0xF1FA

# PACK4 control ops (a packet with count 0 is followed by one of these)
P4_END, P4_SCREEN, P4_SOURCE, P4_NOP = 0, 1, 2, 3
P4_BANK = 8192          # screen bytes per GIME page
P4_SRCCHK = 4096        # chunk bytes between source window checks


def _chunk(ctype, data):
    if len(data) & 1:
        data += b"\0"
    return struct.pack("<IH", len(data) + 6, ctype) + data


def _frame(chunks):
    body = b"".join(chunks)
    return struct.pack("<IHH8x", len(body) + 16, FRAME_TYPE, len(chunks)) + body


# ---------------------------------------------------------------- encoders

def enc_color(pal, six_bit=False):
    out = bytearray(struct.pack("<HBB", 1, 0, len(pal) & 255))  # 1 packet; count 0 = 256
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


# ---------------------------------------------------------------- PACK4

def pack4(frame):
    """(200, 320) array of 4-bit colours -> 32000 GIME screen bytes."""
    return ((frame[:, 0::2] << 4) | frame[:, 1::2]).astype(np.uint8).ravel()


def unpack4(scr):
    nib = np.asarray(scr, np.uint8).reshape(200, 160)
    out = np.empty((200, 320), np.uint8)
    out[:, 0::2] = nib >> 4
    out[:, 1::2] = nib & 15
    return out


# 6809 cycle model of the PACK4 decoder, used to choose packets
P4_OPEN = 30        # a literal packet: skip, count, dispatch
P4_ODD = 14         # odd byte in a literal or fill
P4_LIT = 8          # per literal byte (ldd ,u++ / std ,x++)
P4_FOPEN = 50       # a fill packet
P4_FILL = 4         # per fill byte (std ,x++)


def enc_pack4(prev, cur, lam=4.0):
    """PACK4 chunk taking screen bytes prev -> cur (32000 each), or None.

    The 32000-byte screen is one linear stream of packets:
        skip, count, data
    skip 0-255 bytes, then count 1..127 literal bytes, or count $81..$FF
    (256-n) fills n bytes with the next byte. count 0 is a control op whose
    byte follows: P4_END, P4_SCREEN (move the decoder's 16K screen window on
    by one 8K page), P4_SOURCE (let the decoder slide its source window) or
    P4_NOP (just the skip). The encoder emits P4_SCREEN as soon as a packet
    would start past the first page of the window, and P4_SOURCE every 4K.

    Packets are chosen by a DP over runs minimising decode cycles + lam*bytes.
    Returns (chunk, estimated 6809 decode cycles).
    """
    prev = np.asarray(prev, np.uint8)
    cur = np.asarray(cur, np.uint8)
    diff = prev != cur
    if not diff.any():
        return None, 0
    n = cur.size
    # segments: maximal runs that are unchanged, or changed with one value
    key = np.where(diff, cur.astype(np.int32), -1)
    cut = np.flatnonzero(key[1:] != key[:-1]) + 1
    starts = np.r_[0, cut]
    seg = list(zip(starts.tolist(), np.diff(np.r_[starts, n]).tolist(), key[starts].tolist()))
    while seg[-1][2] < 0:
        seg.pop()

    inf = float("inf")
    lit_open = P4_OPEN + 2 * lam + P4_ODD / 2
    lit_byte = P4_LIT + lam + lit_open / 127

    def fill_cost(ln):
        return -(-ln // 127) * (P4_FOPEN + 3 * lam) + P4_FILL * ln

    L, F = inf, 0.0                 # a literal packet open / closed
    back = []
    for st, ln, v in seg:
        if v < 0:                   # unchanged: copy inside a literal, or skip
            skipc = (ln // 256) * (P4_OPEN + 3 * lam)
            nl, lsrc = L + ln * lit_byte, "L"
            nf, fsrc = (L + skipc, "L") if L < F else (F + skipc, "F")
        else:                       # changed run: literal, or fill
            a, b = L + ln * lit_byte, F + lit_open + ln * lit_byte
            nl, lsrc = (a, "L") if a <= b else (b, "F")
            nf, fsrc = (L + fill_cost(ln), "L") if L < F else (F + fill_cost(ln), "F")
        back.append((lsrc, fsrc))
        L, F = nl, nf
    state = "L" if L <= F else "F"
    tok = [None] * len(seg)
    for i in range(len(seg) - 1, -1, -1):
        lsrc, fsrc = back[i]
        if state == "L":
            tok[i], state = "lit", lsrc
        else:
            tok[i], state = ("skip" if seg[i][2] < 0 else "fill"), fsrc

    # packets: (start, count byte, data, screen bytes)
    pk = []
    i = 0
    while i < len(seg):
        st, ln, v = seg[i]
        if tok[i] == "lit":
            j = i
            while j < len(seg) and tok[j] == "lit":
                j += 1
            end = seg[j - 1][0] + seg[j - 1][1]
            for s in range(st, end, 127):
                c = min(127, end - s)
                pk.append((s, c, cur[s:s + c].tobytes(), c))
            i = j
            continue
        if tok[i] == "fill":
            for s in range(st, st + ln, 127):
                c = min(127, st + ln - s)
                pk.append((s, 256 - c, bytes((v,)), c))
        i += 1

    out = bytearray()
    pos = bank = since = 0
    for s, cb, data, ln in pk:
        while True:
            if pos >= (bank + 1) * P4_BANK:
                out += bytes((0, 0, P4_SCREEN))
                bank += 1
            elif since >= P4_SRCCHK:
                out += bytes((0, 0, P4_SOURCE))
                since = 0
            elif s - pos > 255:
                out += bytes((255, 0, P4_NOP))
                pos += 255
                since += 3
            else:
                break
        out += bytes((s - pos, cb)) + data
        since += 2 + len(data)
        pos = s + ln
    out += bytes((0, 0, P4_END))
    est = sum((P4_OPEN + P4_LIT * ln) if cb < 128 else (P4_FOPEN + P4_FILL * ln)
              for s, cb, data, ln in pk) + P4_ODD * sum(ln & 1 for *_, ln in pk)
    return _chunk(PACK4, bytes(out)), est


def dec_pack4(scr, c):
    """Apply PACK4 chunk data c to a 32000-byte bytearray."""
    q = pos = 0
    while True:
        pos += c[q]
        cb = c[q + 1]
        q += 2
        if cb == 0:
            op = c[q]
            q += 1
            if op == P4_END:
                return
        elif cb < 128:
            scr[pos:pos + cb] = c[q:q + cb]
            q += cb
            pos += cb
        else:
            n = 256 - cb
            scr[pos:pos + n] = bytes((c[q],)) * n
            q += 1
            pos += n


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
    packed = struct.unpack_from("<H", data, 12)[0] == 4
    if packed:
        scr4 = bytearray(32000)
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
                elif ctype == PACK4:
                    dec_pack4(scr4, c)
                    screen = unpack4(scr4)
                elif ctype == BLACK:
                    screen[:] = 0
                elif ctype == FLI_COPY:
                    # read w*h bytes even if the chunk size is short (some
                    # 1993 encoders wrote it 2 bytes low; the CoCo player does too)
                    raw = data[p + 6:p + 6 + w * h]
                    screen[:] = np.frombuffer(raw, np.uint8).reshape(h, w)
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
