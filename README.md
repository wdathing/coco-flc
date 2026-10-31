# flcplay — FLI/FLC player for the TRS-80 Color Computer

Browse an RS-DOS floppy, or a TNFS or HTTP folder through a FujiNet, for `.FLC` / `.FLI`
animations, pick one, and it loops full-screen until you press **BREAK**, which returns to the
browser. Kiosk mode plays every animation in the list in turn.

Status: **CoCo 3 (128K/512K), 6809 or 6309**. CoCo 1/2 are planned (see Roadmap).

## Using it

```
LOADM"FLCPLAY":EXEC
```

| Key        | Action                          |
|------------|---------------------------------|
| ↑ / ↓      | move selection                  |
| ENTER      | load and play; on a folder, open it |
| 0–3        | switch drive                    |
| N          | browse the network folder (asks for it the first time) |
| U          | type a network folder URL       |
| ←          | (network) up one folder         |
| K          | kiosk: play every file in the list |
| T          | kiosk minutes per file: 1, 2, 3, 5, 10, 15, 30, 60 |
| R          | kiosk: play the list once (ONCE) or forever (LOOP) |
| BREAK      | (while playing) back to the browser; stops kiosk mode |
| C          | toggle RGB / composite (CMP) monitor |

After each load, the line above the key help shows the size, time and speed, e.g.
`LOADED 125K 3.4S 36.2K/S`. Times come from the FujiNet's clock (to 1/100 s), or from another
DriveWire server's clock (whole seconds), because BASIC's 60 Hz timer stops counting while a
transfer has interrupts off. Without DriveWire (a plain floppy controller) only the timer is left;
it can only undercount, so the time is shown as a minimum: `>12.9S <9.6K/S`.

The header shows **RGB** or **CMP**. The player starts in CMP mode if BASIC's palette is still the
power-on composite one, and in RGB mode after the `RGB` command (or any other palette). On a
composite monitor the GIME reads palette registers as intensity + hue, not RGB, so the player
maps each colour to the nearest composite code. Files converted with `coco16.py --cmp` go
further: their 16 colours are chosen from the composite palette itself (e.g. ten shades of
orange and brown where the RGB cube has four), and the codes are in the header (`CMP` + 16
bytes at offset 100). On an RGB monitor they play with the nearest RGB colours.

## Network folders and kiosk mode

With HDB-DOS (the DriveWire DOS a FujiNet CoCo boots), **N** or **U** opens a folder on a TNFS or
HTTP server through the FujiNet's network device, e.g. `TNFS://192.168.1.10/flcdemo/HALLOW/` or
`HTTP://myhost/anims/`. Type it in upper case where the server doesn't care (scheme, host) and
press SHIFT-0 for lower case where it does (paths on a Linux server). The list shows subfolders
first, then the `.FLI`/`.FLC` files (up to 128, names up to 31 characters). Files are read
straight into RAM, with no disk image in between, and from a TNFS server that is more than twice
as fast as loading the same file from a FujiNet-mounted disk image (in XRoar: 36K/s against
15K/s).

* TNFS (and WebDAV servers) are listed with the FujiNet's long directory format. A plain HTTP
  server (`python3 -m http.server`, nginx/Apache autoindex) can't be listed that way, so the
  player reads the page and takes the files and folders from its `href="..."` links.
* Files must be ones the player can play from RAM: PACK4 conversions (`make demos` writes them to
  `build/demos/conv/<SET>/`, and `make install-demos` copies those folders next to the disk
  images) or 320×200-or-smaller FLIs up to 416K.
* **Kiosk mode** (**K**) plays each file in the list for the set number of minutes, loads the
  next, and after the last one stops or starts over (**R**). BREAK stops it. It works on a
  drive's list too.

`tools/flcconf.py` sets the start-up settings in FLCPLAY.BIN or in the copy on a disk image: a
folder to open instead of drive 0, the kiosk minutes and repeat, and whether to start straight
in kiosk mode. A FujiNet-booted CoCo can be a hands-off animation kiosk:

```
tools/flcconf.py build/FLCPLAY.BIN --url TNFS://192.168.1.10/flcdemo/HALLOW/ \
    --minutes 3 --repeat --kiosk
tools/flcconf.py build/FLCPLAY.BIN              # show the settings
```

The network code talks the FujiNet protocol through HDB-DOS's DriveWire read and write routines
(the `$D93F`/`$D941` vectors, which every HDB-DOS build has pointing into its ROM), so it works over any DriveWire link
HDB-DOS supports. Without them (stock Disk BASIC) the network keys report `NO DRIVEWIRE` and the two values found there.

## Building

Needs CMOC (tested with 0.1.100), LWTOOLS (lwasm/lwlink), ToolShed `decb`, and Python 3
with numpy + Pillow for the test tools.

```
make            # build/FLCPLAY.BIN, build/flcplay.dsk (+ BALLS.FLC, SMALL.FLC),
                # build/flctest2.dsk, 80-track (+ BOUNCE.FLI, LOOP.FLI and the
                # PACK4 test files BALLS4.FLI, LOOP4.FLI)
make run        # XRoar, CoCo 3 512K, both disks inserted, auto-typed RGB:LOADM/EXEC
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

### Double-sided disks

Double-sided images (720K) follow the convention of the double-sided FD-502 Disk BASIC ROM. The
only change in that ROM is DSKCON's drive select table, `01 02 41 42` instead of `01 02 04 40`.
Bit $40 of $FF40 is both drive select 3 and the side select, so the back of drive 0 becomes
DRIVE 2 and the back of drive 1 becomes DRIVE 3. Each side is an ordinary RS-DOS volume, readable
with DIR DRIVE 2 under that ROM.

* **S** in the browser toggles double-sided mode, shown as `DS` in the header. The player finds
  the table in Disk BASIC's RAM copy ($D89D in 1.1, $D7AA in 1.0) and patches it. Drives 0–1
  then list both sides of a disk as one.
* **Starting mode:** if the ROM is already the double-sided one, the player starts in
  double-sided mode. So does a player copied onto a double-sided image by `mkdisks.py` or
  `putplayer.py`, which set a `DSCONF` byte in FLCPLAY.BIN.
* **Shared head:** drives 0 and 2 share one head, but DSKCON keeps a separate current track for
  each drive number ($097E–$0981). Before every call the player writes the head's real track
  there.
* **Files bigger than one side:** they are split into NAME.FLI, which fills the front with whole
  granules, and NAME.FL2 on the back. The browser shows one entry, and the loader reads the
  second part straight after the first.
* **Image format:** JVC with a 2-byte header (18 sectors, 2 sides) and the two sides
  interleaved by track. decb can't read these images; `tools/putplayer.py` replaces
  FLCPLAY.BIN on one.

```
tools/mkdisks.py OUTDIR PREFIX build/FLCPLAY.BIN FILE... --sides 2
```

## 3D PC animations

`media/3dpc/` holds the 24 animations from the 3D PC screen-saver releases (1993/94). They are
not in this repository (`media/` is git-ignored) because we don't hold the rights to them; drop
your own copies there as `*.3D`. They were recovered from `3dpc_1.zip`, `3dpc_2.zip` and `3dpc_bootleg.zip`. The `.AR0`/`.AR1` pairs are one
ARJ archive split in two, so `cat` them together before extracting. They're standard 320×200 FLIs
(COLOR_64, BYTE_RUN, DELTA_FLI) with a `3DPC` tag and a title in the header. DRAGON's first
FLI_COPY chunk has a size field 2 bytes low, which both decoders tolerate.

```
make 3dpc       # convert + pack: double-sided build/3dpc/3DPC1..8.dsk, ORIG1..3.dsk
                # (contents in 3DPC.txt / ORIG.txt there)
make run3dpc    # XRoar with 3DPC1/3DPC2 in drives 0/1 (backs are drives 2/3)
tools/verify3dpc.sh   # play every animation on every disk in XRoar, frame-exact check
```

* `tools/coco16.py` picks one 16-colour GIME palette from a histogram of all frames, maps every
  pixel and re-encodes the animation. `make 3dpc` uses `--pack4` (the CoCo packed format below).
  Without it you get a standard 16-colour FLI. `--dither` adds a 4×4 ordered dither: better
  gradients, but bigger files.
* `tools/mkdisks.py --sides 2` packs files onto self-booting double-sided 80-track disks
  (FLCPLAY.BIN on each front, plus one spare granule so a test build of the player fits).
  GGHYPER and DSK03 are split over both sides of their disk.
* `ORIG*.dsk` hold the unconverted originals that fit in RAM: SPHERE, BALLS01, TOY, HEARTBRN and
  MAGIC7 (split). The player does its own 16-colour reduction on these.

Verified in XRoar (6809, 512K, `tools/verify3dpc.sh`): all 23 PACK4 animations on 3DPC1–8 and
the 5 originals on ORIG1–3 play frame-exact, front side, back side and split. Loading takes
29–119 s from floppy (GGHYPER, 403K, is the slowest). Uncapped
playback speed on the 6809 (each file asks for about 14 fps), against the earlier 16-colour FLI
conversion where there was one:

| Animation | PACK4 | FLI | | Animation | PACK4 | FLI |
|---|---:|---:|---|---|---:|---:|
| APECHASE | 60+ | 34.3 | | TOY | 16.6 | 4.0 |
| WORM | 48.0 | 17.1 | | ONPATROL | 16.0 | — |
| MAGIC7 | 43.6 | 13.3 | | WHEEL | 14.5 | — |
| HEARTBRN | 36.9 | 12.6 | | BARS | 13.7 | 4.2 |
| MORFSKY | 34.3 | 10.4 | | BALLS01 | 13.0 | 3.4 |
| DOLPHIN | 32.0 | 10.7 | | MARBL1 | 12.6 | — |
| PHNXRUN | 30.0 | 8.6 | | BUBBLES | 12.0 | 2.4 |
| CRCLOCK | 24.0 | — | | SPHERE | 10.2 (6309: 16.0) | 2.6 |
| DRAGON | 20.9 | — | | FOUNTAIN | 8.3 | 2.0 |
| BADDAY | 17.8 | 5.5 | | TWIRL | 8.0 | — |
| DSK03 | 60 | — | | GGHYPER | 15.0 | — |

MORFFACE also plays frame-exact; it has empty frames, so its tick count isn't a useful speed.

Still missing: BIPROT (450K, more than the 416K of animation RAM).

## Demo sets

`demos/*.lst` list animations from the 1993/94 *Interact* CD-ROM (its `ANIMATE` directory has
~220 FLIs/FLCs), with trims where they would not fit. `hallow.lst` is a Halloween set (pumpkins,
eyeballs, a skeleton hand, a ghost pen, Bigfoot...), `show.lst` a general one. As with the 3D PC
files, the animations are not in the repository: copy the CD's `ANIMATE/*.FL?` to
`media/interact/` (or point `DEMOSRC` at them).

```
make demos                  # build/demos/HALLOW1..n.dsk, SHOW1..n.dsk (+ .txt contents)
make install-demos          # copy them to $(TNFS), default ~/tnfsmnt/flcdemo (FujiNet share)
tools/mkdemo.py LIST|DIR OUTDIR [--src DIR] [--prefix P]   # any list, or a folder of FLIs
```

Each line of a list is `NAME SOURCE [coco16.py options]`. Given a directory instead, `mkdemo.py`
takes every FLI/FLC in it as is. Conversions are cached in `OUTDIR/conv/`. One that is too big
for a disk side (335K next to the player) is retried at every 2nd, 3rd or 4th frame. Disks are
single-sided 80-track (360K), the same layout as a plain DriveWire/FujiNet image.

`coco16.py` options for trimming: `--frames A:B` keeps source frames A..B-1, `--step N` every Nth
frame (the delay is scaled to match). Sources bigger than 320×200 (640×480 FLCs) are scaled down
by an integer factor and centre-cropped.

## PACK4: the CoCo packed format

FLI/FLC pixels are 8-bit, so every pixel costs a table lookup and nibble packing on the way to
the 4bpp screen. PACK4 files store the GIME screen bytes themselves. It's an extension inside the
FLI container, so it is CoCo-only. Other players will reject or ignore it.

* **Header:** a normal FLI header (`$AF11`) with depth = 4 and size 320×200. A smaller animation
  is centred on a full-screen canvas by the converter.
* **Palette:** a standard COLOR_64 chunk with 16 entries. The player loads it straight into the
  GIME palette registers, so there is no colour analysis.
* **Pixels:** chunk type `$80`. Each chunk is a delta of the 32000 screen bytes, treated as one
  linear stream (no rows) of packets `skip, count, data`:

  | count | meaning |
  |---|---|
  | 1–127 | copy `count` literal bytes |
  | $81–$FF | fill 256−`count` bytes with the next byte |
  | 0 | control op, in the next byte: 0 end, 1 next screen page, 2 check source window, 3 nothing (long skips) |

  The encoder knows exactly where the decoder is on screen and in its source window, so it emits
  the page switches and window checks itself. The packet loop has no bounds checks at all.
* **First frame:** a delta from a black screen, so there is no separate key-frame type.
* **Decoder:** about 20 cycles per packet, then a jump through one table into unrolled
  `ldd ,u++`/`std ,x++` (literals) or `std ,x++` (fills). On a 6309, literals of 3 or more words
  are a single `TFM`.
* **Encoder** (`flclib.enc_pack4`): a dynamic program over runs picks literals, fills, skips and
  which unchanged gaps to copy over, minimising modelled 6809 cycles + 4 × bytes.

Against the 16-colour FLI conversion of the same 3D PC animations, PACK4 files are 25–40%
smaller and play 3–5× faster.

```
tools/coco16.py --pack4 IN.FLI OUT.FLI   # convert any FLI/FLC (also prints an fps estimate)
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
exit path. `make AUTOKEYS=DDE` types keys into the browser first (D down, U up, E ENTER, L left,
lower case letters for those keys), e.g. to open a folder and play a file in a network test.
`tools/mkflc.py` regenerates the synthetic test animations and round-trip checks them.

Network tests use XRoar's becker port with FujiNet-PC as the DriveWire server: `-default-machine
coco3 -cart becker`, with a disk holding the test build mounted in FujiNet slot 1 (FujiNet-PC's web
API: `/mount?hostslot=0&deviceslot=0&mode=1&filename=/DISK.dsk`), then `LOADM"FLCPLAY":EXEC`.

Current results (XRoar, CoCo 3 512K, 1.79 MHz). The speeds are uncapped (`NODELAY`) decode
rates in frames per second. Every file matches the reference exactly on both CPUs.

| File | Contents | 6809 | 6309 |
|------|----------|-----:|-----:|
| BALLS.FLC  | 320×200 BYTE_RUN + DELTA_FLC        | 13.6 | 16.4 |
| SMALL.FLC  | 161×101, odd width, last-pixel ops  | 32.7 | 37.9 |
| BOUNCE.FLI | 320×200 FLI_COPY + DELTA_FLI        | 15.0 | 18.0 |
| LOOP.FLI   | 255×150 BYTE_RUN + DELTA_FLI        | 21.8 | 26.7 |
| BALLS4.FLI | BALLS.FLC as PACK4                  | 28.8 | 34.3 |
| LOOP4.FLI  | LOOP.FLI as PACK4 (centred canvas)  | 42.4 | 51.4 |

BOUNCE.FLI was 7.7 fps before the DELTA_FLI rewrite. Note that XRoar labels its 6309
emulation "unverified".

## Roadmap

1. Faster loading: a small WD1793 driver of our own could read whole tracks instead of one
   DSKCON call per sector (loads now take 30–120 s). Network loads spend most of their time in
   HDB-DOS's DWRead byte loop; on a becker-port link (XRoar, a Pico cartridge) a tighter loop of
   our own could roughly double them. Also BIPROT (450K): streaming, or 2MB GIME boards.
2. PACK4 on the 6809: offset-indexed literal copies (`ldd n,u`/`std n,x`) would save about 10%
   more on the busiest animations.
3. Per-frame C overhead (32-bit offset maths in `doFrame`/`peek`) could move to asm.
4. Larger FLCs (640×480 etc.): 2× downscale / crop inside the decoder.
5. Stream from disk for files that don't fit in RAM (128K CoCo 3, CoCo 1/2).
6. CoCo 1/2: 6847 VDG output (PMODE 4 dithered or 128×192×4) behind a small platform layer.
