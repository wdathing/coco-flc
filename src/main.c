/* flcplay - FLI/FLC animation player for the Tandy Color Computer 3
 *
 * Browses an RS-DOS floppy for .FLC/.FLI files, loads the chosen file into
 * banked RAM and loops it on the GIME 320x200x16 screen until BREAK.
 *
 * Memory map while running (CPU view):
 *   $0000-$0DFF  BASIC / Disk BASIC system RAM (DSKCON etc.)
 *   $0E00-$0FFF  nibble lookup tables (see flcdec.asm)
 *   $1000-$5BFF  this program
 *   $5C00-$5FFF  our stack
 *   $6000-$7FFF  banking window for loading and reading the file
 *   $8000-$FFFF  BASIC ROM copy; screen + file windows while a chunk decodes
 */
#include <coco.h>
#include <disk.h>

typedef unsigned long u32;

#define STACK_TOP   ((void *) 0x6000)
#define LOTBL       ((byte *) 0x0E00)
#define HITBL       ((byte *) 0x0F00)
#define WINDOW      ((byte *) 0x6000)
#define MMU_SLOT3   (*(byte *) 0xFFA3)
#define BASIC_SLOT3 0x3B
#define SCR_PAGE    0x30            /* screen at physical $60000-$67CFF */
#define LIST_ROWS   12
#define MAXENT      72

/* flcdec.asm */
extern byte flc_pages[66];
extern byte flc_scr_pages[5];
extern byte flc_src_idx;
extern word flc_src_ptr;
extern word flc_scr_base;
extern word flc_w;
extern word flc_h;
extern byte flc_hist_mode;
extern byte flc_pal_dirty;
extern byte flc_ticks;
extern byte flc_pal[768];
extern word flc_hist[256];
void flc_chunk(byte type);
void flc_clear(byte value);
void irq_install(void);
void irq_remove(void);
void call_with_stack(void (*fn)(void), void *stacktop);

struct Entry
{
    char name[13];      /* "NAME.EXT" */
    byte gran;
    word lastBytes;
    u32 size;
};

struct Entry entries[MAXENT];
byte numEntries;
byte fat[MAX_NUM_GRANULES];
byte drive;
byte poolPages;          /* pages usable for file data */
byte pagePool[52];
u32 fileSize;

byte chosen[16];         /* GIME palette cube index (rrggbb levels) per slot */
byte numChosen;
word sq[64];
word w64[64];
byte savedPalette[16];

/* ------------------------------------------------------------------ helpers */

word rd16(byte *p)
{
    return p[0] | ((word) p[1] << 8);
}

u32 rd32(byte *p)
{
    return ((u32) rd16(p + 2) << 16) | rd16(p);
}

/* Copy n bytes from file offset off. */
void peek(u32 off, byte *dst, word n)
{
    byte idx = (byte) (off >> 13);
    word o = (word) off & 0x1FFF;
    word k;
    while (n)
    {
        k = 0x2000 - o;
        if (k > n)
            k = n;
        MMU_SLOT3 = flc_pages[idx++];
        memcpy(dst, WINDOW + o, k);
        dst += k;
        n -= k;
        o = 0;
    }
    MMU_SLOT3 = BASIC_SLOT3;
}

byte breakPressed(void)
{
    byte v;
    * (byte *) 0xFF02 = 0xFB;           /* column 2 */
    v = * (byte *) 0xFF00;
    * (byte *) 0xFF02 = 0xFF;
    return !(v & 0x40);                  /* row 6: BREAK */
}

/* 128K machines alias page $00 onto page $30. */
byte has512K(void)
{
    byte s0, s30, alias;
    MMU_SLOT3 = SCR_PAGE;
    s30 = *WINDOW;
    MMU_SLOT3 = 0x00;
    s0 = *WINDOW;
    *WINDOW = s30 ^ 0xFF;
    MMU_SLOT3 = SCR_PAGE;
    alias = (*WINDOW == (byte) (s30 ^ 0xFF));
    *WINDOW = s30;
    MMU_SLOT3 = 0x00;
    *WINDOW = s0;
    MMU_SLOT3 = BASIC_SLOT3;
    return !alias;
}

void initMemory(void)
{
    byte i;
    poolPages = 0;
    if (has512K())
        for (i = 0x00; i < 0x30; ++i)
            pagePool[poolPages++] = i;
    for (i = 0x34; i < 0x38; ++i)
        pagePool[poolPages++] = i;
    for (i = 0; i < 5; ++i)
        flc_scr_pages[i] = SCR_PAGE + (i < 4 ? i : 3);
}

/* ------------------------------------------------------------------ disk */

void granToTS(byte g, byte *track, byte *sector)
{
    byte t = g >> 1;
    if (t >= 17)
        ++t;
    *track = t;
    *sector = (g & 1) ? 10 : 1;
}

u32 chainSize(byte g, word lastBytes)
{
    u32 size = 0;
    byte guard, v;
    for (guard = 0; guard < MAX_NUM_GRANULES; ++guard)
    {
        if (g >= MAX_NUM_GRANULES)
            return 0;
        v = fat[g];
        if (v >= 0xC0)
        {
            v &= 0x1F;
            if (v == 0)
                return size;
            return size + (u32) (v - 1) * 256 + lastBytes;
        }
        size += GRANULE_SIZE;
        g = v;
    }
    return 0;
}

byte isAnim(byte *ext)
{
    return ext[0] == 'F' && ext[1] == 'L' && (ext[2] == 'C' || ext[2] == 'I');
}

/* Reads the directory of the current drive. Returns FALSE on I/O error. */
byte readDirectory(void)
{
    byte sec[256];
    byte s, i, k;
    byte *d;
    struct Entry *e;

    numEntries = 0;
    if (!readDiskSector(sec, drive, 17, 2))
        return FALSE;
    memcpy(fat, sec, MAX_NUM_GRANULES);
    for (s = 3; s <= 11; ++s)
    {
        if (!readDiskSector(sec, drive, 17, s))
            return FALSE;
        for (i = 0; i < 8; ++i)
        {
            d = sec + i * 32;
            if (d[0] == 0xFF)
                return TRUE;
            if (d[0] == 0 || !isAnim(d + 8) || numEntries >= MAXENT)
                continue;
            e = &entries[numEntries];
            for (k = 0; k < 8 && d[k] != ' '; ++k)
                e->name[k] = d[k];
            e->name[k++] = '.';
            e->name[k++] = d[8];
            e->name[k++] = d[9];
            e->name[k++] = d[10];
            e->name[k] = 0;
            e->gran = d[13];
            e->lastBytes = ((word) d[14] << 8) | d[15];
            e->size = chainSize(e->gran, e->lastBytes);
            ++numEntries;
        }
    }
    return TRUE;
}

/* Loads the whole file into pagePool pages. Returns an error string or 0. */
const char *loadFile(struct Entry *e)
{
    byte g = e->gran, v, n, s, track, sector, page = 0;
    word off = 0;
    byte guard;

    if (e->size == 0)
        return "BAD FILE CHAIN";
    if (e->size > (u32) poolPages * 8192)
        return "FILE TOO BIG FOR RAM";

    for (guard = 0; guard < MAX_NUM_GRANULES; ++guard)
    {
        v = fat[g];
        n = (v >= 0xC0) ? (v & 0x1F) : 9;
        granToTS(g, &track, &sector);
        for (s = 0; s < n; ++s)
        {
            MMU_SLOT3 = pagePool[page];
            if (!readDiskSector(WINDOW + off, drive, track, sector + s))
            {
                MMU_SLOT3 = BASIC_SLOT3;
                return "DISK READ ERROR";
            }
            off += 256;
            if (off == 8192)
            {
                off = 0;
                ++page;
            }
        }
        if (v >= 0xC0)
            break;
        g = v;
    }
    MMU_SLOT3 = BASIC_SLOT3;

    for (s = 0; s < 66; ++s)
        flc_pages[s] = pagePool[s < poolPages ? s : poolPages - 1];
    fileSize = e->size;
    return 0;
}

/* ------------------------------------------------------------------ colour */

byte level(byte v)
{
    if (v >= 213) return 3;
    if (v >= 128) return 2;
    if (v >= 43)  return 1;
    return 0;
}

/* cube index (r*16+g*4+b, levels 0-3) -> GIME RGB register value */
byte gimeColor(byte q)
{
    byte r = q >> 4, g = (q >> 2) & 3, b = q & 3;
    return ((r & 2) << 4) | ((g & 2) << 3) | ((b & 2) << 2)
         | ((r & 1) << 2) | ((g & 1) << 1) | (b & 1);
}

byte cubeDist(byte a, byte b)
{
    sbyte dr = (sbyte) (a >> 4) - (sbyte) (b >> 4);
    sbyte dg = (sbyte) ((a >> 2) & 3) - (sbyte) ((b >> 2) & 3);
    sbyte db = (sbyte) (a & 3) - (sbyte) (b & 3);
    return (byte) (dr * dr + dg * dg + db * db);
}

/* Pick up to 16 cube colours: most used first, then greedy weighted
 * farthest-point so small but distinct colours still get a slot. */
void chooseColors(void)
{
    word c;
    byte q, k, d, md, best;
    u32 score, bestScore;
    byte *p;

    memset(w64, 0, sizeof(w64));
    for (c = 0; c < 256; ++c)
    {
        if (flc_hist[c] == 0)
            continue;
        p = flc_pal + c * 3;
        q = (level(p[0]) << 4) | (level(p[1]) << 2) | level(p[2]);
        if (w64[q] + flc_hist[c] < w64[q])
            w64[q] = 0xFFFF;
        else
            w64[q] += flc_hist[c];
    }

    numChosen = 0;
    while (numChosen < 16)
    {
        bestScore = 0;
        best = 0;
        for (q = 0; q < 64; ++q)
        {
            if (w64[q] == 0)
                continue;
            md = 255;
            for (k = 0; k < numChosen; ++k)
            {
                d = cubeDist(q, chosen[k]);
                if (d < md)
                    md = d;
            }
            if (md == 0)
                continue;
            score = (u32) w64[q] * md;
            if (score > bestScore)
            {
                bestScore = score;
                best = q;
            }
        }
        if (bestScore == 0)
            break;
        chosen[numChosen++] = best;
    }
    if (numChosen == 0)
        chosen[numChosen++] = 0;
    for (k = numChosen; k < 16; ++k)
        chosen[k] = 0;
}

byte absDiff4(byte v, byte lev)
{
    byte t = lev * 85;
    return (v > t ? v - t : t - v) >> 2;
}

/* Map every FLC palette entry to the nearest chosen colour. */
void buildTables(void)
{
    word c;
    byte k, best, r, g, b;
    word d, bd;
    byte *p = flc_pal;

    for (c = 0; c < 256; ++c, p += 3)
    {
        bd = 0xFFFF;
        best = 0;
        for (k = 0; k < numChosen; ++k)
        {
            r = chosen[k] >> 4;
            g = (chosen[k] >> 2) & 3;
            b = chosen[k] & 3;
            d = sq[absDiff4(p[0], r)] + sq[absDiff4(p[1], g)] + sq[absDiff4(p[2], b)];
            if (d < bd)
            {
                bd = d;
                best = k;
            }
        }
        LOTBL[c] = best;
        HITBL[(byte) c ^ 0x80] = best << 4;
    }
}

/* ------------------------------------------------------------------ video */

void graphicsOn(void)
{
    byte i;
    for (i = 0; i < 16; ++i)
        * (byte *) (0xFFB0 + i) = gimeColor(chosen[i]);
    * (byte *) 0xFF90 = 0x4C;           /* CoCo 3 mode, MMU on, $FExx constant */
    * (byte *) 0xFF98 = 0x80;           /* graphics */
    * (byte *) 0xFF99 = 0x3E;           /* 200 lines, 160 bytes/row, 16 colours */
    * (byte *) 0xFF9C = 0;
    * (byte *) 0xFF9D = SCR_PAGE << 2;  /* physical $60000 / 8 */
    * (byte *) 0xFF9E = 0;
    * (byte *) 0xFF9F = 0;
    * (byte *) 0xFF9A = gimeColor(chosen[LOTBL[0]]);
}

void textOn(void)
{
    byte i;
    /* Same values as the CoCo 3 ROM's reset table at $C22E. */
    * (byte *) 0xFF90 = 0xCC;           /* back to CoCo compatible mode */
    * (byte *) 0xFF98 = 0x00;
    * (byte *) 0xFF99 = 0x00;
    * (byte *) 0xFF9A = 0x00;
    * (byte *) 0xFF9C = 0x0F;
    * (byte *) 0xFF9D = 0xE0;           /* $70000: page $38, low RAM */
    * (byte *) 0xFF9E = 0;
    * (byte *) 0xFF9F = 0;
    for (i = 0; i < 16; ++i)
        * (byte *) (0xFFB0 + i) = savedPalette[i];
}

/* ------------------------------------------------------------------ player */

void setSource(u32 off)
{
    flc_src_idx = (byte) (off >> 13);
    flc_src_ptr = 0xC000 + ((word) off & 0x1FFF);
}

/* Decodes the frame at off. Returns the offset of the next frame, 0 on error. */
u32 doFrame(u32 off)
{
    byte h[16];
    u32 size, p, end, cs;
    word n, ct;

    if (off + 16 > fileSize)
        return 0;
    peek(off, h, 16);
    size = rd32(h);
    end = off + size;
    if (size < 16 || end > fileSize)
        return 0;
    if (rd16(h + 4) == 0xF1FA)
    {
        n = rd16(h + 6);
        p = off + 16;
        while (n--)
        {
            if (p + 6 > end)
                break;
            peek(p, h, 6);
            cs = rd32(h);
            ct = rd16(h + 4);
            if (cs < 6 || p + cs > end)
                break;
            if (ct < 256)
            {
                setSource(p + 6);
                flc_chunk((byte) ct);
            }
            p += cs;
        }
    }
    return end;
}

const char *play(struct Entry *e)
{
    byte hdr[128];
    word magic, frames, fi, x0, y0;
    byte delay, start, i;
    u32 oframe1, oframe2, off, next, ms;
    const char *err;
#ifdef AUTOEXIT
    byte loops = 0;
#endif

    printf("LOADING %s...\n", e->name);
    err = loadFile(e);
    if (err)
        return err;

    peek(0, hdr, 128);
    magic = rd16(hdr + 4);
    if (magic != 0xAF11 && magic != 0xAF12)
        return "NOT AN FLI/FLC FILE";
    frames = rd16(hdr + 6);
    flc_w = rd16(hdr + 8);
    flc_h = rd16(hdr + 10);
    if (flc_w > 320 || flc_h > 200 || flc_w == 0 || flc_h == 0)
        return "SIZE OVER 320X200 (FOR NOW)";
    if (rd16(hdr + 12) != 8 && rd16(hdr + 12) != 0)
        return "NOT 8 BITS PER PIXEL";

    if (magic == 0xAF12)
    {
        ms = rd32(hdr + 16);
        delay = (byte) ((ms * 3 + 25) / 50);        /* ms -> 60Hz ticks */
        oframe1 = rd32(hdr + 80);
        oframe2 = rd32(hdr + 84);
    }
    else
    {
        delay = (byte) ((rd16(hdr + 16) * 6 + 3) / 7); /* 1/70s -> 1/60s */
        oframe1 = 0;
        oframe2 = 0;
    }
    if (oframe1 == 0)
        oframe1 = 128;

    x0 = ((320 - flc_w) / 2) & ~1;
    y0 = (200 - flc_h) / 2;
    flc_scr_base = y0 * 160 + x0 / 2;

    printf("%ux%u, %u FRAMES\nANALYZING COLOURS...\n", flc_w, flc_h, frames);

    /* First frame in histogram mode: pick 16 colours from what is used. */
    memset(flc_pal, 0, sizeof(flc_pal));
    memset(flc_hist, 0, sizeof(flc_hist));
    irq_install();
    flc_hist_mode = 1;
    next = doFrame(oframe1);
    flc_hist_mode = 0;
    if (next == 0)
    {
        irq_remove();
        return "BAD FIRST FRAME";
    }
    if (oframe2 == 0)
        oframe2 = next;
    chooseColors();
    buildTables();

    for (i = 0; i < 16; ++i)
        savedPalette[i] = * (byte *) (0xFFB0 + i) & 0x3F;
    flc_clear(HITBL[0x80] | LOTBL[0]);
    graphicsOn();

    off = oframe1;
    fi = 0;
    for (;;)
    {
        start = flc_ticks;
        next = doFrame(off);
        if (flc_pal_dirty)
        {
            flc_pal_dirty = 0;
            buildTables();
        }
        ++fi;
        if (next == 0 || fi > frames)       /* error, or ring frame done */
        {
            off = (next == 0) ? oframe1 : oframe2;
            fi = (next == 0) ? 0 : 1;
        }
        else if (fi == frames && next + 16 > fileSize)
        {
            off = oframe1;                  /* no ring frame: restart */
            fi = 0;
        }
        else
            off = next;
#ifdef AUTOEXIT
        if (fi == 1 && ++loops > AUTOEXIT)  /* test builds: stop after N loops */
            break;
#endif

        while ((byte) (flc_ticks - start) < delay)
            if (breakPressed())
                break;
        if (breakPressed())
            break;
    }

    irq_remove();
    textOn();
    while (breakPressed())
        ;
    while (inkey())
        ;
    return 0;
}

/* ------------------------------------------------------------------ browser */

void drawList(byte sel, byte top)
{
    byte r, i;
    cls(255);
    printf("FLC PLAYER 0.1        DRIVE %u\n", drive);
    printf("--------------------------------");
    if (numEntries == 0)
        printf("\n NO .FLC/.FLI FILES\n");
    for (r = 0; r < LIST_ROWS; ++r)
    {
        i = top + r;
        if (i >= numEntries)
            break;
        locate(0, 2 + r);
        printf("%c%-12s %5u", i == sel ? '>' : ' ', entries[i].name,
               (word) ((entries[i].size + 1023) >> 10));
        printf("K");
    }
    locate(0, 15);
    printf("ARROWS:MOVE ENTER:PLAY 0-3:DRV");
}

void appMain(void)
{
    byte sel = 0, top = 0, key;
#ifdef AUTOPLAY
    byte autoplayed = 0;
#endif
    byte ok;
    const char *err;

    initMemory();
    drive = 0;
    ok = readDirectory();

    for (;;)
    {
        if (!ok)
        {
            cls(255);
            printf("CAN'T READ DRIVE %u\n\nPRESS 0-3 FOR ANOTHER DRIVE", drive);
        }
        else
            drawList(sel, top);

#ifdef AUTOPLAY
        /* test builds: play entry AUTOPLAY-1 once without a keypress */
        if (sel == 0 && ok && numEntries >= AUTOPLAY && !autoplayed)
        {
            autoplayed = 1;
            sel = AUTOPLAY - 1;
            key = 13;
        }
        else
#endif
        key = waitkey(FALSE);
        if (key >= '0' && key <= '3')
        {
            drive = key - '0';
            sel = top = 0;
            ok = readDirectory();
        }
        else if (!ok)
            continue;
        else if (key == 94 && sel > 0)          /* up arrow */
        {
            if (--sel < top)
                top = sel;
        }
        else if (key == 10 && sel + 1 < numEntries) /* down arrow */
        {
            if (++sel >= top + LIST_ROWS)
                top = sel - LIST_ROWS + 1;
        }
        else if (key == 13 && numEntries)
        {
            cls(255);
            err = play(&entries[sel]);
            if (err)
            {
                printf("\n%s\n\nPRESS ANY KEY", err);
                waitkey(FALSE);
            }
        }
    }
}

int main()
{
    byte i;
    initCoCoSupport();
    if (!isCoCo3)
    {
        printf("SORRY, THIS VERSION NEEDS A\nCOCO 3.\n");
        return 0;
    }
    setHighSpeed(TRUE);
    width(32);
    for (i = 0; i < 64; ++i)
        sq[i] = (word) i * i;
    call_with_stack(appMain, STACK_TOP);
    return 0;
}
