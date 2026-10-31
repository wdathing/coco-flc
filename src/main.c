/* flcplay - FLI/FLC animation player for the Tandy Color Computer 3
 *
 * Browses an RS-DOS floppy, or a TNFS/HTTP folder through a FujiNet, for
 * .FLC/.FLI files, loads the chosen file into banked RAM and loops it on the
 * GIME 320x200x16 screen until BREAK. Kiosk mode plays every file in the
 * list for a set number of minutes each.
 *
 * Memory map while running (CPU view):
 *   $0000-$0DFF  BASIC / Disk BASIC system RAM (DSKCON etc.)
 *   $0E00-$0FFF  nibble lookup tables (see flcdec.asm)
 *   $1000-$5DFF  this program
 *   $5E00-$5FFF  our stack
 *   $6000-$7FFF  banking window for loading and reading the file; the rest
 *                of the time BASIC's page $3B, which holds struct Data
 *   $8000-$FFFF  BASIC ROM copy; screen + file windows while a chunk decodes
 */
#include <coco.h>

typedef unsigned long u32;

#define STACK_TOP   ((void *) 0x6000)
#define LOTBL       ((byte *) 0x0E00)
#define HITBL       ((byte *) 0x0F00)
#define WINDOW      ((byte *) 0x6000)
#define MMU_SLOT3   (*(byte *) 0xFFA3)
#define BASIC_SLOT3 0x3B
#define SCR_PAGE    0x30            /* screen at physical $60000-$67CFF */
#define LIST_ROWS   11
#define MAX_GRAN    156             /* 80-track single sided RS-DOS (decb dskini -8) */
#define GRAN_SIZE   2304
#define MAXENT      128
#define NAMELEN     32
#define URLLEN      160
#define TIMER       (* (word *) 0x0112)    /* BASIC's 60 Hz counter */

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
extern byte flc_pal_lo;
extern byte flc_pal_hi;
extern byte flc_ticks;
#define flc_pal     ((byte *) 0x7A00)   /* FLCPAL, FLCHIST in flcdec.asm */
#define flc_hist    ((word *) 0x7D00)
extern byte flc_6309;
byte cpu_is_6309(void);
void flc_init(void);
void flc_chunk(byte type);
void flc_clear(byte value);
void irq_install(void);
void irq_remove(void);
void call_with_stack(void (*fn)(void), void *stacktop);

/* A file can be in two parts: NAME.FLI and NAME.FL2 on the other side of
 * a double-sided disk (see loadFile). */
struct Part
{
    byte side;          /* 0 front, 1 back (DSKCON drive + 2) */
    byte gran;
    word lastBytes;
    u32 size;
};

enum { KIND_DISK, KIND_FILE, KIND_DIR, KIND_UP };    /* disk file; network file, folder, .. */

struct Entry
{
    char name[NAMELEN]; /* "NAME.EXT"; network names as listed, folders end in '/' */
    byte kind;
    u32 size;           /* network: from the listing, 0 if it has none */
};

/* A disk file's parts, after its (at most 12 character) name. part[1].size
 * == 0: single part. */
#define PARTS(e)    ((struct Part *) ((e)->name + 13))

#define MAXPART2    16

/* In page $3B at $6000, so only while the loading window isn't mapped. */
struct Data
{
    struct Entry entries[MAXENT];
    char part2Name[MAXPART2][9];
    struct Part part2[MAXPART2];
    byte cmd[261];          /* network open command */
    char url[URLLEN];       /* network folder, "TNFS://HOST/PATH/" */
    char line[64];          /* listing line being parsed */
    byte buf[512];          /* listing data, directory sectors */
    byte fat[2][MAX_GRAN];  /* per side */
    word sq[64];
    word w64[64];
};                          /* then flc_pal and flc_hist at $7A00 (flcdec.asm) */
typedef char dataFits[sizeof(struct Data) <= 0x1A00 ? 1 : -1];

#define DATA        ((struct Data *) 0x6000)
#define entries     (DATA->entries)
#define part2Name   (DATA->part2Name)
#define part2       (DATA->part2)
#define fat         (DATA->fat)
#define sq          (DATA->sq)
#define w64         (DATA->w64)

byte numEntries;
byte numPart2;
struct Entry cur;           /* copy of the entry being loaded */
byte netMode;               /* browsing the network folder DATA->url */
byte stopped;               /* BREAK ended play() or a network wait */
byte drive;              /* 0-3; with dsMode 0-1 (both sides) */
byte dsMode;             /* drives 2/3 are the back sides of drives 0/1 */
byte *driveTable;        /* DSKCON drive select bits in Disk BASIC's RAM copy */
/* Set to 1 in FLCPLAY.BIN by tools/mkdisks.py / putplayer.py when they put
 * the player on a double-sided image, so it starts in double-sided mode. */
char dsConfig[7] = { 'D', 'S', 'C', 'O', 'N', 'F', 0 };
/* Set by tools/flcconf.py: kiosk minutes per file, kiosk repeats, start in
 * kiosk mode, then the network folder to open at start (empty: drive 0). */
#define CONF_URLLEN 96
char flcConf[11 + CONF_URLLEN] = { 'F', 'L', 'C', 'C', 'O', 'N', 'F', 0, 2, 0, 0 };
#define confMinutes flcConf[8]
#define confRepeat  flcConf[9]
#define confKiosk   flcConf[10]
#define confUrl     (flcConf + 11)
byte poolPages;          /* pages usable for file data */
byte pagePool[52];
u32 fileSize;

byte chosen[16];         /* GIME palette cube index (rrggbb levels) per slot */
byte numChosen;
byte savedPalette[16];
byte packed;             /* PACK4 file: header depth 4, 16-colour palette */
byte cmpMode;            /* composite monitor: palette registers are intensity+hue */
byte hdrCmp[16];         /* composite codes from the header (coco16.py --cmp) */
byte hasCmp;

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

#define DRTRK   ((byte *) 0x097E)    /* DSKCON's current track per drive */

byte headTrack[4] = { 0xFF, 0xFF, 0xFF, 0xFF };    /* per physical drive; FF = unknown */

/* Find Disk BASIC's drive select table: 01 02 04 40 normally, 01 02 41 42
 * in the double-sided FD-502 ROM. Bit $40 of $FF40 is drive select 3 and
 * the side select. */
void findDriveTable(void)
{
    byte *p;
    for (p = (byte *) 0xC000; p < (byte *) 0xDFFC; ++p)
        if (p[0] == 1 && p[1] == 2 && ((p[2] == 4 && p[3] == 0x40)
                                       || (p[2] == 0x41 && p[3] == 0x42)))
        {
            driveTable = p;
            dsMode = p[2] == 0x41;
            return;
        }
}

void setDsMode(byte on)
{
    if (!driveTable)
        return;
    dsMode = on;
    driveTable[2] = on ? 0x41 : 0x04;
    driveTable[3] = on ? 0x42 : 0x40;
    memset(headTrack, 0xFF, 4);
}

/* DSKCON with no range checks. */
byte dskcon(byte *buf, byte drv, byte track, byte sector)
{
    byte phys = dsMode ? drv & 1 : drv;
    /* Drives 0 and 2 (1 and 3) share a head in double-sided mode; tell
     * DSKCON where it really is so it doesn't seek from a stale track. */
    if (headTrack[phys] != 0xFF)
        DRTRK[drv] = headTrack[phys];
    * (byte *) 0x00EA = 2;              /* DCOPC: read */
    * (byte *) 0x00EB = drv;            /* DCDRV */
    * (byte *) 0x00EC = track;          /* DCTRK */
    * (byte *) 0x00ED = sector;         /* DCSEC */
    * (byte **) 0x00EE = buf;           /* DCBPT */
    /* DSKCON's ready timeout is a software loop: at 1.79 MHz it expires
     * before the head can step much past track 40, so run it at normal
     * speed (transfers are paced by the drive anyway). */
    asm
    {
        pshs    u,y
        clr     $FFD8
        jsr     [$C004]
        clr     $FFD9
        puls    y,u
    }
    return * (byte *) 0x00F0 == 0;      /* DCSTA */
}

/* Read one sector. Unlike CMOC's dskcon() this doesn't stop at track 34, so
 * 40 and 80 track disks work. Even at normal speed DSKCON's timeout only
 * covers a seek of roughly 40 tracks, so longer seeks are done in hops of
 * at most 20 tracks by reading a sector on the way (into buf, which the
 * real read then overwrites). CoCo 3 only for now ($FFD8/$FFD9 speed pokes). */
byte diskRead(byte *buf, byte drv, byte track, byte sector)
{
    byte *h = &headTrack[dsMode ? drv & 1 : drv & 3];
    byte next;
    while (*h != 0xFF && (*h > track ? *h - track : track - *h) > 20)
    {
        /* *h must still be the head's position during the call: dskcon()
         * hands it to DSKCON as the track to seek from. */
        next = (*h > track) ? *h - 20 : *h + 20;
        if (!dskcon(buf, drv, next, 1))
        {
            *h = 0xFF;
            break;
        }
        *h = next;
    }
    if (!dskcon(buf, drv, track, sector))
    {
        *h = 0xFF;
        return FALSE;
    }
    *h = track;
    return TRUE;
}

void granToTS(byte g, byte *track, byte *sector)
{
    byte t = g >> 1;
    if (t >= 17)
        ++t;
    *track = t;
    *sector = (g & 1) ? 10 : 1;
}

u32 chainSize(byte *f, byte g, word lastBytes)
{
    u32 size = 0;
    byte guard, v;
    for (guard = 0; guard < MAX_GRAN; ++guard)
    {
        if (g >= MAX_GRAN)
            return 0;
        v = f[g];
        if (v >= 0xC0)
        {
            v &= 0x1F;
            if (v == 0)
                return size;
            return size + (u32) (v - 1) * 256 + lastBytes;
        }
        size += GRAN_SIZE;
        g = v;
    }
    return 0;
}

/* 0: not listed, 1: FLC/FLI, 2: second part (FL2) */
byte fileKind(byte *ext)
{
    if (ext[0] != 'F' || ext[1] != 'L')
        return 0;
    if (ext[2] == 'C' || ext[2] == 'I')
        return 1;
    return ext[2] == '2' ? 2 : 0;
}

/* Adds the files of one side of the current drive. FALSE on I/O error. */
byte readSide(byte side)
{
    byte *sec = DATA->buf;
    byte s, i, k, kind, drv = drive + (side << 1);
    byte *d;
    byte *f = fat[side];
    struct Entry *e;
    struct Part *pt;

    if (!diskRead(sec, drv, 17, 2))
        return FALSE;
    memcpy(f, sec, MAX_GRAN);
    for (s = 3; s <= 11; ++s)
    {
        if (!diskRead(sec, drv, 17, s))
            return FALSE;
        for (i = 0; i < 8; ++i)
        {
            d = sec + i * 32;
            if (d[0] == 0xFF)
                return TRUE;
            kind = d[0] ? fileKind(d + 8) : 0;
            if (kind == 1 && numEntries < MAXENT)
            {
                e = &entries[numEntries++];
                for (k = 0; k < 8 && d[k] != ' '; ++k)
                    e->name[k] = d[k];
                e->name[k++] = '.';
                e->name[k++] = d[8];
                e->name[k++] = d[9];
                e->name[k++] = d[10];
                e->name[k] = 0;
                e->kind = KIND_DISK;
                pt = PARTS(e);
                pt[1].size = 0;
            }
            else if (kind == 2 && numPart2 < MAXPART2)
            {
                for (k = 0; k < 8 && d[k] != ' '; ++k)
                    part2Name[numPart2][k] = d[k];
                part2Name[numPart2][k] = 0;
                pt = &part2[numPart2++];
            }
            else
                continue;
            pt->side = side;
            pt->gran = d[13];
            pt->lastBytes = ((word) d[14] << 8) | d[15];
            pt->size = chainSize(f, pt->gran, pt->lastBytes);
        }
    }
    return TRUE;
}

/* Reads the directory of the current drive (both sides in dsMode) and
 * joins NAME.FL2 parts onto their NAME.FLI/FLC. FALSE on I/O error. */
byte readDirectory(void)
{
    byte i, j, k;
    char *n;

    numEntries = 0;
    numPart2 = 0;
    if (!readSide(0))
        return FALSE;
    if (dsMode)
        readSide(1);            /* a single-sided disk just has no back */
    for (j = 0; j < numPart2; ++j)
        for (i = 0; i < numEntries; ++i)
        {
            n = entries[i].name;
            for (k = 0; n[k] != '.' && n[k] == part2Name[j][k]; ++k)
                ;
            if (n[k] == '.' && part2Name[j][k] == 0 && PARTS(&entries[i])[1].size == 0)
                memcpy(&PARTS(&entries[i])[1], &part2[j], sizeof(struct Part));
        }
    for (i = 0; i < numEntries; ++i)
        entries[i].size = PARTS(&entries[i])[0].size + PARTS(&entries[i])[1].size;
    return TRUE;
}

byte loadPage, loadDone;
word loadOff;

/* Reads one part's granule chain on into pagePool. FALSE on I/O error. */
byte loadPart(struct Part *pt)
{
    byte g = pt->gran, v, n, s, track, sector, guard;
    byte drv = drive + (pt->side << 1);
    byte *f = fat[pt->side];

    for (guard = 0; guard < MAX_GRAN; ++guard)
    {
        if (g >= MAX_GRAN)
            break;
        v = f[g];
        n = (v >= 0xC0) ? (v & 0x1F) : 9;
        granToTS(g, &track, &sector);
        for (s = 0; s < n; ++s)
        {
            MMU_SLOT3 = pagePool[loadPage];
            v = diskRead(WINDOW + loadOff, drv, track, sector + s);
            MMU_SLOT3 = BASIC_SLOT3;        /* fat[] is in that slot too */
            if (!v)
                return FALSE;
            v = f[g];
            loadOff += 256;
            if (loadOff == 8192)
            {
                loadOff = 0;
                ++loadPage;
            }
        }
        if (v >= 0xC0)
            break;
        g = v;
    }
    return TRUE;
}

/* Loads the whole file (both parts) into pagePool pages. A first part
 * always fills whole sectors, so the second part follows on directly.
 * Returns an error string or 0. */
const char *loadDisk(struct Entry *e)
{
    struct Part *pt = PARTS(e);
    if (pt[0].size == 0)
        return "BAD FILE CHAIN";
    if (e->size > (u32) poolPages * 8192)
        return "FILE TOO BIG FOR RAM";
    if (!loadPart(&pt[0]))
        return "DISK READ ERROR";
    if (pt[1].size)
    {
        if (pt[0].size & 0xFF)
            return "PART 1 NOT WHOLE SECTORS";
        if (!loadPart(&pt[1]))
            return "DISK READ ERROR";
    }
    fileSize = e->size;
    return 0;
}

/* ------------------------------------------------------------------ DriveWire */

#define OP_NET      0xE3
#define OP_TIME     0x23
#define NET_UNIT    1
#define NS_OK       1
#define NS_EOF      136

byte dwPresent;     /* HDB-DOS: DWRead/DWWrite vectors at $D93F/$D941 */
word dwBlock;       /* largest reply to ask for in one DWRead */
char dwMsg[32];

/* HDB-DOS (every build: becker, bit banger, CoCo 1/2/3) has its DWRead and
 * DWWrite vectors at $D93F/$D941, pointing into its own ROM. At those
 * addresses stock Disk BASIC 1.0-1.2 has code that doesn't make two such
 * addresses. Not the banner: a custom build may not say HDB-DOS. */
void findDriveWire(void)
{
    word r = * (word *) 0xD93F, w = * (word *) 0xD941;
    byte *p;
    dwPresent = r >= 0xC000 && r < 0xE000 && w >= 0xC000 && w < 0xE000;
    /* HDB-DOS only ever reads 256-byte sectors, and some links rely on that
     * (One ROM's bridge hands over a reply through a 256-byte window and
     * drops bytes the CoCo hasn't asked for yet). A becker port buffers any
     * amount, and there bigger reads save a lot of round trips. */
    dwBlock = 256;
    for (p = (byte *) 0xC000; p < (byte *) 0xDFFA; ++p)
        if (p[0] == 'B' && memcmp(p, "BECKER", 6) == 0)
            dwBlock = 8192;
    sprintf(dwMsg, "NO DRIVEWIRE ($D93F: %04X %04X)", r, w);
}

void dwWrite(byte *s, word n)
{
    asm
    {
        pshs    x,y,u,dp
        ldx     :s
        ldy     :n
        jsr     [$D941]
        puls    dp,u,y,x
    }
}

/* TRUE if all n bytes arrived before DWRead's timeout. */
byte dwRead(byte *s, word n)
{
    byte ok;
    asm
    {
        pshs    x,y,u,dp
        ldx     :s
        ldy     :n
        jsr     [$D93F]
        puls    dp,u,y,x
        tfr     cc,a
        anda    #4                      ; Z: all bytes received
        sta     :ok
    }
    return ok;
}

/* Waits for a reply. The FujiNet answers once it has done the command, which
 * can take several DWRead timeouts; the command isn't resent (a FujiNet that
 * is busy queues what we send and would answer every copy). The reply is read
 * in one DWRead: on a fast serial link nothing buffers bytes that arrive
 * between two calls. */
byte dwGet(byte *p, word n)
{
    word t0 = TIMER;
    while (!dwRead(p, n))
        if (TIMER - t0 > 1800 || (stopped = breakPressed()))
            return FALSE;
    return TRUE;
}

#define OP_CLOCK    0xE5
#define NOCLOCK     0xFFFF

byte fnClock;       /* FujiNet clock with hundredths (APETIME 'M') */

/* Is the server a FujiNet whose clock gives hundredths? One DWRead timeout
 * if it isn't. */
void findClock(void)
{
    byte t[8];
    t[0] = OP_CLOCK;
    t[1] = 'M';
    dwWrite(t, 2);
    fnClock = dwRead(t, 8);
}

/* The server's clock in 1/100 s, 0-59999 (ten minutes round), NOCLOCK if
 * there is none. Without fnClock only whole seconds (DriveWire OP_TIME). */
word clockNow(void)
{
    byte t[8];
    if (!dwPresent)
        return NOCLOCK;
    t[0] = OP_CLOCK;
    t[1] = 'M';
    t[7] = 0;
    if (fnClock)
        dwWrite(t, 2);
    else
    {
        t[0] = OP_TIME;
        dwWrite(t, 1);
    }
    if (!dwGet(t, fnClock ? 8 : 6))
        return NOCLOCK;
    if (!fnClock)                       /* YY MM DD HH MM SS: as C YY MM DD HH MM SS */
    {
        t[6] = t[5];
        t[5] = t[4];
    }
    return (word) (t[5] % 10) * 6000 + (word) t[6] * 100 + t[7];
}

/* ------------------------------------------------------------------ network */

/* A command to the FujiNet network device N1: with its two parameter bytes
 * (a length for READ and GET_RESPONSE, unused otherwise). */
void netCmd(byte cmd, word aux)
{
    byte c[5];
    c[0] = OP_NET;
    c[1] = NET_UNIT;
    c[2] = cmd;
    c[3] = (byte) (aux >> 8);
    c[4] = (byte) aux;
    dwWrite(c, 5);
}

/* The reply data of the last command (padded with zeros to n). */
byte netResponse(byte *p, word n)
{
    netCmd(0x01, n);
    return dwGet(p, n);
}

/* NS_OK if the last command worked. */
byte netError(void)
{
    byte e;
    netCmd(0x02, 0);
    return dwGet(&e, 1) ? e : 0;
}

/* Opens DATA->url + name: mode 4 read, 6 directory (trans 0x80: long
 * listing, one "NAME   SIZE" line per entry). */
byte netOpen(const char *name, byte mode, byte trans)
{
    byte *c = DATA->cmd;
    memset(c, 0, sizeof(DATA->cmd));
    c[0] = OP_NET;
    c[1] = NET_UNIT;
    c[2] = 'O';
    c[3] = mode;
    c[4] = trans;
    strcpy((char *) c + 5, "N1:");
    strcat((char *) c + 5, DATA->url);
    strcat((char *) c + 5, name);
    dwWrite(c, sizeof(DATA->cmd));
    return netError() == NS_OK;
}

void netClose(void)
{
    netCmd('C', 0);
    netError();
}

/* Waits for data on the open channel. Returns how much to read (at most
 * max), 0 at the end, 0xFFFF on an error, a 10 s stall or BREAK. */
word netAvail(word max)
{
    struct { word avail; byte conn, err; } s;
    word t0 = TIMER;
    for (;;)
    {
        netCmd('S', 0);
        if (!netResponse((byte *) &s, 4))
            return 0xFFFF;
        if (s.avail)
            return s.avail < max ? s.avail : max;
        if (s.err == NS_EOF || (s.err == NS_OK && !s.conn))
            return 0;
        if (s.err != NS_OK || TIMER - t0 > 600 || (stopped = breakPressed()))
            return 0xFFFF;
    }
}

/* Reads the open channel on into pagePool pages. */
const char *loadNet(void)
{
    word n, left = 0;
    byte ok;
    const char *err = 0;

    fileSize = 0;
    for (;;)
    {
        if (left == 0)                  /* ask only when what was waiting is read */
        {
            left = netAvail(0xFFFE);
            if (left == 0)
                break;
            if (left == 0xFFFF)
            {
                err = "NETWORK ERROR";
                break;
            }
        }
        n = left < 8192 - loadOff ? left : (word) (8192 - loadOff);
        if (n > dwBlock)
            n = dwBlock;
        left -= n;
        if (loadPage >= poolPages)
        {
            err = "FILE TOO BIG FOR RAM";
            break;
        }
        netCmd('R', n);
        MMU_SLOT3 = pagePool[loadPage];
        ok = netResponse(WINDOW + loadOff, n);
        MMU_SLOT3 = BASIC_SLOT3;
        if (!ok)
        {
            err = "NETWORK READ ERROR";
            break;
        }
        fileSize += n;
        loadOff += n;
        if (loadOff == 8192)
        {
            loadOff = 0;
            ++loadPage;
            putchar('.');
        }
    }
    netClose();
    return err;
}

/* Index just past "SCHEME://HOST/" in DATA->url. */
byte urlRoot(void)
{
    char *u = DATA->url;
    char *p = strchr(u, ':');
    if (!p || p[1] != '/' || p[2] != '/')
        return 0;
    p = strchr(p + 3, '/');
    return p ? (byte) (p - u) + 1 : 0;
}

/* Back up one folder. */
void urlUp(void)
{
    char *u = DATA->url;
    byte n = (byte) strlen(u);
    if (n <= urlRoot())
        return;
    u[--n] = 0;                         /* trailing '/' */
    while (n && u[n - 1] != '/')
        u[--n] = 0;
}

byte hasExt(const char *name, const char *ext)
{
    byte n = (byte) strlen(name);
    return n > 4 && stricmp(name + n - 4, ext) == 0;
}

/* Adds a listed name in order (folders first) if it is a folder or an
 * FLI/FLC file. Skips ., .., hidden files and links to other places. */
void addNetEntry(const char *name, u32 size)
{
    byte n = (byte) strlen(name), kind, i, k;
    struct Entry *e;

    if (n == 0 || n >= NAMELEN || numEntries >= MAXENT || name[0] == '.'
        || name[0] == '/' || name[0] == '?' || strchr(name, ':'))
        return;
    if (name[n - 1] == '/')
        kind = KIND_DIR;
    else if (hasExt(name, ".FLI") || hasExt(name, ".FLC"))
        kind = KIND_FILE;
    else
        return;
    for (i = 0; i < numEntries; ++i)
    {
        k = entries[i].kind;
        if (k == KIND_UP || (k == KIND_DIR && kind == KIND_FILE))
            continue;
        if (k == KIND_FILE && kind == KIND_DIR)
            break;
        if (stricmp(name, entries[i].name) < 0)
            break;
    }
    e = &entries[i];
    memmove(e + 1, e, (numEntries - i) * sizeof(struct Entry));
    ++numEntries;
    strcpy(e->name, name);
    e->kind = kind;
    e->size = size;
}

/* A line of the long listing: the name, spaces, then the size: "123",
 * "45K" or "1.2M". */
void parseDirLine(char *s, byte n)
{
    byte i;
    word k = 0;
    while (n && s[n - 1] == ' ')
        --n;
    s[n] = 0;
    i = n;
    while (i && s[i - 1] != ' ')
        --i;
    if (i == 0)
        return;
    for (n = i; isdigit(s[n]); ++n)
        k = k * 10 + (s[n] - '0');
    if (s[n] == '.')                    /* "1.2M": tenths of M */
        k = (k * 10 + (s[n + 1] - '0')) * 103;
    else if (s[n] == 'M')
        k <<= 10;
    else if (s[n] != 'K')
        k = k != 0;                     /* bytes: show 1K */
    while (i && s[i - 1] == ' ')
        --i;
    s[i] = 0;
    addNetEntry(s, (u32) k << 10);
}

/* Reads the listing of DATA->url. A folder the server can't list (an HTTP
 * server without WebDAV) is read as a web page instead, taking the
 * href="..." links from it. Returns an error string or 0. */
const char *readNetDir(void)
{
    static const char href[] = "href=\"";
    byte html = 0, st = 0, len = 0, c;
    char *line = DATA->line;
    word n, i;
    const char *err = 0;

    numEntries = 0;
    if (!dwPresent)
        return dwMsg;
    if (strlen(DATA->url) > urlRoot())
    {
        strcpy(entries[0].name, "..");
        entries[0].kind = KIND_UP;
        numEntries = 1;
    }
    if (!netOpen("", 6, 0x80))
    {
        if (!netOpen("", 4, 0))
            return "CAN'T OPEN FOLDER";
        html = 1;
    }
    for (;;)
    {
        n = netAvail(dwBlock < sizeof(DATA->buf) ? dwBlock : sizeof(DATA->buf));
        if (n == 0)
            break;
        if (n != 0xFFFF)
            netCmd('R', n);
        if (n == 0xFFFF || !netResponse(DATA->buf, n))
        {
            err = "NETWORK ERROR";
            break;
        }
        for (i = 0; i < n; ++i)
        {
            c = DATA->buf[i];
            if (html)
            {
                if (st < 6)
                    st = (c | 0x20) == href[st] ? st + 1 : (c | 0x20) == 'h';
                else if (c == '"')
                {
                    line[len] = 0;
                    addNetEntry(line, 0);
                    st = len = 0;
                }
                else if (len < sizeof(DATA->line) - 1)
                    line[len++] = c;
            }
            else if (c == '\r' || c == '\n')
            {
                if (len)
                    parseDirLine(line, len);
                len = 0;
            }
            else if (len < sizeof(DATA->line) - 1)
                line[len++] = c;
        }
    }
    if (len && !html)
        parseDirLine(line, len);
    netClose();
    return err;
}

/* ------------------------------------------------------------------ loading */

u32 statBytes;              /* last load, for the browser */
word statTicks;
byte statMin;               /* statTicks is only a lower bound */

/* Loads cur into pagePool pages and times it. BASIC's TIMER misses ticks
 * while a DriveWire or floppy transfer has interrupts off, so the server's
 * clock is used when there is one: the FujiNet's to 1/100 s, or whole
 * seconds from another DriveWire server when they show a longer time.
 * Returns an error string or 0. */
const char *loadFile(void)
{
    const char *err;
    word t0 = TIMER, t, c0 = clockNow(), c1;

    loadPage = 0;
    loadOff = 0;
    if (cur.kind == KIND_DISK)
        err = loadDisk(&cur);
    else
        err = netOpen(cur.name, 4, 0) ? loadNet() : "CAN'T OPEN FILE";
    t = TIMER - t0;
    if (err)
        return err;
    statMin = 1;
    if (c0 != NOCLOCK && (c1 = clockNow()) != NOCLOCK)
    {
        c1 = c1 >= c0 ? c1 - c0 : c1 + (60000 - c0);     /* 1/100 s */
        c1 = c1 / 5 * 3;                                /* ticks */
        if (fnClock || c1 > t + 60)
            t = c1;
        statMin = 0;
    }
    statBytes = fileSize;
    statTicks = t ? t : (word) 1;

    for (t = 0; t < 66; ++t)
        flc_pages[t] = pagePool[t < poolPages ? t : (word) poolPages - 1];
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

/* cube index -> nearest composite code (coco16.py cmp_table()) */
const byte cmpTab[64] = {
     0, 12, 12, 27,  2, 14, 13, 28,  2, 15, 30, 29, 17, 17, 31, 46,
     6,  9, 10, 27,  4, 32, 27, 27,  3, 17, 30, 44, 18, 17, 47, 46,
     7,  8, 25, 26,  5, 22, 25, 42, 20, 20, 32, 43, 19, 34, 33, 61,
     7, 23, 24, 41, 22, 22, 39, 41, 21, 37, 38, 56, 36, 36, 51, 48
};

/* cube index -> palette register value for the monitor in use */
byte palColor(byte q)
{
    return cmpMode ? cmpTab[q] : gimeColor(q);
}

/* Guess the monitor from the palette BASIC left: its RGB command loads
 * 18 54 9 36..., the power-on CMP palette is 18 36 11 7... */
void detectMonitor(void)
{
    static const byte rgbPal[8] = { 18, 54, 9, 36, 63, 27, 45, 38 };
    static const byte cmpPal[8] = { 18, 36, 11, 7, 63, 31, 9, 38 };
    byte i, r = 1, c = 1;
    for (i = 0; i < 8; ++i)
    {
        byte v = * (byte *) (0xFFB0 + i) & 0x3F;
        if (v != rgbPal[i]) r = 0;
        if (v != cmpPal[i]) c = 0;
    }
    cmpMode = c && !r;
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

/* Map FLC palette entries first..last to the nearest chosen colour. */
void buildTables(word first, word last)
{
    word c;
    byte k, best, r, g, b;
    word d, bd;
    byte *p = flc_pal + first * 3;

    for (c = first; c <= last; ++c, p += 3)
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

/* PACK4 files: palette entries 0-15 are the screen colours. */
void loadPalette(void)
{
    byte i;
    byte *p = flc_pal;
    for (i = 0; i < 16; ++i, p += 3)
        * (byte *) (0xFFB0 + i) = cmpMode && hasCmp ? hdrCmp[i]
            : palColor((level(p[0]) << 4) | (level(p[1]) << 2) | level(p[2]));
}

void graphicsOn(void)
{
    byte i;
    if (!packed)
        for (i = 0; i < 16; ++i)
            * (byte *) (0xFFB0 + i) = palColor(chosen[i]);
    * (byte *) 0xFF90 = 0x4C;           /* CoCo 3 mode, MMU on, $FExx constant */
    * (byte *) 0xFF98 = 0x80;           /* graphics */
    * (byte *) 0xFF99 = 0x3E;           /* 200 lines, 160 bytes/row, 16 colours */
    * (byte *) 0xFF9C = 0;
    * (byte *) 0xFF9D = SCR_PAGE << 2;  /* physical $60000 / 8 */
    * (byte *) 0xFF9E = 0;
    * (byte *) 0xFF9F = 0;
    * (byte *) 0xFF9A = packed ? 0 : palColor(chosen[LOTBL[0]]);
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

word kioskSecs;             /* play() stops after this long; 0: until BREAK */

/* Loads and plays cur. Returns an error string or 0. */
const char *play(void)
{
    byte hdr[128];
    word magic, frames, fi, x0, y0, secs = 0, el = 0;
    byte delay, start, i, last;
    u32 oframe1, oframe2, off, next, ms;
    const char *err;
#ifdef AUTOEXIT
    byte loops = 0;
#endif

    stopped = 0;
    printf("LOADING %s", cur.name);
    err = loadFile();
    if (err)
        return err;
    putchar('\n');

    peek(0, hdr, 128);
    magic = rd16(hdr + 4);
    if (magic != 0xAF11 && magic != 0xAF12)
        return "NOT AN FLI/FLC FILE";
    frames = rd16(hdr + 6);
    flc_w = rd16(hdr + 8);
    flc_h = rd16(hdr + 10);
    if (flc_w > 320 || flc_h > 200 || flc_w == 0 || flc_h == 0)
        return "SIZE OVER 320X200 (FOR NOW)";
    packed = rd16(hdr + 12) == 4;
    if (packed && (flc_w != 320 || flc_h != 200))
        return "PACKED FILE NOT 320X200";
    if (!packed && rd16(hdr + 12) != 8 && rd16(hdr + 12) != 0)
        return "NOT 8 BITS PER PIXEL";
    hasCmp = packed && memcmp(hdr + 100, "CMP", 3) == 0;
    memcpy(hdrCmp, hdr + 103, 16);

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
#ifdef NODELAY
    delay = 0;                          /* test builds: measure raw speed */
#endif

    x0 = ((320 - flc_w) / 2) & ~1;
    y0 = (200 - flc_h) / 2;
    flc_scr_base = y0 * 160 + x0 / 2;

    for (i = 0; i < 16; ++i)
        savedPalette[i] = * (byte *) (0xFFB0 + i) & 0x3F;
    memset(flc_pal, 0, 768);
    irq_install();
    if (packed)
    {
        /* PACK4: the first frame is a delta from a black screen and brings
         * its own palette, so just draw it. */
        printf("%u FRAMES, PACKED\n", frames);
        flc_clear(0);
        next = doFrame(oframe1);
        loadPalette();
    }
    else
    {
        printf("%ux%u, %u FRAMES\nANALYZING COLOURS...\n", flc_w, flc_h, frames);
        /* First frame in histogram mode: pick 16 colours from what is used. */
        memset(flc_hist, 0, 512);
        flc_hist_mode = 1;
        next = doFrame(oframe1);
        flc_hist_mode = 0;
        if (next)
        {
            chooseColors();
            buildTables(0, 255);
            flc_clear(HITBL[0x80] | LOTBL[0]);
        }
    }
    if (next == 0)
    {
        irq_remove();
        return "BAD FIRST FRAME";
    }
    if (oframe2 == 0)
        oframe2 = next;
    flc_pal_dirty = 0;
    flc_pal_lo = 255;
    flc_pal_hi = 0;
    graphicsOn();

    off = oframe1;
    fi = 0;
    last = flc_ticks;
    for (;;)
    {
        start = flc_ticks;
        next = doFrame(off);
        if (flc_pal_dirty)                  /* remap only what changed */
        {
            if (packed)
                loadPalette();
            else
                buildTables(flc_pal_lo, flc_pal_hi);
            flc_pal_dirty = 0;
            flc_pal_lo = 255;
            flc_pal_hi = 0;
        }
        ++fi;
        if (next == 0 || fi > frames)       /* error, or ring frame done */
        {
            off = (next == 0) ? oframe1 : oframe2;
            fi = (next == 0) ? 0 : 1;
            if (next == 0 && packed)
                flc_clear(0);               /* frame 1 is a delta from black */
        }
        else if (fi == frames && next + 16 > fileSize)
        {
            off = oframe1;                  /* no ring frame: restart */
            fi = 0;
            if (packed)
                flc_clear(0);
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
        {
            stopped = 1;
            break;
        }
        if (kioskSecs)
        {
            el += (byte) (flc_ticks - last);
            last = flc_ticks;
            while (el >= 60)
            {
                el -= 60;
                ++secs;
            }
            if (secs >= kioskSecs)
                break;
        }
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

#ifdef AUTOKEYS
#define KEYSTR(k)   #k
#define KEYSTR2(k)  KEYSTR(k)
/* test builds: keys typed before the keyboard (see the Makefile) */
const char *autoKeys = KEYSTR2(AUTOKEYS);
#endif

byte getKey(void)
{
#ifdef AUTOKEYS
    static const char codes[] = "D\nU^E\rL\b";
    char k = *autoKeys, *c;
    if (k)
    {
        ++autoKeys;
        c = strchr(codes, k);
        return c ? (byte) c[1] : (byte) (k & 0xDF);
    }
#endif
    return waitkey(TRUE);
}

const char *listErr;        /* why the list couldn't be read, or 0 */

/* Reads the current drive or network folder. */
void readList(void)
{
    if (netMode)
        listErr = readNetDir();
    else
        listErr = readDirectory() ? (const char *) 0 : "CAN'T READ THE DISK";
}

void drawList(byte sel, byte top)
{
    byte r, i, n;
    char name[25];
    struct Entry *e;
    word r10;

    cls(255);
    printf("FLCPLAY 0.6 %s ", flc_6309 ? "6309" : "6809");
    if (netMode)
        printf("NETWORK");
    else
        printf("DRIVE %u%s", drive, dsMode ? " DS" : "");
    printf(" %s\n", cmpMode ? "CMP" : "RGB");
    n = (byte) strlen(DATA->url);
    printf("%s", netMode ? (const char *) DATA->url + (n > 32 ? n - 32 : 0)
                         : "--------------------------------");
    if (numEntries == 0 && !listErr)
    {
        locate(0, 2);
        printf(" NO .FLC/.FLI FILES");
    }
    for (r = 0; r < LIST_ROWS; ++r)
    {
        i = top + r;
        if (i >= numEntries)
            break;
        e = &entries[i];
        locate(0, 2 + r);
        strncpy(name, e->name, 24);
        name[24] = 0;
        printf("%c%-24s", i == sel ? '>' : ' ', name);
        if (e->kind == KIND_DISK || (e->kind == KIND_FILE && e->size))
            printf(" %5uK", (word) ((e->size + 1023) >> 10));
    }
    locate(0, 13);
    if (listErr)
        printf("%s", listErr);
    else if (statBytes)
    {
        r10 = (word) (statBytes * 75 / 128 / statTicks);       /* tenths of K/s */
        printf("LOADED %uK %s%u.%uS %s%u.%uK/S", (word) ((statBytes + 1023) >> 10),
               statMin ? ">" : "", statTicks / 60, statTicks % 60 / 6,
               statMin ? "<" : "", r10 / 10, r10 % 10);
    }
    locate(0, 14);
    if (netMode)
        printf("ENTER:OPEN LEFT:UP U:URL 0-%u:DRV", dsMode ? 1 : 3);
    else
        printf("ENTER:PLAY 0-%u:DRV S:SIDES N:NET", dsMode ? 1 : 3);
    locate(0, 15);
    printf("C:TV K:KIOSK T:%uMIN R:%s", confMinutes, confRepeat ? "LOOP" : "ONCE");
}

/* Edits DATA->url. FALSE if BREAK or left empty. */
byte editUrl(void)
{
    char *u = DATA->url;
    byte n = (byte) strlen(u), k;
    for (;;)
    {
        cls(255);
        printf("FOLDER: TNFS:// OR HTTP://HOST/PATH\nSHIFT-0 LOWER CASE, SHIFT-LEFT\nCLEARS, BREAK CANCELS\n\n%s", u);
        k = getKey();
        if (k == 13)
            break;
        if (k == 3)
            return FALSE;
        if (k == 8)
        {
            if (n)
                u[--n] = 0;
        }
        else if (k == 21)
            u[n = 0] = 0;
        else if (k >= ' ' && k < 127 && n < URLLEN - 2)
        {
            u[n++] = k;
            u[n] = 0;
        }
    }
    if (n && u[n - 1] != '/')
    {
        u[n++] = '/';
        u[n] = 0;
    }
    return n != 0;
}

/* Plays each file in the list for confMinutes, then the next; after the
 * last, stops or (confRepeat) starts over. BREAK stops it. */
byte isFile(byte i)
{
    return entries[i].kind == KIND_DISK || entries[i].kind == KIND_FILE;
}

void kiosk(void)
{
    byte i, n, k, played;
    word t0;
    const char *err;

    kioskSecs = (word) confMinutes * 60;
    for (i = n = 0; i < numEntries; ++i)
        n += isFile(i);
    do
    {
        played = k = 0;
        for (i = 0; i < numEntries; ++i)
        {
            if (!isFile(i))
                continue;
            memcpy(&cur, &entries[i], sizeof(cur));
            cls(255);
            printf("KIOSK %u/%u, BREAK STOPS\n", ++k, n);
            err = play();
            if (stopped)
                goto done;
            if (err)
            {
                printf("\n%s\n", err);
                for (t0 = TIMER; TIMER - t0 < 180; )
                    if (breakPressed())
                        goto done;
            }
            else
                played = 1;
        }
    } while (confRepeat && played);
done:
    kioskSecs = 0;
    while (breakPressed())
        ;
}

void appMain(void)
{
    static const byte minutes[] = { 1, 2, 3, 5, 10, 15, 30, 60, 0 };
    byte sel = 0, top = 0, key, i;
    struct Entry *e;
#ifdef AUTOPLAY
    byte autoplayed = 0;
#endif
    const char *err;

    initMemory();
    findDriveTable();
    findDriveWire();
    if (dwPresent)
        findClock();
    detectMonitor();
    if (dsConfig[6])
        setDsMode(1);
    drive = 0;
    strcpy(DATA->url, confUrl);
    netMode = DATA->url[0] != 0;
    readList();
    if (confKiosk && !listErr)
        kiosk();

    for (;;)
    {
        drawList(sel, top);

#ifdef AUTOPLAY
        /* test builds: play entry AUTOPLAY-1 once without a keypress */
        if (sel == 0 && !listErr && numEntries >= AUTOPLAY && !autoplayed)
        {
            autoplayed = 1;
            sel = AUTOPLAY - 1;
            key = 13;
        }
        else
#endif
        key = getKey();
        if (key >= 'a' && key <= 'z')
            key -= 32;
        if (key >= '0' && key <= (dsMode ? '1' : '3'))
        {
            drive = key - '0';
            netMode = 0;
        }
        else if (key == 'S' && driveTable && !netMode)
        {
            setDsMode(!dsMode);
            drive &= dsMode ? 1 : 3;
        }
        else if (key == 'N' || key == 'U')
        {
            if ((key == 'N' && DATA->url[0]) || editUrl())
                netMode = 1;
            else
                continue;
        }
        else if (key == 8 && netMode)           /* left arrow */
            urlUp();
        else if (key == 13 && numEntries)
        {
            e = &entries[sel];
            if (e->kind == KIND_UP)
                urlUp();
            else if (e->kind == KIND_DIR)
            {
                if (strlen(DATA->url) + strlen(e->name) < URLLEN)
                    strcat(DATA->url, e->name);
            }
            else
            {
                memcpy(&cur, e, sizeof(cur));
                cls(255);
                err = play();
                if (err)
                {
                    printf("\n%s\n\nPRESS ANY KEY", err);
                    getKey();
                }
                continue;
            }
        }
        else
        {
            if (key == 'C')
                cmpMode = !cmpMode;
            else if (key == 'T')
            {
                for (i = 0; minutes[i] && minutes[i] <= confMinutes; ++i)
                    ;
                confMinutes = minutes[i] ? minutes[i] : minutes[0];
            }
            else if (key == 'R')
                confRepeat = !confRepeat;
            else if (key == 'K')
                kiosk();
            else if (key == 94 && sel > 0)      /* up arrow */
            {
                if (--sel < top)
                    top = sel;
            }
            else if (key == 10 && sel + 1 < numEntries) /* down arrow */
            {
                if (++sel >= top + LIST_ROWS)
                    top = sel - LIST_ROWS + 1;
            }
            continue;
        }
        sel = top = 0;                          /* somewhere else: read it */
        cls(255);
        printf("READING...");
        readList();
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
    setHighSpeed(TRUE);                 /* POKE 65497: 1.79 MHz */
    flc_6309 = cpu_is_6309();
    flc_init();
    width(32);
    for (i = 0; i < 64; ++i)
        sq[i] = (word) i * i;
    call_with_stack(appMain, STACK_TOP);
    return 0;
}
