# flcplay — FLI/FLC player for the TRS-80 Color Computer

Browse an RS-DOS floppy for `.FLC` / `.FLI` animations, pick one, and it loops
full-screen until you press **BREAK**, which returns to the browser.

Status: **CoCo 3 (128K/512K)**. CoCo 1/2 are planned (see Roadmap).

## Using it

```
LOADM"FLCPLAY":EXEC
```

| Key        | Action                          |
|------------|---------------------------------|
| ↑ / ↓      | move selection                  |
| ENTER      | load and play                   |
| 0–3        | switch drive                    |
| BREAK      | (while playing) back to browser |

## Building

Needs CMOC (tested with 0.1.100), LWTOOLS (lwasm/lwlink), ToolShed `decb`, and Python 3
with numpy + Pillow for the test tools.

```
make            # build/FLCPLAY.BIN, build/flcplay.dsk (+ BALLS.FLC, SMALL.FLC),
                # build/flctest2.dsk (+ BOUNCE.FLI)
make run        # XRoar, CoCo 3 512K, both disks inserted, auto-typed LOADM/EXEC
```

## How it works

* **Video:** GIME 320×200×16 (160 bytes/row, physical pages $30–$33). FLC files are 8-bit, so
  each file gets its own 16 colours from the CoCo 3's 64-colour palette.
* **Colour reduction:** the first frame is decoded once in a *histogram* mode. The used palette
  entries are binned into the 4×4×4 GIME colour cube. The most-used colour is picked first, then
  15 more by weighted farthest-point selection. Every FLC index then maps to its nearest chosen
  colour through two 256-byte nibble tables (`$0E00` low nibble, `$0F00` high nibble). Later
  palette chunks are remapped onto the same 16 colours.
* **Loading:** the whole file is read sector-by-sector (DSKCON) into banked RAM. That gives
  up to 416K on a 512K machine, but only 32K on a 128K one. Looping is then pure RAM work.
* **Decoding:** written in 6809 assembly (`src/flcdec.asm`). It decodes straight into the 4bpp
  screen, with no 8-bit shadow buffer. While a chunk decodes, `$8000-$BFFF` banks in the screen
  pages for the current row and `$C000-$FFFF` is a sliding 16K window onto the file. A private
  VSYNC IRQ, hooked through the constant `$FEF7` vector, keeps 60 Hz time for the frame delay.
* Supported chunks: COLOR_256, COLOR_64, BYTE_RUN, DELTA_FLC (SS2), DELTA_FLI (LC), FLI_COPY,
  BLACK. Others (PSTAMP etc.) are skipped. The ring frame is used for seamless looping.

Memory map (CPU view):

```
$0000-$0DFF  BASIC / Disk BASIC system RAM
$0E00-$0FFF  nibble lookup tables
$1000-$5BFF  program (CMOC --limit enforces this)
$5C00-$5FFF  private stack
$6000-$7FFF  C-side bank window (loading, header reads)
$8000-$FFFF  BASIC ROM copy; screen + file windows during a chunk decode
```

## Testing in XRoar

The tools drive XRoar through its GDB stub (`-gdb`):

```
make AUTOPLAY=1                 # test build: plays file #1 without a key press
tools/xrun.sh                   # start XRoar in the background
tools/xgrab.py out.png --sync --ref test/BALLS.FLC   # grab a finished frame, compare
tools/xspeed.py 30              # 60 Hz ticks per frame
tools/xgrab.py t.txt --text     # dump the 32x16 text screen
tools/xwinshot.py win.png       # screenshot the XRoar window
```

`--ref` decodes the same file with the Python reference decoder (`tools/flclib.py`). It reports
which frame is on screen and how many pixels disagree with a consistent index→colour mapping
(0 = exact). `make AUTOPLAY=n AUTOEXIT=k` returns to the browser after `k` loops, to test the
exit path. `tools/mkflc.py` regenerates the synthetic test animations and round-trip checks them.

Current results (XRoar, CoCo 3 512K): all three test files match the reference exactly. BALLS
and SMALL (FLC) run at the files' own 15 fps. BOUNCE (FLI) reaches about 7.7 fps, because LC
deltas still use the general nibble path.

## Roadmap

1. Fast paths for DELTA_FLI and BYTE_RUN, as was done for DELTA_FLC (≈4× there).
2. Larger FLCs (640×480 etc.): 2× downscale / crop inside the decoder.
3. Stream from disk for files that don't fit in RAM (128K CoCo 3, CoCo 1/2).
4. Composite-monitor palette (currently RGB values).
5. CoCo 1/2: 6847 VDG output (PMODE 4 dithered or 128×192×4) behind a small platform layer.
