#!/usr/bin/env python3
"""Set the player's start-up settings in FLCPLAY.BIN, or in the FLCPLAY.BIN
on an RS-DOS disk image.

usage: flcconf.py BIN|IMAGE [--url URL] [--minutes N] [--repeat|--once]
                            [--kiosk|--no-kiosk]

--url       network folder to open at start instead of drive 0, e.g.
            TNFS://192.168.1.10/flcdemo/HALLOW/ or HTTP://host/anims/
            ('' for none). The player adds the trailing '/'.
--minutes   kiosk minutes per animation (1-255)
--repeat    kiosk starts over after the last animation (--once: stops)
--kiosk     start in kiosk mode on the start-up folder or drive 0

With no options it prints the current settings.
"""
import argparse
import os
import subprocess
import sys
import tempfile


TAG = b"FLCCONF\0"
URLLEN = 96


def patch(data, a):
    i = data.find(TAG)
    if i < 0:
        raise SystemExit("no FLCCONF block: player too old?")
    c = i + len(TAG)
    if a.url is not None:
        url = a.url.encode()
        if url and not url.endswith(b"/"):
            url += b"/"
        if len(url) >= URLLEN:
            raise SystemExit(f"URL longer than {URLLEN - 1} characters")
        data[c + 3:c + 3 + URLLEN] = url.ljust(URLLEN, b"\0")
    if a.minutes is not None:
        if not 1 <= a.minutes <= 255:
            raise SystemExit("--minutes must be 1-255")
        data[c] = a.minutes
    if a.repeat is not None:
        data[c + 1] = a.repeat
    if a.kiosk is not None:
        data[c + 2] = a.kiosk
    url = bytes(data[c + 3:c + 3 + URLLEN]).split(b"\0")[0].decode()
    print(f"url={url or '(drive 0)'} minutes={data[c]} "
          f"{'repeat' if data[c + 1] else 'once'} {'kiosk' if data[c + 2] else 'browser'}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--url")
    ap.add_argument("--minutes", type=int)
    ap.add_argument("--repeat", dest="repeat", action="store_const", const=1)
    ap.add_argument("--once", dest="repeat", action="store_const", const=0)
    ap.add_argument("--kiosk", dest="kiosk", action="store_const", const=1)
    ap.add_argument("--no-kiosk", dest="kiosk", action="store_const", const=0)
    a = ap.parse_args()

    if not a.file.lower().endswith(".dsk"):
        data = bytearray(open(a.file, "rb").read())
        patch(data, a)
        open(a.file, "wb").write(data)
        return
    with tempfile.TemporaryDirectory() as tmp:
        binf = os.path.join(tmp, "FLCPLAY.BIN")
        r = subprocess.run(["decb", "copy", "-2", "-b", f"{a.file},FLCPLAY.BIN", binf],
                           capture_output=True, text=True)
        if r.returncode:
            raise SystemExit(f"can't read FLCPLAY.BIN from {a.file} (double-sided images: "
                             "patch the BIN and use putplayer.py)")
        data = bytearray(open(binf, "rb").read())
        patch(data, a)
        open(binf, "wb").write(data)
        subprocess.run(["decb", "copy", "-2", "-b", "-r", binf, f"{a.file},FLCPLAY.BIN"],
                       check=True)


if __name__ == "__main__":
    main()
