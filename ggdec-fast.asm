; ------------------------------------------------------------
; ggdec.asm - greygoo's decoder (format 4), 6502
;
; unpacks a greygoo stream (tools/greygoo.py) forward into memory:
; an adaptive binary range decoder under an LZ77 token model. every
; token: isM by the last two kinds; a literal is a bit tree, after a
; match or repeat predicted by the byte at the last offset back (its
; bits select the contexts while they agree); a match is a new one
; (length, then offset: a 4-bit slot tree by length class and its
; lower bits), a repeat of the last offset (length) or a near byte (one
; byte from 1-15 back: a fourth slot tree). lines marked {delta} add the
; delta match (the near byte's value 0, then a new match's length and
; offset and an 8-bit delta added to every byte copied); {nodelta} lines
; are for the decoder without it. ggsfx.py and ggasm.py pick one set. the arithmetic
; follows tools/ggcoder.py to the bit: a 24-bit range and code,
; normalised a bit at a time before each decision; the split is range
; bits 23-16 times the probability, by shifting and adding. the range's
; low byte is always 0 and the code's is only a delay line of input
; bits, so both keep 16 bits here. lengths and offsets are built as
; their complements (the stream codes their bits inverted), so a copy
; counts up to zero and an offset adds.
;
; written from scratch for greygoo.
; Copyright (c) 2026 Nathan Butcher, driven by Claude Opus 5.5. zlib licence: see LICENSE.
;
; in:   assembled with ggzp (ggilen + 13 zero page bytes), ggwork (10 pages,
;       from an even page), ggstream (the stream from its third byte),
;       ggend (the output's end, exclusive), gghook (called on each new
;       output page; a, x, y free to use), ggdone (where the decoder
;       goes when it has finished) and ggini: ggilen bytes for the zero
;       page, 12 or more: $80, $ff, the stream's first two bytes, 0, 0,
;       the output's start, 0, >ggwork, the output's start - 1, low
;       first (then any of the includer's own). gghook must keep y and
;       c. enter ggdec with y = 0.
; uses: a, x, y, the zero page ggzp to ggzp+ggzlen-1, the work area
; ------------------------------------------------------------

; zero page: the first ggilen bytes start from ggini (12, and any the
; includer adds after them: ggsfx's exit trampoline)
rh      = ggzp+0        ; range, bits 23-16 (normalised: 128-255)
rm      = ggzp+1        ;        bits 15-8 (bits 7-0 are always 0)
ch      = ggzp+2        ; code, bits 23-16
cm      = ggzp+3        ;       bits 15-8: the input bits come in here
bitbuf  = ggzp+4        ; the input bits left, over a marker bit
hist    = ggzp+5        ; the token kinds, two bits each (L 00, M 10, R 11, N 01)
ggout   = ggzp+6        ; (2) the next byte out
pp      = ggzp+8        ; (2) the probabilities' page (low byte 0)
src     = ggzp+10       ; (2) ggout less the last offset
ggv     = ggzp+ggilen
r0l     = ggv+0         ; (2) a new offset's complement
r0h     = ggv+1
prob    = ggv+2         ; the probability in use (shifted out by the multiply)
plo     = ggv+3         ; the split: rh * prob
phi     = ggv+4
mbyte   = ggv+5         ; the match byte's bits still to compare
ev      = ggv+6         ; a length's or offset's bit length - 1
cnt     = ggv+7         ; its bits left
tbase   = ggv+8         ; a context row in its page, + 7
lenl    = ggv+9         ; (2) a copy's length's complement (counts up)
lenh    = ggv+10
sx      = ggv+11        ; x and y kept over the multiply
sy      = ggv+12
dlt     = ggv+13        ; the delta added to a copy's bytes (0: none) {delta}
ggzlen  = ggilen+13     ; zero page bytes used {nodelta}
ggzlen  = ggilen+14     ; zero page bytes used {delta}

; the work area: 10 pages (the first even: mlit's page's bit 0 is the match bit)
mlit    = ggwork        ; 2 pages: matched literals by the match bit
lit     = ggwork+$200   ; plain literals
small   = ggwork+$300   ; isM (0-15), the unaries (32-63), isNear (131-191), isRep (225-255)
lmant   = ggwork+$400   ; length mantissas: new matches at 0, repeats at 128
offp    = ggwork+$500   ; offset slot trees (nodes 8-95), mantissas at 128
nprob   = 6             ; pages of probabilities {nodelta}
sqlo    = ggwork+$600   ; quarter squares, floor(i * i / 4) for i = 0-511: {nodelta}
sqhi    = ggwork+$800   ; low bytes, high bytes (2 pages each) {nodelta}
dpage   = ggwork+$600   ; a delta match's delta: an 8-bit tree {delta}
nprob   = 7             ; pages of probabilities {delta}
sqlo    = ggwork+$700   ; quarter squares, floor(i * i / 4) for i = 0-511: {delta}
sqhi    = ggwork+$900   ; low bytes, high bytes (2 pages each) {delta}

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
        ldx #nprob              ; the probabilities: all 128 (a, from rh; y = 0)
@p      sta (pp),y
        iny
        bne @p
        inc pp+1
        dex
        bne @p
        jsr ggsq                ; the quarter squares (x = 0, y = 0)
@loop   lda ggout               ; the end?
        cmp #<ggend
        lda ggout+1
        sbc #>ggend
        bcs ggdone
        lda #>small             ; isM by the last two kinds
        sta pp+1
        lda hist
        and #15
        tay
        jsr decbit
        bcs @match
        asl hist                ; a literal: kind 00
        asl hist
        ldy #1                  ; its bit tree's root
        lda hist
        and #12                 ; after a literal: plain
        beq @plain
        ldx #0
        lda (src,x)             ; the byte at the last offset back
        sta mbyte
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
@plain  lda #>lit
        sta pp+1
@pl     jsr decbit
        tya
        rol a
        tay
        bcc @pl
@put    jsr putbyte
        bcs @loop               ; (always: c = 1 from the tree)
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
        beq @cnt                ; (always: z = 1, y = 0)
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
        bpl @t                  ; 4 bits: the node 128 or more
        and #15
        cpy #$b0                ; class 3 (a near byte) ends at $b0-$bf
        bcs @nr
        sta ev
        ldx #r0l
        lda #$87                ; the mantissas' rows at 128
        sta tbase
        jsr mant
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
        ldy #0                  ; {delta}
@src    lda ggout               ; src = ggout - offset (r0 is its complement)
        sec
        adc r0l
        sta src
        lda ggout+1
        adc r0h
        sta src+1               ; (y = 0)
@cp     lda (src),y             ; copy, counting the complement up
        clc                     ; (a delta match: plus its delta) {delta}
        adc dlt                 ; {delta}
        jsr putbyte
@cnt    inc lenl
        bne @cp
        inc lenh
        bne @cp
        jmp @loop
@nr
        tax                     ; slot 0: a delta match {delta}
        beq @dm                 ; {delta}
        eor #$ff                ; the offset is the slot itself: r0 = its
        sta r0l                 ; complement, the length's complement ~0
        lda #$ff                ; (one byte)
        sta r0h
        sta lenl
        sta lenh
        ldy #0
        beq @src                ; (always)
@dm     lda #>small             ; a delta match: a new match's length and {delta}
        sta pp+1                ; offset follow, then the delta {delta}
        dec dlt                 ; (0 to $ff: marks it until the delta is read) {delta}
        bne @newd               ; (always) {delta}

; putbyte: a to the output, src along with it (gghook on every new
; page); returns y = 0
putbyte ldy #0
        sta (ggout),y
        inc src
        bne @s
        inc src+1
@s      inc ggout
        bne @x
        inc ggout+1
        jmp gghook
@x      rts

; ------------------------------------------------------------
; getlen  a length (n >= 1), complemented, into lenh:lenl: y = its
;         unary's first context in the small page, a = its mantissas'
;         rows in lmant, + 7
; mant    ev bits under a top 1 into the pair at x, complemented: the
;         first 3 (while the complement's low byte is $f8 or more)
;         adaptive at row (tbase - 7) + ev * 8 + (the complement & 7)
;         in the page pp, the rest at 1/2. returns y = 0, z = 1
; ------------------------------------------------------------
getlen  sta tbase
        ldx #0
@u      jsr decbit              ; (pp: the small page)
        bcc @ue
        iny
        inx
        bne @u                  ; (always)
@ue     stx ev
        ldx #lenl
        lda #>lmant
        sta pp+1
mant    lda #$ff                ; ~1
        sta 1,x
        asl a
        sta 0,x
        lda ev
        beq @x
        sta cnt
        asl a                   ; the row: tbase + ev * 8
        asl a
        asl a
        adc tbase               ; (c = 0)
        sta tbase
@b      ldy #128
        lda 0,x
        cmp #$f8
        bcs @a
        tya                     ; at 1/2: the dummy index 128, p = 128
        bcc @d                  ; (always)
@a      adc tbase               ; adaptive: row + (the complement & 7) (c = 1)
        tay
        lda (pp),y
@d      jsr decp
        rol 0,x
        rol 1,x
        dec cnt
        bne @b
@x      ldy #0
        rts

; ------------------------------------------------------------
; decbit  a bit by the probability at (pp),y into c (decp: a = the
;         probability, y its index); the probability adapts: after a 0
;         by (256 - p) >> 4 up, after a 1 by p >> 4 down. keeps x, y
;   uses: a
; ------------------------------------------------------------
decbit  lda (pp),y
decp    sta prob
        lda rh                  ; normalise: range and code left until
        bmi dmul                ; the range's top bit is set, the code
dnorm   asl bitbuf              ; taking the input's next bits
        bne dshift
rdin    lda ggstream            ; (the next stream byte)
        inc rdin+1
        bne @r
        inc rdin+2
@r      sec                     ; its top bit out, a marker bit in
        rol a
        sta bitbuf
dshift  rol cm
        rol ch
        asl rm
        rol rh
        bpl dnorm
dmul    stx sx                  ; rh * p by quarter squares: q(rh + p)
        sty sy                  ; - q(|rh - p|), exactly
        lda rh
        sec
        sbc prob
        bcs @d
        eor #$ff                ; (c = 0: the negation)
        adc #1
@d      tay
        lda rh
        clc
        adc prob
        tax
        bcs @h                  ; rh + p of 256 or more
        lda sqlo,x
        sec
        sbc sqlo,y
        sta plo
        lda sqhi,x
        sbc sqhi,y
        bcs @r                  ; (always: no borrow)
@h      lda sqlo+256,x
        sbc sqlo,y              ; (c = 1)
        sta plo
        lda sqhi+256,x
        sbc sqhi,y
@r      sta phi
        ldx sx
        ldy sy
        lda cm                  ; the code against the split
        cmp plo
        lda ch
        sbc phi
        bcs @one
        lda plo                 ; a 0: the range becomes the split
        sta rm
        lda phi
        sta rh
        lda #0                  ; p += (256 - p) >> 4
        sec
        sbc (pp),y
        lsr a
        lsr a
        lsr a
        lsr a
        clc
        adc (pp),y              ; (at most 241: c = 0, the bit)
        sta (pp),y
        rts
@one    sta ch                  ; a 1: code and range less the split
        lda cm
        sbc plo                 ; (c = 1)
        sta cm
        lda rm
        sec
        sbc plo
        sta rm
        lda rh
        sbc phi
        sta rh
        lda (pp),y              ; p -= p >> 4
        lsr a
        lsr a
        lsr a
        lsr a
        eor #$ff
        sec
        adc (pp),y              ; (p - (p >> 4) + 256: c = 1, the bit)
        sta (pp),y
        rts

; ------------------------------------------------------------
; ggsq  the quarter squares q(i) = floor(i * i / 4), i = 0-511, into
;       sqlo/sqhi: q(i + 1) = q(i) + k, k = (i + 1) >> 1. in: x = 0
; ------------------------------------------------------------
ggsq    stx r0l                 ; the quarter squares (x = 0): q(i + 1) =
        stx r0h                 ; q(i) + k, k = (i + 1) >> 1
        stx cnt
@q      lda r0l
@q1     sta sqlo,x
        lda r0h
@q2     sta sqhi,x
        txa
        lsr a
        bcc @qe                 ; (k steps up after odd i)
        inc cnt
        clc
@qe     lda r0l
        adc cnt                 ; (c = 0)
        sta r0l
        bcc @qn
        inc r0h
@qn     inx
        bne @q
        inc @q1+2               ; the second half
        inc @q2+2
        lda @q1+2
        cmp #>sqhi               ; (the high bytes' page: both halves done)
        bne @q
        rts
