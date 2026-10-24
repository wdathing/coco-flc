# flcplay — FLI/FLC player for the TRS-80 Color Computer

Browse an RS-DOS floppy for `.FLC` / `.FLI` animations, pick one, and it loops
full-screen until you press **BREAK**, which returns to the browser.

Status: **CoCo 3 (128K/512K), 6809 or 6309**. CoCo 1/2 are planned (see Roadmap).

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

## Disks

The player reads RS-DOS directories and allocation tables itself, so it handles 35-, 40- and
80-track single-sided disks: `decb dskini -8` gives 156 granules, 360K. Sectors are read through
Disk BASIC's DSKCON, but without CMOC's track-34 limit. DSKCON runs at normal speed, because its
ready timeout is a software loop. At 1.79 MHz that loop expires before a seek can get much past
track 40.

DSKCON's ready timeout also caps a single seek at about 40 tracks even at normal speed, so
`diskRead` breaks longer seeks into hops of 20 tracks or fewer, reading a sector on the way.
Real hardware needs an 80-track drive (typically a 3.5" 720K drive) for the 80-track images.

## 3D PC animations

`media/3dpc/` holds the 24 animations from the 3D PC screen-saver releases (1993/94). They are
not in this repository (`media/` is git-ignored) because we don't hold the rights to them; drop
your own copies there as `*.3D`. They were recovered from `3dpc_1.zip`, `3dpc_2.zip` and `3dpc_bootleg.zip`. The `.AR0`/`.AR1` pairs are one
ARJ archive split in two, so `cat` them together before extracting. They're standard 320×200 FLIs
(COLOR_64, BYTE_RUN, DELTA_FLI) with a `3DPC` tag and a title in the header. DRAGON's first
FLI_COPY chunk has a size field 2 bytes low, which both decoders tolerate.

```
make 3dpc       # convert + pack: build/3dpc/3DPC1..12.dsk, ORIG1..4.dsk (see *.txt there)
make run3dpc    # XRoar with 3DPC1/3DPC2 in drives 0/1
tools/verify3dpc.sh   # play every animation on every disk in XRoar, frame-exact check
```

* `tools/coco16.py` converts an FLI/FLC into a 16-colour FLI. It picks one GIME palette from a
  histogram of all frames, maps every pixel, and re-encodes the deltas. Pixels that changed only
  by palette noise drop out, so the files get 1.5–5× smaller (DOLPHIN 790K → 153K) and play faster. `--dither` adds a
  4×4 ordered dither: better gradients, but bigger files.
* `tools/mkdisks.py` packs files onto self-booting 80-track disks (FLCPLAY.BIN on each).
* `ORIG*.dsk` hold the unconverted originals that fit in RAM and on one disk: SPHERE, BALLS01,
  TOY and HEARTBRN. The player does its own 16-colour reduction on these.

Verified in XRoar (6809, 512K, `tools/verify3dpc.sh`): all 15 converted animations on
3DPC1–12 and the 4 originals on ORIG1–4 play frame-exact. Loading takes 40–100 s from floppy.
Uncapped playback speed (each file asks for about 14 fps):

| Animation | fps | | Animation | fps | | Animation | fps |
|---|---:|---|---|---:|---|---|---:|
| APECHASE | 34.3 | | MAGIC7 | 13.3 | | BARS | 4.2 |
| WORM | 17.1 | | DOLPHIN | 10.7 | | TOY | 4.0 |
| HEARTBRN | 12.6 | | MORFSKY | 10.4 | | BALLS01 | 3.4 |
| PHNXRUN | 8.6 | | BADDAY | 5.5 | | SPHERE | 2.6 |
| MORFFACE | (has empty frames) | | BUBBLES | 2.4 | | FOUNTAIN | 2.0 (6309: 2.3) |

The slow ones change a third of the screen or more every frame, in thousands of tiny runs. With
8-bit FLI data that costs a table lookup per pixel plus about 100 cycles per run. The encoder
already trades a little size for fewer runs (`GAP`/`MINFILL` in coco16.py, roughly 10–20%
faster), and falls back to the compact encoding when a file would no longer fit on a disk.

What doesn't fit yet (after conversion): BIPROT 640K, GGHYPER 603K, DSK03 576K, MARBL1 536K,
CRCLOCK 498K, TWIRL 476K and DRAGON 460K are bigger than the 416K of animation RAM. WHEEL (368K)
and ONPATROL (404K) fit in RAM but not on one 360K disk side.

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
* **Speed tricks:**
  * The CPU runs at 1.79 MHz (`$FFD9`).
  * Byte-oriented chunks (BYTE_RUN, DELTA_FLI) track pixel parity by which branch of the code
    is running, not by a flag in memory, and use separate loops for odd and even run lengths.
  * DELTA_FLC has a word-aligned fast path.
  * Fills of 6 or more bytes jump into an unrolled run of `std ,x++` instructions.
  * Rows advance by adding 160 and re-banking only when crossing a page.
* **6309:** detected at start-up (shown in the browser header). Each chunk is decoded in native
  mode (`LDMD #1`, then back to 6809 mode before returning to C/BASIC), and long fills use `TFM`.
  Every 6309-only opcode sits behind a `flc_6309` test, so one binary runs on both CPUs.
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
make AUTOPLAY=1 NODELAY=1       # ...ignoring the file's frame delay (raw decode speed)
tools/matrix.sh                 # verify + time every test file on 6809 and 6309
tools/xtest.py DISK REF [--cpu 6309]   # one file: boot, wait, time, verify
tools/xprof.py 150              # sampling profiler (PC -> symbol / local label)
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

Current results (XRoar, CoCo 3 512K, 1.79 MHz). The speeds are uncapped (`NODELAY`) decode
rates in frames per second. Every file matches the reference exactly on both CPUs.

| File | Contents | 6809 | 6309 |
|------|----------|-----:|-----:|
| BALLS.FLC  | 320×200 BYTE_RUN + DELTA_FLC        | 13.6 | 16.4 |
| SMALL.FLC  | 161×101, odd width, last-pixel ops  | 32.7 | 37.9 |
| BOUNCE.FLI | 320×200 FLI_COPY + DELTA_FLI        | 15.0 | 18.0 |
| LOOP.FLI   | 255×150 BYTE_RUN + DELTA_FLI        | 21.8 | 26.7 |

BOUNCE.FLI was 7.7 fps before the DELTA_FLI rewrite. Note that XRoar labels its 6309
emulation "unverified".

## Roadmap

1. Room for the big 3D PC animations. Options: double-sided disks (patch DSKCON's drive table
   at $D89D in the RAM copy of Disk BASIC 1.1, or a small WD1793 driver of our own, which would
   also load several times faster); a packed-4-bit "CoCo native" delta chunk (measured at about
   70% of current size, and a much simpler decode); streaming; 2MB GIME boards.
2. Per-frame C overhead (32-bit offset maths in `doFrame`/`peek`) could move to asm.
3. Larger FLCs (640×480 etc.): 2× downscale / crop inside the decoder.
4. Stream from disk for files that don't fit in RAM (128K CoCo 3, CoCo 1/2).
5. Composite-monitor palette (currently RGB values).
6. CoCo 1/2: 6847 VDG output (PMODE 4 dithered or 128×192×4) behind a small platform layer.
