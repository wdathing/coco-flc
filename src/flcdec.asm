; flcdec.asm - FLI/FLC chunk decoders for the CoCo 3 (GIME 320x200x16)
;
; Decodes 8-bit FLC pixels straight into a 4bpp GIME screen through two
; 256-byte nibble tables built by the C side:
;   LOTBL ($0E00): LOTBL[c]       = 4-bit colour for FLC index c   (DP-indexed)
;   HITBL ($0F00): HITBL[c^$80]   = 4-bit colour << 4             (Y=$0F80, signed A/B offset)
;
; While a chunk is decoded the CPU map is:
;   $8000-$BFFF  screen window: the two screen pages holding the current row
;                (re-banked at the start of every row; a row is 160 bytes)
;   $C000-$FFFF  two consecutive pages of the loaded file (source window)
; The source window slides forward 8K whenever U passes $E000 at a packet
; boundary. Packets are tiny next to the 7.5K left before the $FExx page,
; so a packet never runs off the end. Slots 0-3 (program, stack, tables,
; IRQ handler) are never touched.
;
; Register use inside decoders:  U=source  X=screen byte  Y=$0F80  DP=$0E
; podd: 1 when the next pixel goes in the low nibble of ,X

LOTBL   equ     $0E00
HITBL   equ     $0F00
HIMID   equ     $0F80
SRCSLD  equ     $E000
SCRWIN  equ     $8000

        SECTION code

_flc_chunk      EXPORT
_flc_clear      EXPORT
_irq_install    EXPORT
_irq_remove     EXPORT
_call_with_stack EXPORT
_flc_pages      EXPORT
_flc_scr_pages  EXPORT
_flc_src_idx    EXPORT
_flc_src_ptr    EXPORT
_flc_scr_base   EXPORT
_flc_w          EXPORT
_flc_h          EXPORT
_flc_hist_mode  EXPORT
_flc_pal_dirty  EXPORT
_flc_ticks      EXPORT
_flc_pal        EXPORT
_flc_hist       EXPORT

;------------------------------------------------------------------------------
; void flc_chunk(byte type)
; Decodes one chunk whose data starts at pages[flc_src_idx]:flc_src_ptr.
_flc_chunk
        pshs    u,y,dp,cc
        orcc    #$40                    ; no FIRQ while ROM is unmapped
        ldd     $FFA4                   ; save MMU slots 4-7
        std     mmusv
        ldd     $FFA6
        std     mmusv+2
        ldx     #_flc_pages
        ldb     _flc_src_idx
        abx
        ldd     ,x
        std     $FFA6
        lda     #LOTBL/256
        tfr     a,dp
        ldy     #HIMID
        ldu     _flc_src_ptr
        ldb     9,s                     ; type (low byte of int arg)
        cmpb    #15
        bne     @n1
        lbsr    brun
        bra     @exit
@n1     cmpb    #7
        bne     @n2
        lbsr    ss2
        bra     @exit
@n2     cmpb    #12
        bne     @n3
        lbsr    lc
        bra     @exit
@n3     cmpb    #16
        bne     @n4
        lbsr    copy
        bra     @exit
@n4     cmpb    #13
        bne     @n5
        lbsr    black
        bra     @exit
@n5     cmpb    #4
        bne     @n6
        clr     colsh
        lbsr    color
        bra     @exit
@n6     cmpb    #11
        bne     @exit
        lda     #1
        sta     colsh
        lbsr    color
@exit   stu     _flc_src_ptr
        ldd     mmusv
        std     $FFA4
        ldd     mmusv+2
        std     $FFA6
        puls    cc,dp,y,u,pc

;------------------------------------------------------------------------------
; Source window slide: next 8K page becomes the low half of the window.
slide   pshs    d,x
        inc     _flc_src_idx
        ldx     #_flc_pages
        ldb     _flc_src_idx
        abx
        ldd     ,x
        std     $FFA6
        leau    -$2000,u
        puls    d,x,pc

; Bank in row cury; X = its first byte, podd = 0
setrow  lda     cury+1
        ldb     #160
        mul
        addd    _flc_scr_base           ; offset into the 32000-byte screen
        std     rowoff
        lsra                            ; page = offset >> 13
        lsra
        lsra
        lsra
        lsra
        ldx     #_flc_scr_pages
        ldd     a,x
        std     $FFA4
        ldd     rowoff
        anda    #$1F
        adda    #SCRWIN/256
        tfr     d,x
        clr     podd
        rts

; Advance B pixels (0..255)
skip    clra
        addb    podd
        adca    #0
        lsra
        rorb
        leax    d,x                     ; leax leaves C alone
        lda     #0
        rola
        sta     podd
        rts

;------------------------------------------------------------------------------
; lit: copy D pixels from U
lit     tst     _flc_hist_mode
        lbne    hlit
        std     cnt
        beq     @ret
        tst     podd
        beq     @even
        lda     ,x                      ; finish the half-written byte
        anda    #$F0
        ldb     ,u+
        stb     @m1+1
@m1     ora     <0
        sta     ,x+
        clr     podd
        ldd     cnt
        subd    #1
@even   lsra
        rorb
        pshs    cc                      ; C = one pixel left over
        pshs    x
        addd    ,s++
        std     @cmp+1                  ; end address of the byte loop
        cmpx    @cmp+1
        beq     @tail
@lp     ldd     ,u++
        stb     @m2+1
        lda     a,y
@m2     ora     <0
        sta     ,x+
@cmp    cmpx    #0
        bne     @lp
@tail   puls    cc
        bcc     @ret
        ldb     ,u+                     ; last pixel -> high nibble
        lda     b,y
        sta     @m3+1
        lda     ,x
        anda    #$0F
@m3     ora     #0
        sta     ,x
        inc     podd
@ret    rts

; fill: cnt pixels alternating A,B (A first)
fill    tst     _flc_hist_mode
        lbne    hfill
        std     fc
        ldd     cnt
        beq     @ret
        tst     podd
        beq     @even
        ldb     fc
        stb     @m1+1
        lda     ,x
        anda    #$F0
@m1     ora     <0
        sta     ,x+
        clr     podd
        ldd     fc                      ; pattern is now B,A
        exg     a,b
        std     fc
        ldd     cnt
        subd    #1
        std     cnt
@even   ldb     fc+1
        stb     @m2+1
        lda     fc
        lda     a,y
@m2     ora     <0
        sta     fv
        ldd     cnt
        lsra
        rorb
        pshs    cc
        pshs    x
        addd    ,s++
        std     @cmp+1
        cmpx    @cmp+1
        beq     @tail
        lda     fv
@lp     sta     ,x+
@cmp    cmpx    #0
        bne     @lp
@tail   puls    cc
        bcc     @ret
        lda     fc                      ; last pixel -> high nibble
        lda     a,y
        sta     @m3+1
        lda     ,x
        anda    #$0F
@m3     ora     #0
        sta     ,x
        inc     podd
@ret    rts

; histogram versions (first frame analysis; nothing is drawn)
hlit    std     cnt
        beq     @ret
@lp     ldb     ,u+
        clra
        lslb
        rola
        addd    #_flc_hist
        tfr     d,x
        ldd     ,x
        addd    #1
        std     ,x
        ldd     cnt
        subd    #1
        std     cnt
        bne     @lp
@ret    rts

hfill   std     fc
        ldd     cnt
        addd    #1
        lsra
        rorb
        ldx     #fc
        bsr     @add                    ; (cnt+1)/2 of colour A
        ldd     cnt
        lsra
        rorb
        leax    1,x                     ; cnt/2 of colour B
@add    pshs    d,x
        ldb     ,x
        clra
        lslb
        rola
        addd    #_flc_hist
        tfr     d,x
        ldd     ,x
        addd    ,s
        std     ,x
        puls    d,x,pc

;------------------------------------------------------------------------------
; BYTE_RUN (15): full frame, run-length encoded
brun    clr     cury
        clr     cury+1
@line   ldd     cury
        cmpd    _flc_h
        bhs     @done
        lbsr    setrow
        leau    1,u                     ; packet count is unreliable; use width
        ldd     _flc_w
        std     xrem
@pk     ldd     xrem
        ble     @next
        cmpu    #SRCSLD
        blo     @ok
        lbsr    slide
@ok     ldb     ,u+
        bmi     @lit
        beq     @next                   ; malformed; give up on this row
        clra
        std     cnt
        ldd     xrem
        subd    cnt
        std     xrem
        lda     ,u
        ldb     ,u+
        lbsr    fill
        bra     @pk
@lit    negb
        clra
        std     cnt
        ldd     xrem
        subd    cnt
        std     xrem
        ldd     cnt
        lbsr    lit
        bra     @pk
@next   ldd     cury
        addd    #1
        std     cury
        bra     @line
@done   rts

; FLI_COPY (16): raw pixels
copy    clr     cury
        clr     cury+1
@line   ldd     cury
        cmpd    _flc_h
        bhs     @done
        lbsr    setrow
        cmpu    #SRCSLD
        blo     @ok
        lbsr    slide
@ok     ldd     _flc_w
        lbsr    lit
        ldd     cury
        addd    #1
        std     cury
        bra     @line
@done   rts

; BLACK (13): colour 0 everywhere
black   clr     cury
        clr     cury+1
@line   ldd     cury
        cmpd    _flc_h
        bhs     @done
        lbsr    setrow
        ldd     _flc_w
        std     cnt
        clra
        clrb
        lbsr    fill
        ldd     cury
        addd    #1
        std     cury
        bra     @line
@done   rts

; DELTA_FLI (12): byte-oriented line deltas
lc      ldb     ,u+
        lda     ,u+
        std     cury
        ldb     ,u+
        lda     ,u+
        std     nlines
@line   ldd     nlines
        beq     @done
        subd    #1
        std     nlines
        ldd     cury
        cmpd    _flc_h
        bhs     @done
        lbsr    setrow
        lda     ,u+
        sta     npk
@pk     lda     npk
        beq     @next
        deca
        sta     npk
        cmpu    #SRCSLD
        blo     @ok
        lbsr    slide
@ok     ldb     ,u+
        lbsr    skip
        ldb     ,u+
        bmi     @fill
        clra
        lbsr    lit
        bra     @pk
@fill   negb
        clra
        std     cnt
        lda     ,u
        ldb     ,u+
        lbsr    fill
        bra     @pk
@next   ldd     cury
        addd    #1
        std     cury
        bra     @line
@done   rts

; DELTA_FLC (7): word-oriented line deltas
; Rows normally stay word aligned (even skips, even x0), so each row starts
; in a fast loop that writes whole bytes. An odd skip, a huge packet count
; or histogram mode drops the rest of the row to the general routines.
ss2     ldb     ,u+
        lda     ,u+
        std     nlines
        clr     cury
        clr     cury+1
@line   ldd     nlines
        lbeq    @done
        subd    #1
        std     nlines
        clr     lastf
@op     cmpu    #SRCSLD
        blo     @ok
        lbsr    slide
@ok     ldb     ,u+
        lda     ,u+
        cmpa    #$C0
        lbhs    @skipl
        bita    #$80
        lbne    @last
        std     npk16
        ldd     cury
        cmpd    _flc_h
        lbhs    @done
        lbsr    setrow
        tst     _flc_hist_mode
        bne     @pk
        lda     npk16
        bne     @pk
        lda     npk16+1
        lbeq    @eol
        sta     npk
; fast loop: podd stays 0
@fpk    cmpu    #SRCSLD
        blo     @fok
        lbsr    slide
@fok    ldb     ,u+                     ; skip (pixels)
        lsrb
        bcs     @odd
        abx
        ldb     ,u+
        bmi     @fwf
        beq     @fnx
        pshs    b                       ; literal words -> bytes
@fwl    ldd     ,u++
        stb     @fm1+1
        lda     a,y
@fm1    ora     <0
        sta     ,x+
        dec     ,s
        bne     @fwl
        leas    1,s
        dec     npk
        bne     @fpk
        bra     @eol
@fwf    negb                            ; repeated word -> one byte value
        stb     fv
        ldd     ,u++
        stb     @fm2+1
        lda     a,y
@fm2    ora     <0
        ldb     fv
@fwfl   sta     ,x+
        decb
        bne     @fwfl
@fnx    dec     npk
        bne     @fpk
        bra     @eol
@odd    lslb                            ; restore the odd skip
        incb
        lbsr    skip
        clra
        ldb     npk
        subd    #1
        std     npk16                   ; packets left after this one
        bra     @pk2
; general loop
@pk     ldd     npk16
        beq     @eol
        subd    #1
        std     npk16
        cmpu    #SRCSLD
        blo     @ok2
        lbsr    slide
@ok2    ldb     ,u+
        lbsr    skip
@pk2    ldb     ,u+
        bmi     @wfill
        beq     @pk
        clra                            ; literal words
        lslb
        rola
        lbsr    lit
        bra     @pk
@wfill  negb
        clra
        lslb
        rola
        std     cnt
        lda     ,u+
        ldb     ,u+
        lbsr    fill
        bra     @pk
@skipl  pshs    d                       ; D = -lines to skip
        ldd     cury
        subd    ,s++
        std     cury
        lbra    @op
@last   stb     lastv
        inc     lastf
        lbra    @op
@eol    tst     lastf
        beq     @nl
        lbsr    setrow                  ; odd width: last pixel of the row
        ldd     _flc_w
        subd    #1
        lsra
        rorb
        leax    d,x
        lda     #0
        rola
        sta     podd
        ldd     #1
        std     cnt
        lda     lastv
        tfr     a,b
        lbsr    fill
@nl     ldd     cury
        addd    #1
        std     cury
        lbra    @line
@done   rts

; COLOR_256 (4) / COLOR_64 (11): update flc_pal
color   ldb     ,u+
        lda     ,u+
        std     npk16
        clr     cidx
@pk     ldd     npk16
        beq     @done
        subd    #1
        std     npk16
        cmpu    #SRCSLD
        blo     @ok
        lbsr    slide
@ok     lda     cidx
        adda    ,u+
        sta     cidx
        ldb     ,u+
        stb     ccount
        lda     cidx
        ldb     #3
        mul
        addd    #_flc_pal
        tfr     d,x
@c      ldb     #3
@rgb    lda     ,u+
        tst     colsh
        beq     @st
        lsla
        lsla
@st     sta     ,x+
        decb
        bne     @rgb
        inc     cidx
        bne     @nw
        ldx     #_flc_pal               ; index wrapped past 255
@nw     dec     ccount
        bne     @c
        bra     @pk
@done   inc     _flc_pal_dirty
        rts

;------------------------------------------------------------------------------
; void flc_clear(byte value): fill the whole screen with a byte
_flc_clear
        pshs    u,cc
        orcc    #$40
        lda     $FFA4
        sta     mmusv
        ldu     #_flc_scr_pages
@pg     lda     ,u+
        sta     $FFA4
        lda     6,s
        tfr     a,b
        ldx     #SCRWIN
@lp     std     ,x++
        cmpx    #SCRWIN+$2000
        blo     @lp
        cmpu    #_flc_scr_pages+4
        blo     @pg
        lda     mmusv
        sta     $FFA4
        puls    cc,u,pc

;------------------------------------------------------------------------------
; 60Hz tick counter on the VSYNC IRQ. Installed through the $FEF7 secondary
; vector, which lives in the constant $FExx page and so stays valid while
; the screen is mapped over the ROM copy at $8000-$FFFF.
_irq_install
        pshs    cc
        orcc    #$50
        lda     $FEF7
        sta     irqsv
        ldd     $FEF8
        std     irqsv+1
        lda     #$7E                    ; JMP extended
        sta     $FEF7
        ldd     #irqh
        std     $FEF8
        puls    cc,pc

_irq_remove
        pshs    cc
        orcc    #$50
        lda     irqsv
        sta     $FEF7
        ldd     irqsv+1
        std     $FEF8
        puls    cc,pc

irqh    lda     $FF03
        bpl     @nv
        lda     $FF02                   ; ack VSYNC
        inc     _flc_ticks
@nv     lda     $FF01
        bpl     @nh
        lda     $FF00                   ; ack HSYNC (shouldn't be enabled)
@nh     rti

;------------------------------------------------------------------------------
; void call_with_stack(void (*fn)(void), void *stacktop)
; Runs fn on a private stack so the $6000-$7FFF window can be remapped freely.
_call_with_stack
        ldx     2,s
        ldd     4,s
        sts     oldsp
        tfr     d,s
        jsr     ,x
        lds     oldsp
        rts

        ENDSECTION

        SECTION bss
_flc_pages      rmb     66              ; physical page per 8K of file (+ spare)
_flc_scr_pages  rmb     5               ; +1 spare for the 2-page window
_flc_src_idx    rmb     1
_flc_src_ptr    rmb     2
_flc_scr_base   rmb     2
_flc_w          rmb     2
_flc_h          rmb     2
_flc_hist_mode  rmb     1
_flc_pal_dirty  rmb     1
_flc_ticks      rmb     1
_flc_pal        rmb     768
_flc_hist       rmb     512
mmusv   rmb     4
rowoff  rmb     2
irqsv   rmb     3
oldsp   rmb     2
podd    rmb     1
cnt     rmb     2
fc      rmb     2
fv      rmb     1
cury    rmb     2
xrem    rmb     2
nlines  rmb     2
npk     rmb     1
npk16   rmb     2
lastf   rmb     1
lastv   rmb     1
cidx    rmb     1
ccount  rmb     1
colsh   rmb     1
        ENDSECTION
