; ------------------------------------------------------------
; ggdec-tab.asm - greygoo's table decoder (ggsfx.py --speed 2 and 3), 6502
;
; unpacks a greygoo stream packed for speed 2 or 3 (tools/greygoo.py)
; forward into memory. the token model is ggdec.asm's; the arithmetic
; is the table coder's (tools/ggcoder.py, to the bit): an 8-bit range
; and code, normalised a bit at a time before each decision; the split
; is looked up by the range's top four bits and the probability, and
; the probability's update is looked up too, so there is no multiply.
; a bypass bit is the bit buffer's next bit and nothing more. the
; tables (10 pages) are built at the start. the output pointer is a
; page and outl, which the copies keep in y.
; lines marked {lean} are for speed 3, where a plain literal is the
; stream's next byte and a matched literal's bits after a mismatch and
; offsets' mantissas are bypass bits; {full} lines are for speed 2.
; {delta} and {nodelta} as in ggdec.asm.
;
; written from scratch for greygoo.
; Copyright (c) 2026 Nathan Butcher, driven by Claude Opus 5.5. zlib licence: see LICENSE.
;
; in:   assembled with ggzp (ggilen + 9 zero page bytes, 10 with delta
;       matches), ggwork (16 pages, 17 with delta matches, from an even
;       page), ggstream (the stream from its third byte),
;       ggend (the output's end, exclusive), gghook (called on each new
;       output page; a, x free to use, y kept), ggdone (where the
;       decoder goes when it has finished) and ggini: ggilen bytes for
;       the zero page, 12 or more: $80, $ff, the stream's first two
;       bytes, 0, 0, the output's start, 0, >ggwork, two spare (then any
;       of the includer's own). enter ggdec with y = 0.
; uses: a, x, y, the zero page ggzp to ggzp+ggzlen-1, the work area
; ------------------------------------------------------------

; zero page: the first ggilen bytes start from ggini (12, and any the
; includer adds after them: ggsfx's exit trampoline)
cnt     = ggzp+0        ; a length's or offset's bits left (starts as the $80 the probabilities get)
rh      = ggzp+1        ; the range (normalised: 128-255)
ch      = ggzp+2        ; the code
bitbuf  = ggzp+3        ; the input bits left, over a marker bit
spl     = ggzp+4        ; a split less 1 (and the table builder's)
hist    = ggzp+5        ; the token kinds, two bits each (L 00, M 10, R 11, N 01)
ggout   = ggzp+6        ; (2) the output's page (its low byte 0 once started)
pp      = ggzp+8        ; (2) the probabilities' page (low byte 0)
src     = ggzp+10       ; (2) ggout less the last offset
ggv     = ggzp+ggilen
r0l     = ggv+0         ; (2) a new offset's complement
r0h     = ggv+1
mbyte   = ggv+2         ; the match byte's bits still to compare
ev      = ggv+3         ; a length's or offset's bit length - 1
tbase   = ggv+4         ; a context row in its page, + 7
lenl    = ggv+5         ; (2) a copy's length's complement (counts up)
lenh    = ggv+6
sx      = ggv+7         ; x kept over a decision
outl    = ggv+8         ; the next byte out, in its page
dlt     = ggv+9         ; the delta added to a copy's bytes (0: none) {delta}
ggzlen  = ggilen+9      ; zero page bytes used {nodelta}
ggzlen  = ggilen+10     ; zero page bytes used {delta}

; the work area (the first page even: mlit's page's bit 0 is the match bit)
mlit    = ggwork        ; 2 pages: matched literals by the match bit
lit     = ggwork+$200   ; plain literals (speed 2)
small   = ggwork+$300   ; isM (0-15), the unaries (32-63), isNear (131-191), isRep (225-255)
lmant   = ggwork+$400   ; length mantissas: new matches at 0, repeats at 128
offp    = ggwork+$500   ; offset slot trees (nodes 8-95), mantissas at 128
nprob   = 6             ; pages of probabilities {nodelta}
up0     = ggwork+$600   ; a probability after a 0 {nodelta}
up1     = ggwork+$700   ; and after a 1 {nodelta}
splits  = ggwork+$800   ; the splits less 1: 8 pages, by the range's cell {nodelta}
spage   = ggwork        ; (splits - $800: the page for range $80-$8f at + 8) {nodelta}
dpage   = ggwork+$600   ; a delta match's delta: an 8-bit tree {delta}
nprob   = 7             ; pages of probabilities {delta}
up0     = ggwork+$700   ; a probability after a 0 {delta}
up1     = ggwork+$800   ; and after a 1 {delta}
splits  = ggwork+$900   ; the splits less 1: 8 pages, by the range's cell {delta}
spage   = ggwork+$100   ; (splits - $800) {delta}

; the small page
k_ulen  = 32            ; new match lengths' unary (16)
k_urlen = 48            ; repeats' lengths' unary (16)

; ------------------------------------------------------------
; ggdec  unpack
; ------------------------------------------------------------
ggdec   ldx #ggilen-1           ; the state from ggini
@i      lda ggini,x
        sta ggzp,x
        dex
        bpl @i
        ldx #nprob              ; the probabilities: all 128 (a, from cnt; y = 0)
@p      sta (pp),y
        iny
        bne @p
        inc pp+1
        dex
        bne @p
        jsr ggtab               ; the tables (x = 0, y = 0)
        lda ggout               ; the output pointer: its page, and outl
        sta outl
        sty ggout
        dey                     ; src: the page less 1 (the last offset starts as 1)
        sty src
        ldx ggout+1
        dex
        stx src+1
@loop   lda ggout+1             ; the end? (the low byte matters only in
        cmp #>ggend             ; the last page)
        bcc @go
        lda outl
        cmp #<ggend
        bcs ggdone
@go     lda #>small             ; isM by the last two kinds
        sta pp+1
        lda hist
        and #15
        tay
        jsr decbit
        bcs @match
        asl hist                ; a literal: kind 00
        asl hist
        lda hist
        and #12                 ; after a literal: plain
        beq @plain
        ldy outl
        lda (src),y             ; the byte at the last offset back
        sta mbyte
        ldy #1                  ; the bit tree's root
@m      asl mbyte               ; c = the match bit
        lda #>mlit
        adc #0
        sta pp+1
        jsr decbit
        tya
        rol a
        tay                     ; c = 8 bits done
        bcs @put
        eor pp+1                ; bit 0: the bit against the match bit
        lsr a
        bcc @m                  ; still agreeing
        lda #>lit               ; {full}
        sta pp+1                ; {full}
@pl     jsr decbit              ; {full}
        tya                     ; {full}
        rol a                   ; {full}
        tay                     ; {full}
        bcc @pl                 ; {full}
        bcs @put                ; (always) {full}
@plain  ldy #1                  ; {full}
        lda #>lit               ; {full}
        sta pp+1                ; {full}
        bne @pl                 ; (always) {full}
@pl     asl bitbuf              ; (the rest: bypass bits) {lean}
        bne @pb                 ; {lean}
        jsr refill              ; {lean}
@pb     tya                     ; {lean}
        rol a                   ; {lean}
        tay                     ; {lean}
        bcc @pl                 ; {lean}
        bcs @put                ; (always) {lean}
@plain  jsr getbyte             ; (a plain literal: the stream's next byte) {lean}
@put    ldy outl
        sta (ggout),y
        inc outl
        bne @loop
        jsr page
        jmp @loop
@match  rol hist                ; (c = 1)
        ldx #0                  ; no delta until a delta match says so {delta}
        stx dlt                 ; {delta}
        lda hist                ; isRep: at $e0 + (hist & 31), odd
        ora #$e0
        tay
        jsr decbit
        bcc @new
        rol hist                ; kind 11 (c = 1)
        lda hist                ; isNear: at 128 + (hist & 63), 3 mod 4
        and #63
        ora #128
        tay
        jsr decbit
        bcs @near
        lda #$87                ; a repeat: its length (n bytes): mantissa rows at 128
        ldy #k_urlen
        jsr getlen
        ldy outl
        ldx lenl
        jmp @cnt
@near   dec hist                ; a near byte: kind 01
        dec hist
        ldy #8+3                ; its offset: the slot tree of class 3
        bne @tr                 ; (always)
@new    asl hist                ; a new match: kind 10
@newd
        lda #7                  ; its length (n + 1 bytes): mantissa rows at 0
        ldy #k_ulen
        jsr getlen
        lda ev                  ; its offset: the slot tree by min(e, 2)
        cmp #2
        bcc @c
        lda #2
@c      ora #8
        tay
@tr     lda #>offp
        sta pp+1
@t      jsr decbit
        tya
        rol a
        tay
        bpl @t                  ; 4 bits: the node 128 or more {full}
        and #$20                ; 2 bits: the node 32 or more {lean}
        beq @t                  ; {lean}
@t2     asl bitbuf              ; 2 more: bypass bits {lean}
        bne @t3                 ; {lean}
        jsr refill              ; {lean}
@t3     tya                     ; {lean}
        rol a                   ; {lean}
        tay                     ; {lean}
        bpl @t2                 ; {lean}
        and #15
        cpy #$b0                ; class 3 (a near byte) ends at $b0-$bf
        bcs @nr
        sta ev
        ldx #r0l
        lda #$87                ; the mantissas' rows at 128 {full}
        sta tbase               ; {full}
        jsr mant                ; {full}
        jsr rmant               ; (its mantissa: bypass bits) {lean}
        lda dlt                 ; a delta match: its delta, 8 bits through {delta}
        beq @src                ; a tree {delta}
        lda #>dpage             ; {delta}
        sta pp+1                ; {delta}
        ldy #1                  ; {delta}
@dt     jsr decbit              ; {delta}
        tya                     ; {delta}
        rol a                   ; {delta}
        tay                     ; {delta}
        bcc @dt                 ; {delta}
        sty dlt                 ; {delta}
@src    sec                     ; src = the page - offset (r0 is its complement)
        lda #0
        adc r0l
        sta src
        lda ggout+1
        adc r0h
        sta src+1
        ldy outl
        ldx lenl
        lda dlt                 ; {delta}
        bne @dcp                ; {delta}
@cp     lda (src),y             ; copy, counting the complement up in x and lenh
        sta (ggout),y
        iny
        beq @pg
@cnt    inx
        bne @cp
        inc lenh
        bne @cp
        sty outl
        jmp @loop
@pg     stx lenl                ; a new output page
        jsr page
        ldx lenl
        jmp @cnt
@dcp    lda (src),y             ; a delta match's copy: plus its delta {delta}
        clc                     ; {delta}
        adc dlt                 ; {delta}
        sta (ggout),y           ; {delta}
        iny                     ; {delta}
        bne @dc                 ; {delta}
        stx lenl                ; {delta}
        jsr page                ; {delta}
        ldx lenl                ; {delta}
@dc     inx                     ; {delta}
        bne @dcp                ; {delta}
        inc lenh                ; {delta}
        bne @dcp                ; {delta}
        sty outl                ; {delta}
        jmp @loop               ; {delta}
@nr
        tax                     ; slot 0: a delta match {delta}
        beq @dm                 ; {delta}
        eor #$ff                ; the offset is the slot itself: r0 = its
        sta r0l                 ; complement, the length's complement ~0
        lda #$ff                ; (one byte)
        sta r0h
        sta lenl
        sta lenh
        jmp @src
@dm     lda #>small             ; a delta match: a new match's length and {delta}
        sta pp+1                ; offset follow, then the delta {delta}
        dec dlt                 ; (0 to $ff: marks it until the delta is read) {delta}
        jmp @newd               ; {delta}

; page: the output (and src with it) into its next page; gghook is
; called (y kept: 0)
page    inc ggout+1
        inc src+1
        jmp gghook

; ------------------------------------------------------------
; getlen  a length (n >= 1), complemented, into lenh:lenl: y = its
;         unary's first context in the small page, a = its mantissas'
;         rows in lmant, + 7
; mant    ev bits under a top 1 into the pair at x, complemented: the
;         first 3 adaptive at row (tbase - 7) + ev * 8 + (the
;         complement & 7) in the page pp, the rest by rtail
; rmant   the same with no adaptive bits (speed 3: lengths' too) {lean}
; rtail   cnt (1 or more) bits into the pair at x: bypass bits, but the
;         last 8 of 8 or more are the stream's next byte
; ------------------------------------------------------------
getlen  sta tbase
        lda #0
        sta ev
@u      jsr decbit              ; (pp: the small page)
        bcc @ue
        iny
        inc ev
        bne @u                  ; (always)
@ue     ldx #lenl
        lda #>lmant             ; {full}
        sta pp+1                ; {full}
mant    lda #$ff                ; ~1 {full}
        sta 1,x                 ; {full}
        asl a                   ; {full}
        sta 0,x                 ; {full}
        lda ev                  ; {full}
        beq @x                  ; {full}
        sta cnt                 ; {full}
        asl a                   ; the row: tbase + ev * 8 {full}
        asl a                   ; {full}
        asl a                   ; {full}
        adc tbase               ; (c = 0) {full}
        sta tbase               ; {full}
@b      lda 0,x                 ; {full}
        cmp #$f8                ; (the value so far under 8: its first 3 bits) {full}
        bcc rtail               ; {full}
        adc tbase               ; adaptive: row + (the complement & 7) (c = 1) {full}
        tay                     ; {full}
        stx sx                  ; {full}
        jsr decbit              ; {full}
        ldx sx                  ; {full}
        rol 0,x                 ; {full}
        rol 1,x                 ; {full}
        dec cnt                 ; {full}
        bne @b                  ; {full}
@x      rts                     ; {full}
rmant   lda #$ff                ; ~1 {lean}
        sta 1,x                 ; {lean}
        asl a                   ; {lean}
        sta 0,x                 ; {lean}
        lda ev                  ; {lean}
        beq rtailx              ; {lean}
        sta cnt                 ; {lean}
rtail   lda cnt
        sec
        sbc #8
        bcc rbits               ; under 8: bits only
        beq @y
        sta cnt
        jsr rbits               ; the bits above the byte
@y      lda 0,x                 ; the low 8 bits: a byte
        sta 1,x
        jsr getbyte
        sta 0,x
rtailx  rts
rbits   asl bitbuf
        bne @r
        jsr refill
@r      rol 0,x
        rol 1,x
        dec cnt
        bne rbits
        rts

; ------------------------------------------------------------
; decbit  a bit by the probability at (pp),y into c; the probability
;         adapts through up0 / up1. keeps y
;   uses: a, x
; ------------------------------------------------------------
decbit  lda (pp),y
        tax
        lda rh                  ; normalise: range and code left until
        bmi @m                  ; the range's top bit is set, the code
@n      asl bitbuf              ; taking the input's next bits
        bne @s
        jsr refill
@s      rol ch
        asl rh
        bpl @n
        lda rh
@m      lsr a                   ; the range's cell picks the splits' page
        lsr a
        lsr a
        lsr a
        clc
        adc #>spage
        sta dsplit+2
dsplit  lda $ff00,x             ; the split less 1
        cmp ch
        bcc @one
        adc #0                  ; a 0: the range becomes the split (c = 1)
        sta rh
        lda up0,x
        sta (pp),y
        rts                     ; (c = 0)
@one    sta spl                 ; a 1: code and range less the split
        lda ch
        sbc spl                 ; (c = 0: less one more)
        sta ch
        lda rh
        clc
        sbc spl
        sta rh
        lda up1,x
        sta (pp),y
        rts                     ; (c = 1)

; ------------------------------------------------------------
; refill   the stream's next byte into the bit buffer: its top bit out
;          in c, a marker bit in. keeps x, y
; getbyte  the stream's next byte in a. keeps x, y
; ------------------------------------------------------------
getbyte clc
        bcc rdin                ; (always)
refill  sec
rdin    lda ggstream            ; (the next stream byte)
        inc rdin+1
        bne @r
        inc rdin+2
@r      bcc @x
        rol a                   ; (c = 1: the marker)
        sta bitbuf
@x      rts

; ------------------------------------------------------------
; ggtab  the update tables and the splits. in: x = 0, y = 0
; ------------------------------------------------------------
ggtab
@tu     txa                     ; the update tables (x = 0): after a 1,
        lsr a                   ; p - (p >> 4)
        lsr a
        lsr a
        lsr a
        sta spl
        txa
        sec
        sbc spl
        sta up1,x
        txa                     ; after a 0, p + ((256 - p) >> 4)
        eor #$ff
        adc #0                  ; (c = 1)
        lsr a
        lsr a
        lsr a
        lsr a
        stx spl
        clc
        adc spl
        sta up0,x
        inx
        bne @tu
        lda #>splits            ; the splits less 1, a page for each cell of
        sta pp+1                ; the range: min(lo - 1, (mid * p + 128) >> 8),
        lda #136                ; mid = lo + 8 = 136, 152 .. 248
        sta cnt
@tc     lda #128                ; mid * p + 128 in x and spl, p in y
        sta spl
        ldx #0
@ts     txa
        clc
        adc #8
        cmp cnt
        txa
        bcc @tk
        lda cnt                 ; lo - 1 at most
        sbc #9                  ; (c = 1)
        clc
@tk     sbc #0                  ; less 1 (c = 0)
        sta (pp),y
        lda spl
        clc
        adc cnt
        sta spl
        bcc @tn
        inx
@tn     iny
        bne @ts
        inc pp+1
        lda cnt
        clc
        adc #16
        sta cnt
        bcc @tc                 ; (after 248: done)
        rts
