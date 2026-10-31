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
;
; On a 6309 (flc_6309 != 0, set by the C side after cpu_is_6309()) chunks
; are decoded in native mode and long fills use TFM. 6309-only opcodes are
; always behind a flc_6309 test, so a 6809 never executes them.

LOTBL   equ     $0E00
HITBL   equ     $0F00
HIMID   equ     $0F80
SRCSLD  equ     $E000
SCRWIN  equ     $8000
; In BASIC's page $3B at $6000-$7FFF, after main.c's struct Data: only used
; while the file window isn't mapped there. (Not in a SECTION: an equ there
; is relative to the section.)
FLCPAL  equ     $7A00                   ; flc_pal[768]
FLCHIST equ     $7D00                   ; flc_hist[256] words, up to $7EFF

        PRAGMA  autobranchlength
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
_flc_pal_lo     EXPORT
_flc_pal_hi     EXPORT
_flc_ticks      EXPORT
_flc_6309       EXPORT
_cpu_is_6309    EXPORT
_flc_init       EXPORT

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
        tst     _flc_6309
        beq     @emu
        ldmd    #1                      ; 6309 native mode while decoding
@emu    ldb     9,s                     ; type (low byte of int arg)
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
@n6     cmpb    #$80
        bne     @n7
        lbsr    pack4
        bra     @exit
@n7     cmpb    #11
        bne     @exit
        lda     #1
        sta     colsh
        lbsr    color
@exit   stu     _flc_src_ptr
        tst     _flc_6309
        beq     @emu2
        ldmd    #0                      ; back to 6809 mode for C and BASIC
@emu2   ldd     mmusv
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

; Row cury for decoders that walk rows 0,1,2,...: full setup only for row 0
seqrow  ldd     cury
        lbeq    setrow
        lbra    nextrow

; Advance to the row below: X = its first byte, podd = 0. A step of 160
; crosses into a new page exactly when the in-page offset drops below 160.
nextrow ldd     rowoff
        addd    #160
        std     rowoff
        anda    #$1F
        adda    #SCRWIN/256
        tfr     d,x
        cmpx    #SCRWIN+160
        bhs     @same
        lda     rowoff
        lsra
        lsra
        lsra
        lsra
        lsra
        pshs    x
        ldx     #_flc_scr_pages
        ldd     a,x
        std     $FFA4
        puls    x
@same   clr     podd
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
        addd    #FLCHIST
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
        addd    #FLCHIST
        tfr     d,x
        ldd     ,x
        addd    ,s
        std     ,x
        puls    d,x,pc

;------------------------------------------------------------------------------
; BYTE_RUN (15): full frame, run-length encoded. Same E/O parity scheme as
; DELTA_FLI; a row ends when X reaches the per-row end for the current
; state (patched into the two cmpx below).
brun    tst     _flc_hist_mode
        lbne    brunslow
        clr     cury
        clr     cury+1
        lbsr    setrow
        bra     @row
@line   ldd     cury                    ; next row
        addd    #1
        std     cury
        cmpd    _flc_h
        lbhs    @done
        lbsr    nextrow
@row    cmpu    #SRCSLD
        blo     @ok
        lbsr    slide
@ok     pshs    x                       ; E state ends at X0+(w+1)/2,
        ldd     _flc_w                  ; O state at X0+w/2
        addd    #1
        lsra
        rorb
        addd    ,s
        std     @ce+1
        ldd     _flc_w
        lsra
        rorb
        addd    ,s++
        std     @co+1
        leau    1,u                     ; packet count is unreliable; use width
        bra     @epk
; ---- E state
@enext
@ce     cmpx    #0
        bhs     @line
@epk    ldb     ,u+
        bmi     @elit
        beq     @line                   ; malformed; give up on this row
        lda     ,u+                     ; fill B pixels
        sta     @m3+1
        lda     a,y
@m3     ora     <0
@efv    lsrb
        bcs     @efo
        beq     @enext
        cmpb    #6
        bhs     @t1
@el     sta     ,x+
        decb
        bne     @el
        bra     @enext
@t1     lbsr    fillw
        bra     @enext
@efo    beq     @eo1
        cmpb    #6
        bhs     @t2
@el2    sta     ,x+
        decb
        bne     @el2
        bra     @eo1
@t2     lbsr    fillw
@eo1    anda    #$F0                    ; odd pixel -> high nibble, go O
        sta     @m4+1
        lda     ,x
        anda    #$0F
@m4     ora     #0
        sta     ,x
        bra     @onext
@elit   negb                            ; literal: B pixels
@elv    lsrb
        bcs     @elo
        beq     @enext
        stb     cntb
@elp    ldd     ,u++
        stb     @m1+1
        lda     a,y
@m1     ora     <0
        sta     ,x+
        dec     cntb
        bne     @elp
        bra     @enext
@elo    beq     @elo1
        stb     cntb
@elp2   ldd     ,u++
        stb     @m1b+1
        lda     a,y
@m1b    ora     <0
        sta     ,x+
        dec     cntb
        bne     @elp2
@elo1   ldb     ,u+                     ; odd pixel -> high nibble, go O
        lda     b,y
        sta     @m2+1
        lda     ,x
        anda    #$0F
@m2     ora     #0
        sta     ,x
; ---- O state
@onext
@co     cmpx    #0
        lbhs    @line
        ldb     ,u+
        bmi     @olit
        lbeq    @line
        decb                            ; fill: one pixel here, rest as E
        lda     ,u+
        sta     @m6+1
        lda     a,y
@m6     ora     <0
        pshs    b
        tfr     a,b
        andb    #$0F
        stb     @m7+1
        ldb     ,x
        andb    #$F0
@m7     orb     #0
        stb     ,x+
        puls    b
        bra     @efv
@olit   negb                            ; literal: one pixel here, rest as E
        decb
        stb     cntb
        lda     ,x
        anda    #$F0
        ldb     ,u+
        stb     @m5+1
@m5     ora     <0
        sta     ,x+
        ldb     cntb
        bra     @elv
@done   rts

; General BYTE_RUN path (used for the histogram pass)
brunslow    clr     cury
        clr     cury+1
@line   ldd     cury
        cmpd    _flc_h
        bhs     @done
        lbsr    seqrow
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
        lbsr    seqrow
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
        lbsr    seqrow
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

;------------------------------------------------------------------------------
; DELTA_FLI (12): byte-oriented line deltas.
; Pixel parity is carried by where we are in the code instead of podd:
;   E state - next pixel is the high nibble of ,X
;   O state - next pixel is the low nibble of ,X (high nibble already done)
; An O-state fill/literal writes its first pixel, then continues as E.
; Odd/even run lengths take separate copies of each loop so no count has
; to be kept in memory. Fills of 6+ bytes go through fillw.
lc      tst     _flc_hist_mode
        lbne    lcslow
        ldb     ,u+
        lda     ,u+
        std     cury
        ldb     ,u+
        lda     ,u+
        std     nlines
        ldd     cury
        cmpd    _flc_h
        lbhs    @done
        lbsr    setrow
        bra     @row
@line   ldd     cury                    ; next row
        addd    #1
        std     cury
        cmpd    _flc_h
        lbhs    @done
        lbsr    nextrow
@row    ldd     nlines
        lbeq    @done
        subd    #1
        std     nlines
        cmpu    #SRCSLD                 ; a row's data is < 1K, check per row
        blo     @ok
        lbsr    slide
@ok     lda     ,u+
        beq     @line
        sta     npk
; ---- E state
@epk    ldb     ,u+                     ; skip
        lsrb
        abx                             ; abx leaves C alone
        bcs     @ocnt
@ecnt   ldb     ,u+                     ; count
        bmi     @efill
        beq     @enext
@elit   lsrb                            ; literal: B pairs, C = odd pixel
        bcs     @elo
        beq     @enext
        stb     cntb
@elp    ldd     ,u++
        stb     @m1+1
        lda     a,y
@m1     ora     <0
        sta     ,x+
        dec     cntb
        bne     @elp
@enext  dec     npk
        bne     @epk
        bra     @line
@elo    beq     @elo1
        stb     cntb
@elp2   ldd     ,u++
        stb     @m1b+1
        lda     a,y
@m1b    ora     <0
        sta     ,x+
        dec     cntb
        bne     @elp2
@elo1   ldb     ,u+                     ; odd pixel -> high nibble, go O
        lda     b,y
        sta     @m2+1
        lda     ,x
        anda    #$0F
@m2     ora     #0
        sta     ,x
        bra     @onext
@efill  negb                            ; fill: B pixels of one colour
        lda     ,u+
        sta     @m3+1
        lda     a,y
@m3     ora     <0                      ; A = colour in both nibbles
@efv    lsrb                            ; B bytes, C = odd pixel
        bcs     @efo
        beq     @enext
        cmpb    #6
        bhs     @t1
@el     sta     ,x+
        decb
        bne     @el
        bra     @enext
@t1     lbsr    fillw
        bra     @enext
@efo    beq     @eo1
        cmpb    #6
        bhs     @t2
@el2    sta     ,x+
        decb
        bne     @el2
        bra     @eo1
@t2     lbsr    fillw
@eo1    anda    #$F0                    ; odd pixel -> high nibble, go O
        sta     @m4+1
        lda     ,x
        anda    #$0F
@m4     ora     #0
        sta     ,x
; ---- O state
@onext  dec     npk
        lbeq    @line
@opk    ldb     ,u+                     ; skip n: odd n lands on a high nibble
        lsrb
        abx
        bcc     @ocnt
        leax    1,x
        bra     @ecnt
@ocnt   ldb     ,u+
        bmi     @ofill
        beq     @onext
        decb                            ; literal: one pixel here, rest as E
        stb     cntb
        lda     ,x
        anda    #$F0
        ldb     ,u+
        stb     @m5+1
@m5     ora     <0
        sta     ,x+
        ldb     cntb
        lbra    @elit
@ofill  negb                            ; fill: one pixel here, rest as E
        decb
        lda     ,u+
        sta     @m6+1
        lda     a,y
@m6     ora     <0
        pshs    b
        tfr     a,b
        andb    #$0F
        stb     @m7+1
        ldb     ,x
        andb    #$F0
@m7     orb     #0
        stb     ,x+
        puls    b
        bra     @efv
@done   rts

; Fill B (>0) bytes at X with A; A is preserved.
; 6809: jump into an unrolled run of word stores. 6309: TFM.
fillw   tst     _flc_6309
        bne     tfmfill
        lsrb                            ; words, C = odd byte
        bcc     @even
        sta     ,x+
@even   pshs    u
        negb                            ; enter 2*words bytes before @end
        lslb                            ; (64 words max -> -128)
        leau    @end,pcr
        leau    b,u
        tfr     a,b
        jmp     ,u
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
@end    puls    u,pc

; 6309 only: fill B (>0) bytes at X with A using TFM; returns A intact
tfmfill sta     fv
        tfr     b,f
        clre
        ldd     #fv
        tfm     d,x+
        lda     fv
        rts

; General DELTA_FLI path (used for the histogram pass)
lcslow  ldb     ,u+
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
        cmpb    #6
        bhs     @fwlong
@fwfl   sta     ,x+
        decb
        bne     @fwfl
        bra     @fnx
@fwlong lbsr    fillw
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

;------------------------------------------------------------------------------
; PACK4 ($80, CoCo extension): delta of the 32000 screen bytes themselves,
; written with no lookups (format: tools/flclib.py enc_pack4). The screen is
; one linear stream; the encoder tells us when to move the 16K screen window
; on a page (control op 1) and when to check the source window (op 2), so
; the packet loop has no bounds checks.
;   X = screen, U = source, Y = p4mid:
;   p4mid+2k -> copy k words (k=1..63), p4mid-2k -> fill k words, p4mid -> p4pk
pack4   clr     scrbank
        ldd     _flc_scr_pages
        std     $FFA4
        ldx     #SCRWIN
        ldy     #p4mid
        bra     p4pk
        ldd     ,u++
        std     ,x++
        ldd     ,u++
        std     ,x++
        ldd     ,u++
        std     ,x++
        ldd     ,u++
        std     ,x++
        ldd     ,u++
        std     ,x++
        ldd     ,u++
        std     ,x++
        ldd     ,u++
        std     ,x++
        ldd     ,u++
        std     ,x++
        ldd     ,u++
        std     ,x++
        ldd     ,u++
        std     ,x++
        ldd     ,u++
        std     ,x++
        ldd     ,u++
        std     ,x++
        ldd     ,u++
        std     ,x++
        ldd     ,u++
        std     ,x++
        ldd     ,u++
        std     ,x++
        ldd     ,u++
        std     ,x++
        ldd     ,u++
        std     ,x++
        ldd     ,u++
        std     ,x++
        ldd     ,u++
        std     ,x++
        ldd     ,u++
        std     ,x++
        ldd     ,u++
        std     ,x++
        ldd     ,u++
        std     ,x++
        ldd     ,u++
        std     ,x++
        ldd     ,u++
        std     ,x++
        ldd     ,u++
        std     ,x++
        ldd     ,u++
        std     ,x++
        ldd     ,u++
        std     ,x++
        ldd     ,u++
        std     ,x++
        ldd     ,u++
        std     ,x++
        ldd     ,u++
        std     ,x++
        ldd     ,u++
        std     ,x++
        ldd     ,u++
        std     ,x++
        ldd     ,u++
        std     ,x++
        ldd     ,u++
        std     ,x++
        ldd     ,u++
        std     ,x++
        ldd     ,u++
        std     ,x++
        ldd     ,u++
        std     ,x++
        ldd     ,u++
        std     ,x++
        ldd     ,u++
        std     ,x++
        ldd     ,u++
        std     ,x++
        ldd     ,u++
        std     ,x++
        ldd     ,u++
        std     ,x++
        ldd     ,u++
        std     ,x++
        ldd     ,u++
        std     ,x++
        ldd     ,u++
        std     ,x++
        ldd     ,u++
        std     ,x++
        ldd     ,u++
        std     ,x++
        ldd     ,u++
        std     ,x++
        ldd     ,u++
        std     ,x++
        ldd     ,u++
        std     ,x++
        ldd     ,u++
        std     ,x++
        ldd     ,u++
        std     ,x++
        ldd     ,u++
        std     ,x++
        ldd     ,u++
        std     ,x++
        ldd     ,u++
        std     ,x++
        ldd     ,u++
        std     ,x++
        ldd     ,u++
        std     ,x++
        ldd     ,u++
        std     ,x++
        ldd     ,u++
        std     ,x++
        ldd     ,u++
        std     ,x++
        ldd     ,u++
        std     ,x++
        ldd     ,u++
        std     ,x++
        ldd     ,u++
        std     ,x++
p4pk    ldb     ,u+                     ; skip 0-255 bytes
        abx
        ldb     ,u+                     ; count
        bmi     @fill
        beq     @ctl
        bitb    #1                      ; literal 1-127 bytes
        bne     @lodd
        jmp     [b,y]                   ; B = 2 * words
@lodd   lda     ,u+
        sta     ,x+
        decb
        jmp     [b,y]
@fill   bitb    #1                      ; fill 256-B bytes with the next byte
        beq     @fe
        lda     ,u
        sta     ,x+
        incb
@fe     ldd     b,y                     ; B = -2 * words
        std     @j+1
        lda     ,u+
        tfr     a,b
@j      jmp     >p4pk
@ctl    ldb     ,u+
        beq     @end                    ; 0: end of chunk
        decb
        beq     @scr                    ; 1: next screen page
        decb
        bne     p4pk                    ; 3: skip only
        cmpu    #SRCSLD                 ; 2: source window check
        blo     p4pk
        lbsr    slide
        bra     p4pk
@scr    leax    -$2000,x
        inc     scrbank
        ldb     scrbank
        pshs    x
        ldx     #_flc_scr_pages
        abx
        ldd     ,x
        std     $FFA4
        puls    x
        bra     p4pk
@end    rts
; 6309: literals of P4TFM+ words are one TFM (table entries patched by
; _flc_init). B = 2 * words.
p4tfm   clra
        tfr     d,w
        tfm     u+,x+
        lbra    p4pk
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
        std     ,x++
p4fend  bra     p4pk
        fdb     p4fend-126
        fdb     p4fend-124
        fdb     p4fend-122
        fdb     p4fend-120
        fdb     p4fend-118
        fdb     p4fend-116
        fdb     p4fend-114
        fdb     p4fend-112
        fdb     p4fend-110
        fdb     p4fend-108
        fdb     p4fend-106
        fdb     p4fend-104
        fdb     p4fend-102
        fdb     p4fend-100
        fdb     p4fend-98
        fdb     p4fend-96
        fdb     p4fend-94
        fdb     p4fend-92
        fdb     p4fend-90
        fdb     p4fend-88
        fdb     p4fend-86
        fdb     p4fend-84
        fdb     p4fend-82
        fdb     p4fend-80
        fdb     p4fend-78
        fdb     p4fend-76
        fdb     p4fend-74
        fdb     p4fend-72
        fdb     p4fend-70
        fdb     p4fend-68
        fdb     p4fend-66
        fdb     p4fend-64
        fdb     p4fend-62
        fdb     p4fend-60
        fdb     p4fend-58
        fdb     p4fend-56
        fdb     p4fend-54
        fdb     p4fend-52
        fdb     p4fend-50
        fdb     p4fend-48
        fdb     p4fend-46
        fdb     p4fend-44
        fdb     p4fend-42
        fdb     p4fend-40
        fdb     p4fend-38
        fdb     p4fend-36
        fdb     p4fend-34
        fdb     p4fend-32
        fdb     p4fend-30
        fdb     p4fend-28
        fdb     p4fend-26
        fdb     p4fend-24
        fdb     p4fend-22
        fdb     p4fend-20
        fdb     p4fend-18
        fdb     p4fend-16
        fdb     p4fend-14
        fdb     p4fend-12
        fdb     p4fend-10
        fdb     p4fend-8
        fdb     p4fend-6
        fdb     p4fend-4
        fdb     p4fend-2
p4mid   fdb     p4pk
P4TFM   equ     3
        fdb     p4pk-4
        fdb     p4pk-8
        fdb     p4pk-12
        fdb     p4pk-16
        fdb     p4pk-20
        fdb     p4pk-24
        fdb     p4pk-28
        fdb     p4pk-32
        fdb     p4pk-36
        fdb     p4pk-40
        fdb     p4pk-44
        fdb     p4pk-48
        fdb     p4pk-52
        fdb     p4pk-56
        fdb     p4pk-60
        fdb     p4pk-64
        fdb     p4pk-68
        fdb     p4pk-72
        fdb     p4pk-76
        fdb     p4pk-80
        fdb     p4pk-84
        fdb     p4pk-88
        fdb     p4pk-92
        fdb     p4pk-96
        fdb     p4pk-100
        fdb     p4pk-104
        fdb     p4pk-108
        fdb     p4pk-112
        fdb     p4pk-116
        fdb     p4pk-120
        fdb     p4pk-124
        fdb     p4pk-128
        fdb     p4pk-132
        fdb     p4pk-136
        fdb     p4pk-140
        fdb     p4pk-144
        fdb     p4pk-148
        fdb     p4pk-152
        fdb     p4pk-156
        fdb     p4pk-160
        fdb     p4pk-164
        fdb     p4pk-168
        fdb     p4pk-172
        fdb     p4pk-176
        fdb     p4pk-180
        fdb     p4pk-184
        fdb     p4pk-188
        fdb     p4pk-192
        fdb     p4pk-196
        fdb     p4pk-200
        fdb     p4pk-204
        fdb     p4pk-208
        fdb     p4pk-212
        fdb     p4pk-216
        fdb     p4pk-220
        fdb     p4pk-224
        fdb     p4pk-228
        fdb     p4pk-232
        fdb     p4pk-236
        fdb     p4pk-240
        fdb     p4pk-244
        fdb     p4pk-248
        fdb     p4pk-252

; COLOR_256 (4) / COLOR_64 (11): update flc_pal; entries that really change
; set flc_pal_dirty and widen flc_pal_lo..flc_pal_hi
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
        addd    #FLCPAL
        tfr     d,x
@c      ldb     #3
        clr     chg
@rgb    lda     ,u+
        tst     colsh
        beq     @st
        lsla
        lsla
@st     cmpa    ,x                      ; only real changes count
        beq     @eq
        sta     ,x
        inc     chg
@eq     leax    1,x
        decb
        bne     @rgb
        tst     chg
        beq     @nc
        lda     cidx                    ; widen the dirty range
        cmpa    _flc_pal_lo
        bhs     @l1
        sta     _flc_pal_lo
@l1     cmpa    _flc_pal_hi
        bls     @l2
        sta     _flc_pal_hi
@l2     inc     _flc_pal_dirty
@nc     inc     cidx
        bne     @nw
        ldx     #FLCPAL                 ; index wrapped past 255
@nw     dec     ccount
        bne     @c
        bra     @pk
@done   rts

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
; void flc_init(void): call once after setting flc_6309
_flc_init
        tst     _flc_6309
        beq     @done
        ldx     #p4mid+2*P4TFM          ; PACK4 long literals -> TFM
        ldd     #p4tfm
@lp     std     ,x++
        cmpx    #p4mid+2*64
        blo     @lp
@done   rts

;------------------------------------------------------------------------------
; byte cpu_is_6309(void)
; $10 $43 is COMD on a 6309; a 6809 ignores the prefix and runs COMA.
_cpu_is_6309
        ldd     #1
        fdb     $1043
        cmpb    #1
        beq     @no
        ldb     #1
        rts
@no     clrb
        rts

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
_flc_pal_lo     rmb     1
_flc_pal_hi     rmb     1
chg     rmb     1
_flc_ticks      rmb     1
_flc_6309       rmb     1
cntb    rmb     1
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
scrbank rmb     1
        ENDSECTION
