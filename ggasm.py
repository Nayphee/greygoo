#!/usr/bin/env python3
"""
ggasm.py - greygoo's decoder as source to put in your own program:

    python3 ggasm.py -o ggunpack.asm                    # streams in memory
    python3 ggasm.py --input get -o ggunpack.asm        # bytes from your routine
    python3 ggasm.py --fast --zp '$02' --work '$c000' --org '$c800' -o ggunpack.asm
    python3 ggasm.py --syntax acme -o ggunpack.a

Streams come from greygoo.py --mem (a .prg that loads where it unpacks in
place, or anywhere with --mem-at) or --level (the same bytes without a
load address, for a loader to feed). Either way: the destination and the
end (2 bytes each, low first), then the stream.

  --input mem:  lda #<packed / ldx #>packed / jsr ggunpack
                (packed: the address the file loaded at: its header)
  --input get:  jsr ggunpack; it calls your ggget for every byte, the
                header first: ggget returns the next byte in a and keeps
                x and y (c may change)

The routine returns when the destination is full. On every new output page
it calls ggpage if you give --hook (keep y and c), for an effect or a
loader's bookkeeping. It needs the zero page from --zp (23 bytes; 2 more
with --fast, 1 more with --delta), the work area from --work (6 pages
from an even page; 4 more with --fast, 1 more with --delta) and itself
at --org (about 545 bytes; 95 more with --fast, 40 more with --delta).
Interrupts may stay on; the decoder touches no I/O. --delta is for
streams packed with greygoo.py --delta.

Syntax: greygoo (also CBM prg Studio's: the c64-assembly skill's
asm6502.py assembles it), acme or ca65 (translated; not assembled here).

Copyright (c) 2026 Nathan Butcher, driven by Claude Opus 5.5. zlib licence: see LICENSE.
"""
import os, sys, re, argparse
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def find(name):
    return next((p for p in (os.path.join(HERE, name), os.path.join(HERE, '..', 'src', name))
                 if os.path.exists(p)), None)


HEAD_MEM = """
; ggunpack: unpack a greygoo stream from memory. in: a/x = the address of
; its header (destination, end, then the stream). uses a, x, y
ggunpack sta ggzp               ; (the header's address, in the decoder's
        stx ggzp+1              ; zero page until it starts)
        ldy #5
@h      lda (ggzp),y            ; the destination, the end, the stream's
        sta gghdr,y             ; first two bytes
        dey
        bpl @h
        lda ggzp                ; the stream from its third byte
        clc
        adc #6
        sta rdin+1
        lda ggzp+1
        adc #0
        sta rdin+2
"""
HEAD_GET = """
; ggunpack: unpack a greygoo stream read byte by byte through ggget (the
; next byte in a, x and y kept): its header (destination, end), then the
; stream. uses a, x, y
ggunpack ldy #0
@h      jsr ggget               ; the destination, the end, the stream's
        sta gghdr,y             ; first two bytes
        iny
        cpy #6
        bne @h
"""
SETUP = """        lda gghdr+4             ; the start table from the header
        sta ggini+2
        lda gghdr+5
        sta ggini+3
        lda gghdr
        sta ggini+6
        sec
        sbc #1
        sta ggini+10
        lda gghdr+1
        sta ggini+7
        sbc #0
        sta ggini+11
        ldy #0
        jmp ggdec
ggdone  rts
{hook}"""
TAIL = """
gghdr   byte 0,0,0,0,0,0        ; the destination, the end, the stream's first two bytes
ggini   byte $80,$ff,0,0,0,0,0,0,0,>ggwork,0,0
"""


def variant(src, delta):
    """the decoder's source with or without delta matches (as ggsfx.variant)"""
    out = []
    for line in src.split('\n'):
        if '{delta}' in line:
            if not delta:
                continue
            line = line.replace(' {delta}', '').replace('{delta}', '').rstrip()
        elif '{nodelta}' in line:
            if delta:
                continue
            line = line.replace(' {nodelta}', '').replace('{nodelta}', '').rstrip()
        out.append(line)
    return '\n'.join(out)


def source(inp='mem', fast=False, zp=0x55, work=0xc000, org=0xc800, hook=False, delta=False):
    """the decoder for your program, in greygoo's (prg Studio) syntax"""
    dec = variant(open(find('ggdec-fast.asm' if fast else 'ggdec.asm')).read(), delta)
    # the end from the header, not an assembly-time constant
    for a, b in (('cmp #<ggend', 'cmp gghdr+2'), ('sbc #>ggend', 'sbc gghdr+3')):
        assert dec.count(a) == 1
        dec = dec.replace(a, b)
    if inp == 'get':
        old = re.search(r'rdin    lda ggstream.*?\n@r      sec', dec, re.S).group(0)
        dec = dec.replace(old, 'rdin    jsr ggget               ; (the next stream byte: x and y kept)\n        sec')
    head = ('; greygoo decoder (ggasm.py): input %s%s%s\n' % (inp, ', fast' if fast else '',
                                                              ', with delta matches' if delta else '')
            + 'ggzp = $%02x\nggilen = 12\nggwork = $%04x\nggstream = $ffff          ; (patched: above $ff, so lda stays absolute)\n*=$%04x\n' % (zp, work, org))
    hook_src = '' if hook else 'gghook  rts                     ; (no page hook: --hook calls ggpage)\n'
    if hook:
        hook_src = 'gghook  jmp ggpage\n'
    return (head + (HEAD_MEM if inp == 'mem' else HEAD_GET) + SETUP.format(hook=hook_src) + dec + TAIL)


def to_acme(src):
    out = []
    for line in src.split('\n'):
        code, sep, comment = line.partition(';')
        m = re.match(r'(\s*)(byte|word)\b(.*)', code)
        if m:
            code = '%s!%s%s' % (m.group(1), m.group(2), m.group(3))
        m = re.match(r'([A-Za-z_]\w*)(\s+)(byte|word)\b(.*)', code)
        if m:
            code = '%s%s!%s%s' % (m.group(1), m.group(2), m.group(3), m.group(4))
        out.append(code + sep + comment)
    return '; (ACME syntax, translated by ggasm.py: ACME 0.97 or later, for its @ cheap locals)\n' + '\n'.join(out)


def to_ca65(src):
    out = []
    for line in src.split('\n'):
        code, sep, comment = line.partition(';')
        m = re.match(r'\*=\$([0-9a-fA-F]+)', code.strip())
        if m:
            out.append('        .org $%s' % m.group(1) + (sep + comment if sep else ''))
            continue
        m = re.match(r'([A-Za-z_@]\w*)(\s+)(\S.*)?$', code)
        if m and not re.match(r'([A-Za-z_]\w*)\s*=', code) and line[:1] not in (' ', '\t', ''):
            rest = m.group(3) or ''
            code = '%s:%s%s' % (m.group(1), m.group(2), rest)
        elif re.match(r'@\w+\s*$', code):
            code = code.rstrip() + ':'
        code = re.sub(r'^(\s*(?:[@\w]+:\s*)?)(byte|word)\b', lambda mm: mm.group(1) + '.' + mm.group(2), code)
        out.append(code + sep + comment)
    return ('; (ca65 syntax, translated by ggasm.py; assemble with ca65 and link\n'
            '; with a config that puts it at the .org)\n        .setcpu "6502"\n' + '\n'.join(out))


def main():
    ap = argparse.ArgumentParser(description="greygoo's decoder as source for your own program")
    num = lambda v: int(v[1:], 16) if v.startswith('$') else int(v, 0)
    ap.add_argument('-o', dest='out', help='the output file (default: standard output)')
    ap.add_argument('--input', choices=('mem', 'get'), default='mem')
    ap.add_argument('--fast', action='store_true', help='the quarter-square decoder (1.5 times as fast, 10 pages)')
    ap.add_argument('--zp', type=num, default=0x55, help='its zero page (default $55)')
    ap.add_argument('--work', type=num, default=0xc000, help='its work area, from an even page (default $c000)')
    ap.add_argument('--org', type=num, default=0xc800, help='where it goes (default $c800)')
    ap.add_argument('--hook', action='store_true', help='call your ggpage on every new output page')
    ap.add_argument('--delta', action='store_true',
                    help='with delta matches, for streams packed with greygoo.py --delta (40 bytes more, a '
                         'zero-page byte and a work page more)')
    ap.add_argument('--syntax', choices=('greygoo', 'prgstudio', 'acme', 'ca65'), default='greygoo')
    a = ap.parse_args()
    if a.work & 0x1ff:
        sys.exit('ggasm: the work area must start on an even page')
    src = source(a.input, a.fast, a.zp, a.work, a.org, a.hook, a.delta)
    if a.syntax == 'acme':
        src = to_acme(src)
    elif a.syntax == 'ca65':
        src = to_ca65(src)
    if a.out:
        open(a.out, 'w').write(src)
    else:
        sys.stdout.write(src)


if __name__ == '__main__':
    main()
