#!/usr/bin/env python3
"""
ggsfx.py - a self-extracting greygoo program, for the C64 and the other
Commodore 8-bits:

    python3 ggsfx.py game.prg game-gg.prg
    python3 ggsfx.py -t c128 game.prg -o game-gg.prg
    python3 ggsfx.py -o out.prg main.prg music.bin@0x1000 gfx.prg,0x6000 --start 0x0810

The packed file loads where the machine's BASIC programs start, as one
BASIC line, 10 SYS<the stub>, whose end-of-program 0 is the operand of
the stub's first instruction. The program (one file, or several merged:
file.prg, file.prg,ADDR to move a .prg, file.bin@ADDR for raw data; the
gaps between them are filled with zeros) may sit anywhere in the machine's
RAM, under its ROMs and I/O too.

While it unpacks, the stream sits in place (just far enough above the
program's start, as greygoo.trace measures, that the output never
catches up with unread input), and the decoder and its work area (6
pages; 10 with --fast) go wherever there is room: above or below the
program, under the ROMs, the work area even under the I/O. The layout
chosen needs the least banking (ggtarget.py: on the C64 none, BASIC out,
the KERNAL out too, all RAM; on the C128 the same through the MMU; on the
Plus/4 the ROMs in or out; nothing on the VIC-20 or PET), and keeps the
interrupts on when it can; next it avoids the screen (borrowed, it is
blanked meanwhile). When nothing fits, the program is folded: its top or
bottom rides inside the program's own runs of one byte, and a mover at the
bottom of the stack page puts it back at the end.

The stub moves the stream, the decoder (and the mover) to their places in
an order that never overwrites what is still to be moved. The decoder
unpacks the program with a border effect on every page (the C64's greys
by default, and the Ultimate 64's turbo; the C128 runs at 2 MHz and the
Plus/4 at 1.77 MHz with the screen blanked), then puts back what it
changed, sets the memory configuration for the program (--mem), the
interrupts as they were (--sei: off) and starts the program: its SYS
(--start sys, the default), the SYS with the BASIC line trimmed from the
image (systrim), a BASIC RUN with its pointers set (basic[,START[,END
[,HIGH]]]), the first file's load address (bin) or an address.

Checked here, unless --no-check: the whole file runs in the simulator
(gg6502, counting real cycles, its memory banked as the target's is) from
its SYS to the program's start, and the program must be in memory byte for
byte, the border, turbo and screen registers, the memory configuration,
the interrupt flag and the stack as they should be; code never runs
outside RAM. The time is for the machine at its normal clock.

--desfx unpacks a self-extracting file instead (one of ours or another
cruncher's) by running it in the simulator.

Needs beside it: greygoo.py, ggcoder.py, gg6502.py, ggtarget.py,
ggdec.asm (and ggdec-fast.asm for --fast).

Copyright (c) 2026 Nathan Butcher, driven by Claude Opus 5.5. zlib licence: see LICENSE.
"""
import os, sys, argparse, re, itertools
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import greygoo as GG
import gg6502
import ggtarget

ZP = 0x55               # (the C64's; each target has its own)


def find_decoder(name):
    return next((p for p in (os.path.join(HERE, name), os.path.join(HERE, '..', 'src', name))
                 if os.path.exists(p)), None)


DECODER = find_decoder('ggdec.asm')
FAST = find_decoder('ggdec-fast.asm')     # (--fast: the multiply by quarter squares)


def decoder_path(fast=False):
    if os.environ.get('GG_DEC'):                # (experiments: another decoder in src/)
        return os.path.join(HERE, '..', 'src', os.environ['GG_DEC'])
    return FAST if fast else DECODER


def work_size(fast=False, delta=False):
    """the decoder's work area in bytes: 6 pages of probabilities (7 with
    delta matches), and 4 more for the fast decoder's quarter squares"""
    return (0x700 if delta else 0x600) + (0x400 if 'sqlo' in open(decoder_path(fast)).read() else 0)


def variant(src, delta):
    """the decoder's source with or without delta matches: lines marked
    {delta} or {nodelta} kept or dropped"""
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


# (in greygoo's own project: each assembled source and its labels, VICE style, in build/)
BUILD = os.path.join(HERE, '..', 'build')
BUILD = BUILD if os.path.isdir(BUILD) and os.path.exists(os.path.join(HERE, '..', 'src', 'ggdec.asm')) else None


def assemble_src(src, name='ggtest'):
    """assemble with gg6502: (the .prg bytes, its symbols)"""
    try:
        prg, syms = gg6502.assemble(src)
    except gg6502.AsmError as e:
        sys.exit('ggsfx: %s (%s)' % (e, name))
    if BUILD:
        open(os.path.join(BUILD, name + '.asm'), 'w').write(src)
        with open(os.path.join(BUILD, name + '.lbl'), 'w') as f:
            for k, v in sorted(syms.items(), key=lambda kv: kv[1]):
                if '@' not in k:
                    f.write('al C:%04x .%s\n' % (v & 0xffff, k))
    return prg, syms


def decoder_src(zp, work, stream_at, code_at, ini, end, head='', fast=False, delta=False):
    """the decoder's source for one layout: equates, an entry that unpacks
    and returns (or the head given), the decoder, its start table"""
    dec = decoder_path(fast)
    return ('ggzp = $%02x\nggilen = %d\nggwork = $%04x\nggstream = $%04x\nggend = $%04x\n*=$%04x\n' % (
                zp, len(ini), work, stream_at, end, code_at)
            + (head or 'entry   ldy #0\n        jsr ggdec\ngghook\nggdone  rts\n')
            + variant(open(dec).read(), delta)
            + '\nggini   byte %s\n' % ','.join(v if isinstance(v, str) else '$%02x' % v for v in ini))


def layout(stream, n, out, extra=0):
    """for unpacking n bytes in place at out: (the stream bytes to place
    (from the third, zero padded as far as the 6502 reads), their address,
    the start table)"""
    data, excess, total = GG.trace(stream, n)
    body = stream + bytes(max(0, total - len(stream)))
    place = out + excess + 3 + extra
    ini = [GG.G.START >> 16, (GG.G.START >> 8) & 255, body[0], body[1], 0, 0, out & 255, out >> 8, 0, '>ggwork',
           (out - 1) & 255, (out - 1) >> 8]
    return body[2:], place, ini


# ------------------------------------------------------------------ inputs
def number(v):
    v = v.strip()
    if v.startswith('$'):
        return int(v[1:], 16)
    return int(v, 0)


def load_input(spec):
    """file (a .prg), file,ADDR[,OFFSET[,LENGTH]] (a .prg moved to ADDR) or
    file@ADDR[,OFFSET[,LENGTH]] (raw data at ADDR; the offset and length
    pick part of the file): (load address, bytes)"""
    if os.path.exists(spec):
        d = open(spec, 'rb').read()
        if len(d) < 3:
            sys.exit('ggsfx: %s is too short for a .prg' % spec)
        return d[0] | d[1] << 8, d[2:]
    raw = False
    c, at = spec.rfind(','), spec.rfind('@')
    if at > c or (at >= 0 and ',' not in spec[:at] and not os.path.exists(spec[:spec.find(',')] if ',' in spec else '')):
        name, rest, raw = spec[:at], spec[at + 1:], True
    else:
        k = spec.find(',')
        while k >= 0 and not os.path.exists(spec[:k]):
            k = spec.find(',', k + 1)
        if k < 0:
            sys.exit('ggsfx: no such file: %s' % spec)
        name, rest = spec[:k], spec[k + 1:]
    parts = [p for p in rest.split(',')]
    d = open(name, 'rb').read()
    if not raw:
        d = d[2:]
    addr = number(parts[0]) if parts and parts[0] else None
    off = number(parts[1]) if len(parts) > 1 and parts[1] else 0
    ln = number(parts[2]) if len(parts) > 2 and parts[2] else len(d) - off
    if addr is None:
        sys.exit('ggsfx: no address in %s' % spec)
    return addr, d[off:off + ln]


def merge(pieces):
    """the files, loaded in order (later ones over earlier): (start, image
    bytes, the gaps filled with zeros)"""
    lo = min(a for a, d in pieces)
    hi = max(a + len(d) for a, d in pieces)
    if hi > 0x10000:
        sys.exit('ggsfx: a file runs past $ffff')
    img = bytearray(hi - lo)
    got = bytearray(hi - lo)
    for a, d in pieces:
        img[a - lo:a - lo + len(d)] = d
        got[a - lo:a - lo + len(d)] = b'\x01' * len(d)
    gaps, s = [], None
    for i in range(hi - lo + 1):
        g = i < hi - lo and not got[i]
        if g and s is None:
            s = i
        elif not g and s is not None:
            gaps.append((lo + s, lo + i))
            s = None
    return lo, bytes(img), gaps


def basic_end(data, load):
    """the end of a BASIC program at load: the address after its final
    link (00 00), or None"""
    a = load
    for _ in range(1000):
        i = a - load
        if i + 1 >= len(data):
            return None
        link = data[i] | data[i + 1] << 8
        if link == 0:
            return a + 2
        if link <= a:
            return None
        a = link
    return None


def sys_address(data, load, basic_start=0x0801):
    """the SYS address of a BASIC program's first line, or None"""
    if load != basic_start or len(data) < 7:
        return None
    line = data[4:data.index(0, 4)] if 0 in data[4:] else b''
    m = re.match(rb'\x9e\s*\(?\s*(\d+)', line)
    return int(m.group(1)) if m else None


def petscii(text):
    return bytes(ord(c.upper()) if ' ' <= c <= 'z' else ord('?') for c in text)


# ------------------------------------------------------------------ planning
MINRUN = 16             # the shortest run a fold stores into


def overlap(a, b):
    return a[0] < b[1] and b[0] < a[1]


def free_spans(busy, lo, hi=0x10000):
    """[lo, hi) less the busy ranges, as ranges"""
    spans = [(lo, hi)]
    for b0, b1 in busy:
        out = []
        for a0, a1 in spans:
            if b1 <= a0 or b0 >= a1:
                out.append((a0, a1))
                continue
            if a0 < b0:
                out.append((a0, b0))
            if b1 < a1:
                out.append((b1, a1))
        spans = out
    return spans


def intersect(spans, others):
    out = []
    for a0, a1 in spans:
        for b0, b1 in others:
            lo, hi = max(a0, b0), min(a1, b1)
            if lo < hi:
                out.append((lo, hi))
    return out


def plan(t, o, p_min, L, dsize, stub_end, extra=(), ws=0x600, reserved=()):
    """where everything goes while the decoder unpacks into o = (lo, hi) on
    target t: the stream (p_min or above, L bytes), the decoder (dsize
    bytes; where the stub runs never) and its work area (ws bytes from an
    even page); extra: more ranges that must be RAM. The mode that needs
    no interrupts off wins, then no borrowed screen, the least banking, the
    decoder right after the stream. None if nothing fits"""
    stub = (t.basic_start - 1, stub_end)
    p = p_min
    if overlap((p, p + L), stub):
        p = max(p, stub_end)
    if p + L > 0x10000:
        return None
    S = (p, p + L)
    ram = t.ram_anywhere()
    spans = intersect(free_spans([o, S, t.system] + list(reserved), t.low), ram)
    cands = set()
    for a0, a1 in spans:
        for x0, x1 in free_spans([stub], a0, a1):
            if x1 - x0 >= dsize:
                cands.update((x0, x1 - dsize))
    fixed = (o, S) + tuple(extra)
    modes = [m for m in t.modes if all(t.all_ram(m.cfg, r) for r in fixed)]
    if not modes:
        return None
    best = None
    for d in sorted(cands):
        D = (d, d + dsize)
        dmodes = [m for m in modes if t.all_ram(m.cfg, D) and (m.io_cfg is None or t.all_ram(m.io_cfg, D))]
        if not dmodes:
            continue
        for a0, a1 in spans:
            w = (a0 + 0x1ff) & ~0x1ff
            while w + ws <= a1:
                W = (w, w + ws)
                if not overlap(W, D):
                    m = next((m for m in dmodes if t.all_ram(m.cfg, W)), None)
                    if m is not None:
                        blank = (overlap(D, t.screen) or overlap(W, t.screen)
                                 or (overlap(S, t.screen) and not overlap(o, t.screen)))
                        cost = (m.sei, blank, m.level, d != S[1])
                        if best is None or cost < best[0]:
                            best = (cost, dict(o=o, S=S, D=D, W=W, mode=m, blank=blank))
                w += 0x200
    return best[1] if best else None


def runs(data, base, lo, hi):
    """the runs of one byte value, MINRUN long or more, in [lo, hi) of data
    (which starts at base): (address, length, value), longest first"""
    out = []
    i, end = lo - base, hi - base
    while i < end:
        j = i
        while j < end and data[j] == data[i]:
            j += 1
        if j - i >= MINRUN:
            out.append((base + i, j - i, data[i]))
        i = j
    return sorted(out, key=lambda r: -r[1])


def fold_pieces(data, base, o, T, maxpieces):
    """the folded range T stored into runs inside o: [(run address, its
    home, length, run value)], or None if the runs are too few"""
    pieces, t = [], T[0]
    for a, ln, v in runs(data, base, *o):
        if t >= T[1] or len(pieces) == maxpieces:
            break
        m = min(ln, T[1] - t)
        pieces.append((a, t, m, v))
        t += m
    return pieces if t >= T[1] else None


# ------------------------------------------------------------------ code
STUB = """
; greygoo self-extractor: 10 SYS{sys}, then the stub
*=${basic:04x}
        byte {line}
*=${code:04x}
ggstub  ldy #0                  ; (its 0 ends the BASIC program)
{body}        jmp ggdec               ; (y = 0)
blk
"""
MOVE_DOWN = """ggmv{k}   ldy #{y}                 ; {what}: {n} bytes from the top
        ldx #{chunks}
@l      dey
        lda {src},y
        sta {dst},y
        tya
        bne @l
        dec @l+3
        dec @l+6
        dex
        bne @l
"""
MOVE_UP = """ggmv{k}   ldy #{y}                 ; {what}: {n} bytes from the bottom
        ldx #{chunks}
@l      lda {src},y
        sta {dst},y
        iny
        bne @l
        inc @l+2
        inc @l+5
        dex
        bne @l
"""


def move_src(k, what, src, dst, n):
    """exact copy of n bytes: from the top when the destination is above
    (or apart), else from the bottom"""
    P, R = n >> 8, n & 255
    chunks = P + (R > 0)
    if dst < src < dst + n:
        return MOVE_UP.format(k=k, what=what, n=n, y=(256 - R) & 255, chunks=chunks,
                              src=src + R - 256 if R else src, dst=dst + R - 256 if R else dst)
    off = 256 * P if R else 256 * (P - 1)
    return MOVE_DOWN.format(k=k, what=what, n=n, y=R, chunks=chunks, src=src + off, dst=dst + off)


TAIL = """
; the decoder's page hook and finish (they run where the stub moved them;
; the finish last, nearest the decoder's branch to it)
gghook
{ioin}{hook}{ioout}        rts
{greys}{far}ggdone
{ioin}{restore}{exit}{basicptr}{fin}"""
MOVER_SRC = """
; the fold's mover: each folded piece home from the run it rode in, and
; the run filled again
*=${at:04x}
ggmover {enter}        ldy #0
@e      ldx #0
@c      lda ggmvtab,y           ; from, to, -length, the run's value
        sta ${zp:02x},x
        iny
        inx
        cpx #7
        bne @c
        sty ${zp6:02x}
        ldy #0
@m      lda (${zp:02x}),y
        sta (${zp2:02x}),y
        lda ${zp6m:02x}
        sta (${zp:02x}),y
        inc ${zp:02x}
        bne @a
        inc ${zp1:02x}
@a      inc ${zp2:02x}
        bne @b
        inc ${zp3:02x}
@b      inc ${zp4:02x}
        bne @m
        inc ${zp5:02x}
        bne @m
        ldy ${zp6:02x}
        cpy #{tlen}
        bne @e
{fin}ggmvtab byte {tab}
"""


def build(pieces, t='c64', start='sys', verbose=True, turbo=True, flash=True, hold=False, passes=None, mem=None,
          sei=False, fast=False, text='', enter='', exit_='', effect='', max_offset=None, max_length=None,
          basic_end_ptrs=True, delta='auto'):
    """the packed file for target t and facts about it. pieces: [(load
    address, bytes)], merged in order. start: 'sys' (the first file's SYS
    line), 'systrim' (the same, the BASIC line left out of the image),
    'basic[,START[,END[,HIGH]]]' (RUN the BASIC program, its pointers set),
    'bin' (the first file's load address) or an address. hold: end in a
    loop instead of starting the program (for reading the unpacked memory
    back from real hardware); mem: the memory configuration for the program
    (the target's default; without the KERNAL, interrupts stay off); sei:
    interrupts off for the program anyway; fast: the decoder that multiplies
    by quarter squares (about 1.5 times as fast, 91 bytes more, a 10-page
    work area); text: PETSCII after the SYS number; enter, exit_, effect:
    assembly run as the stub starts, before the program starts (with the
    I/O in) and on every output page (with the I/O in; keep y and c);
    basic_end_ptrs: leave BASIC as the program's own LOAD would: its
    scratch zero page clear (7 bytes) and, for a program that loads where
    BASIC programs start, its end-of-program pointers at the program's
    end (16 bytes). Without them a program that returns to BASIC (or uses
    its routines) can meet a wrong first floating-point result, or
    variables created inside it; delta: 'auto' (delta matches when the
    file with them, and the 40 bytes more of decoder, is the smaller),
    'on' or 'off'"""
    if isinstance(t, str):
        t = ggtarget.target(t)
    if mem is None:
        mem = t.default_mem
    first_load, first = pieces[0]
    load, data, gaps = merge(pieces)
    # where the program starts
    basic = None
    entry = None
    st = str(start).lower()
    if st in ('sys', 'systrim'):
        entry = sys_address(first, first_load, t.basic_start)
        if entry is None:
            sys.exit('ggsfx: no SYS line to find the start in (the first file must be a BASIC program at $%04x):'
                     ' give --start' % t.basic_start)
        if st == 'systrim':
            e = basic_end(first, first_load)
            if e is None or load != first_load:
                sys.exit('ggsfx: cannot find the end of the BASIC line to trim')
            data = data[e - load:]
            load = e
    elif st.startswith('basic'):
        parts = st.split(',')[1:]
        txt = number(parts[0]) if len(parts) > 0 and parts[0] else t.basic_start
        var = number(parts[1]) if len(parts) > 1 and parts[1] else load + len(data)
        high = number(parts[2]) if len(parts) > 2 and parts[2] else None
        basic = dict(txt=txt, var=var, high=high)
        entry = t.basic_run['calls'][0]
    elif st == 'bin':
        entry = first_load
    else:
        entry = number(st)
    n = len(data)
    if n == 0:
        sys.exit('ggsfx: nothing to pack')
    set_end = basic_end_ptrs and not basic and t.basic_start in (load, first_load)
    end = load + n
    if load < 0x0200:
        sys.exit('ggsfx: the program loads below $0200 (into the zero page or the stack)')
    if not t.all_ram(t.modes[-1].cfg, (load, end)):
        bad = next(a for a in range(load, end) if t.kinds(t.modes[-1].cfg)[a] != 'ram')
        sys.exit('ggsfx: the program reaches $%04x, which is not RAM on the %s' % (bad, t.name))

    D = [False]         # whether the decoder being built has delta matches (set by the packing below)

    def crunch(img):
        kw = {} if passes is None else {'passes': passes}
        st_ = GG.crunch(img, max_offset=max_offset, max_length=max_length, delta=D[0], **kw)
        assert GG.decode(st_, len(img)) == img, 'the stream does not decode'
        return st_

    turbo_on = turbo and t.turbo is not None
    tb = t.turbo if turbo_on else None
    flash_on = flash and t.flash is not None

    def regs(lay):
        """the registers saved by the stub and put back by the decoder:
        (name, register, mask kept, a label in the finish)"""
        r = []
        if tb and tb.get('reg') is not None:
            r.append(('turbo', tb['reg'], 0xff, 'ggturb'))
        if flash_on:
            r.append(('flash', t.flash['reg'], 0xff, 'ggbord'))
        if lay['blank'] or (tb and tb.get('blank')):
            r.append(('blank', t.blank['reg'], t.blank['keep'], 'ggscr'))
        return r

    def tramp_bytes(lay, fold):
        """the exit after the decoder, when it must not run from the decoder
        (which may sit under a ROM): store the configuration, (cli,) start"""
        st_asm, st_bytes = t.exit_store(mem)
        b = list(st_bytes) + ([0x58] if lay['cli'] else [])
        if basic:
            for c in t.basic_run['calls']:
                b += [0x20, c & 255, c >> 8]
            j = t.basic_run['jump']
        else:
            j = entry
        return b + [0x4c, j & 255, j >> 8]

    def needs_tramp(lay):
        st_asm, st_bytes = t.exit_store(mem)
        return lay['mode'].level > 0 or (st_bytes and mem != lay['mode'].cfg)

    def basic_ptr_src():
        if not basic:
            if not set_end:
                return ''
            # a BASIC program's image: the pointers as its LOAD would leave them
            e = (load + n) & 0xffff             # (a program to $ffff: LOAD wraps to 0)
            s = ('        lda #$%02x                ; BASIC\'s end of program as a LOAD\n'
                 '        ldx #$%02x                ; leaves it\n' % (e & 255, e >> 8))
            for a in t.load_end:
                s += '        sta $%04x\n        stx $%04x\n' % (a, a + 1)
            return s
        s = ''
        for k in ('txt', 'var', 'high'):
            v = basic[k]
            if v is None:
                continue
            a = t.basic_run[k]
            s += ('        lda #$%02x                ; BASIC %s\n        sta $%04x\n        lda #$%02x\n        sta $%04x\n'
                  % (v & 255, k, a, v >> 8, a + 1))
        return s

    def fill_src(lay):
        """the borrowed screen clean again before it shows"""
        src = ''
        for k, (lo, cnt, val) in enumerate(t.screen_fill):
            offs = list(range(0, cnt - 256, 256)) + [max(0, cnt - 256)]
            src += '        lda %s                ; the borrowed screen clean\n        ldx #%d\n' % (
                val, min(cnt, 256) & 255)
            src += '@f%d     dex\n' % k + ''.join('        sta $%04x,x\n' % (lo + o) for o in offs)
            src += '        bne @f%d\n' % k          # (x = 0 stored too: dex sets z, sta keeps it)
        return src

    def late(lay):
        """the decoder itself in the borrowed screen: the screen is cleaned
        from the stack page (the exit block, or the fold's mover)"""
        return lay['blank'] and overlap(lay['D'], t.screen) and not hold

    def exit_src(lay, at):
        """the exit block in the stack page: (the screen clean,) the
        configuration for the program, (cli,) its start"""
        st_asm, st_bytes = t.exit_store(mem)
        src = '*=$%04x\nggexit\n' % at + (fill_src(lay) if late(lay) else '')
        if st_bytes:
            src += '        lda #$%02x\n' % mem + st_asm
        src += '        cli\n' if lay['cli'] else ''
        if basic:
            src += ''.join('        jsr $%04x\n' % c for c in t.basic_run['calls'])
            src += '        jmp $%04x\n' % t.basic_run['jump']
        else:
            src += '        jmp %d\n' % entry
        return src

    def tail_src(lay, fold, tramp_at, far=False):
        m = lay['mode']
        restore = ''
        if lay['blank'] and not hold and not late(lay):
            restore += fill_src(lay)
        for name, reg, keep, lab in regs(lay):
            restore += '%-8slda #$%02x                ; (the stub: the %s register as it was)\n        sta $%04x\n' % (
                lab, {'turbo': 0xff, 'flash': 0x00, 'blank': 0x1b}[name], name, reg)
        ex = ''.join('        %s\n' % l.strip() for l in exit_.split(';') if l.strip()) if exit_ else ''
        # the zero page back to zeros for BASIC (its floating-point rounding
        # byte and overflow digit are assumed clear): the decoder's variables,
        # and its start table too when the trampoline is not in it
        zfrom = 'ggv' if needs_tramp(lay) and tramp_at == zp + 12 and not fold and not hold else 'ggzp'
        if not hold and basic_end_ptrs:
            ex = ('        lda #0                  ; the zero page clear for BASIC\n'
                  '        ldx #ggzlen-(%s-ggzp)\n@z      sta %s-1,x\n        dex\n        bne @z\n' % (zfrom, zfrom)) + ex
        if fold:
            fin = '        jmp ggmover\n'
        elif hold:          # (a loop before the memory configuration goes back, so a memory read sees the RAM)
            fin = m.io_out + 'gghold  jmp gghold\n'
        elif tramp_at is not None and tramp_at != zp + 12:
            fin = '        jmp $%04x               ; the exit block, in the stack page\n' % tramp_at
        elif needs_tramp(lay):
            fin = '        lda #$%02x                ; the configuration for the program, from\n' % mem + \
                  '        jmp $%04x               ; RAM that stays: this may be under a ROM\n' % tramp_at
        else:
            fin = ('        cli\n' if lay['cli'] else '')
            if basic:
                fin += ''.join('        jsr $%04x\n' % c for c in t.basic_run['calls'])
                fin += '        jmp $%04x\n' % t.basic_run['jump']
            else:
                fin += '        jmp %d\n' % entry
        hook = ''
        if flash_on:
            f = t.flash
            hook += '        lda ggout+1             ; the border, a step a page\n        and #3\n        tax\n'
            if f['rmw'] is not None:
                hook += '        lda $%04x\n        and #$%02x\n        ora gggrey,x\n' % (f['reg'], f['rmw'])
            else:
                hook += '        lda gggrey,x\n'
            hook += '        sta $%04x\n' % f['reg']
        if tb and tb.get('per_page'):
            hook += '        lda #$%02x                ; %s\n        sta $%04x\n' % (tb['value'], tb['what'], tb['reg'])
        if effect:
            hook += ''.join('        %s\n' % l.strip() for l in effect.split(';') if l.strip())
        greys = ('gggrey  byte %s\n' % ','.join('$%02x' % c for c in t.flash['colours'])) if flash_on else ''
        if far:             # (the finish too long for the decoder's branch: reached by a jmp)
            return TAIL.format(ioin=m.io_in, restore=restore, exit=ex, basicptr=basic_ptr_src(), fin=fin, hook=hook,
                               ioout=m.io_out, greys=greys, far='').replace(
                'ggdone\n', 'ggfin\n') + 'ggdone  jmp ggfin\n'
        return TAIL.format(ioin=m.io_in, restore=restore, exit=ex, basicptr=basic_ptr_src(), fin=fin, hook=hook,
                           ioout=m.io_out, greys=greys, far='')

    zp = t.zp
    ZPBUDGET = t.zp_budget  # (the decoder's start table and variables: 23, 25 with --fast; the trampoline after)

    def dec_src(lay, ini, fold, tramp_at):
        tramp = []
        if needs_tramp(lay) and not fold and not hold and tramp_at == zp + 12:
            tramp = tramp_bytes(lay, fold)
        src = decoder_src(zp, lay['W'][0], lay['S'][0], lay['D'][0], ini + tramp, lay['o'][1],
                          head=tail_src(lay, fold, tramp_at), fast=fast, delta=D[0])
        try:
            gg6502.assemble(src + ('\nggmover = $%04x\n' % t.mover[0] if fold else ''))
        except gg6502.AsmError as e:
            if 'out of range' not in str(e):
                raise
            src = decoder_src(zp, lay['W'][0], lay['S'][0], lay['D'][0], ini + tramp, lay['o'][1],
                              head=tail_src(lay, fold, tramp_at, far=True), fast=fast, delta=D[0])
        return src

    def tramp_where(lay, fold):
        """the trampoline in the zero page with the start table when it fits
        the budget, else its own block at the top of the mover's area"""
        if fold or hold or not (needs_tramp(lay) or late(lay)):
            return None
        tb_ = tramp_bytes(lay, fold)
        if not late(lay) and 12 + len(tb_) + 11 + 2 * bool(fast) + D[0] <= ZPBUDGET:
            return zp + 12
        size = len(gg6502.assemble(exit_src(lay, t.mover[0]))[0]) - 2
        return t.mover[1] - size

    def mover_src(lay, pieces_):
        tab = []
        for a, tt, m, v in pieces_:
            c = -m & 0xffff
            tab += [a & 255, a >> 8, tt & 255, tt >> 8, c & 255, c >> 8, v]
        if hold:
            fin = 'ggmhold jmp ggmhold\n'
        else:
            st_asm, _ = t.exit_store(mem)
            st_asm = (fill_src(lay) if late(lay) else '') + ('        lda #$%02x\n' % mem + st_asm if st_asm else '')
            fin = st_asm + ('        cli\n' if lay['cli'] else '')
            if basic:
                fin = basic_ptr_src() + fin + ''.join('        jsr $%04x\n' % c for c in t.basic_run['calls'])
                fin += '        jmp $%04x\n' % t.basic_run['jump']
            else:
                fin = basic_ptr_src() + fin + '        jmp %d\n' % entry
        return MOVER_SRC.format(at=t.mover[0], enter=lay['mode'].enter or '', tlen=len(tab), fin=fin,
                                tab=','.join('$%02x' % b for b in tab), zp=zp, zp1=zp + 1, zp2=zp + 2, zp3=zp + 3,
                                zp4=zp + 4, zp5=zp + 5, zp6=zp + 7, zp6m=zp + 6)

    def basic_line(sys_at):
        """10 SYS<sys_at>: its link points at the stub (BASIC V2: the stub's
        ldy #0 ends the program) or at an end marker of two zeros"""
        tx = petscii(text) if text else b''
        digits = str(sys_at).encode()
        nxt = t.basic_start + 4 + 1 + len(digits) + len(tx) + 1
        return bytes([nxt & 255, nxt >> 8, 10, 0, 0x9e]) + digits + tx + b'\x00' + (
            b'' if t.link_trick else b'\x00\x00')

    def stub_src(lay, moves):
        code_at = t.basic_start + len(basic_line(t.basic_start))
        while t.basic_start + len(basic_line(code_at)) != code_at:
            code_at = t.basic_start + len(basic_line(code_at))
        line = basic_line(code_at)
        m = lay['mode']
        body = '        sei\n' if lay['sei'] else ''
        if enter:
            body += ''.join('        %s\n' % l.strip() for l in enter.split(';') if l.strip())
        rr = regs(lay)
        early = m.io_cfg is not None        # (the I/O goes out with the moves: saved first)
        if early:
            for name, reg, keep, lab in rr:
                body += '        lda $%04x               ; the %s register as it is\n' % (reg, name)
                body += ('        and #$%02x\n' % keep if keep != 0xff else '') + '        pha\n'
                if name == 'blank':
                    body += '        and #$%02x                ; the screen off\n        sta $%04x\n' % (t.blank['off'], reg)
                if name == 'turbo' and not tb.get('per_page'):
                    body += '        lda #$%02x                ; %s\n        sta $%04x\n' % (tb['value'], tb['what'], reg)
        body += m.enter
        for k, (what, src, dst, n_) in enumerate(moves):
            body += move_src(k, what, src, dst, n_)
        if early:
            for name, reg, keep, lab in reversed(rr):
                body += '        pla\n        sta %s+1\n' % lab
        else:
            for name, reg, keep, lab in rr:
                body += '        lda $%04x               ; the %s register as it is\n' % (reg, name)
                body += ('        and #$%02x\n' % keep if keep != 0xff else '') + '        sta %s+1\n' % lab
                if name == 'blank':
                    body += '        and #$%02x                ; the screen off\n        sta $%04x\n' % (t.blank['off'], reg)
                if name == 'turbo' and not tb.get('per_page'):
                    body += '        lda #$%02x                ; %s\n        sta $%04x\n' % (tb['value'], tb['what'], reg)
        return STUB.format(sys=code_at, basic=t.basic_start, line=','.join('$%02x' % b for b in line),
                           code=code_at, body=body)

    # sizes for the planning: the largest decoder, mover and stub
    big_mode = t.modes[-1]
    big = dict(o=(0x1000, 0x2000), S=(0x2000, 0x2100), D=(0x2100, 0x2400), W=(0x2400, 0x2a00), mode=big_mode,
               blank=t.blank is not None, sei=True, cli=True)
    dmaxes = {}
    for D[0] in (False, True):
        dm_ = len(assemble_src(dec_src(big, [0] * 12, False, zp + 12), 'ggsfx-dec')[0]) - 2
        dm_ = max(dm_, len(assemble_src(dec_src(big, [0] * 12, True, None) + '\nggmover = $%04x\n' % t.mover[0],
                                        'ggsfx-dec')[0]) - 2)
        dm_ = max(dm_, len(assemble_src(dec_src(big, [0] * 12, False, 0x0180), 'ggsfx-dec')[0]) - 2)
        dmaxes[D[0]] = dm_
    D[0] = False
    mvmax_pieces = 0
    while len(assemble_src(mover_src(big, [(0x1000, 0x2000, 1, 0)] * (mvmax_pieces + 1)),
                           'ggsfx-mover')[0]) - 2 <= t.mover[1] - t.mover[0]:
        mvmax_pieces += 1
    stub_max = len(assemble_src(stub_src(big, [('x', 0x1000, 0x2000, 300)] * 4), 'ggsfx-stub')[0]) - 2
    stub_end = t.basic_start + stub_max

    def finish(lay, stream, fold, pieces_):
        """assemble the decoder (and the mover, the trampoline), order the
        moves, the file"""
        body, place, ini = layout(stream, lay['o'][1] - lay['o'][0], lay['o'][0])
        assert place <= lay['S'][0]
        tramp_at = tramp_where(lay, fold)
        code, lab = assemble_src(dec_src(lay, ini, fold, tramp_at)
                                 + ('\nggmover = $%04x\n' % t.mover[0] if fold else ''), 'ggsfx-dec')
        assert len(code) - 2 <= lay['D'][1] - lay['D'][0]
        blocks = [('the stream', lay['S'][0], body), ('the decoder', lay['D'][0], code[2:])]
        mlab = {}
        if fold:
            mv, mlab = assemble_src(mover_src(lay, pieces_), 'ggsfx-mover')
            blocks.append(('the mover', t.mover[0], mv[2:]))
        elif tramp_at is not None and tramp_at != zp + 12:
            ex_code, _ = assemble_src(exit_src(lay, tramp_at), 'ggsfx-exit')
            blocks.append(('the exit', tramp_at, ex_code[2:]))
        # in the file by their homes, those that end where the next begins as one
        blocks.sort(key=lambda b: b[1])
        merged = []
        for what, at, b in blocks:
            if merged and merged[-1][1] + len(merged[-1][2]) == at:
                merged[-1] = (merged[-1][0] + ' and ' + what, merged[-1][1], merged[-1][2] + b)
            else:
                merged.append((what, at, b))
        # the stub's size depends on its moves (their count and directions),
        # which depend on where the stub ends: settle it by iterating
        blk = t.basic_start + (len(assemble_src(stub_src(lay, [(w, 0x1000, a, len(b)) for w, a, b in merged]),
                                                'ggsfx-stub')[0]) - 2)
        for attempt_ in range(6):
            srcs, at = [], blk
            for what, home, b in merged:
                srcs.append(at)
                at += len(b)
            file_end = at
            if file_end > t.load_limit:
                sys.exit('ggsfx: the packed file would reach $%04x: too big to load on the %s' % (t.load_limit, t.name))
            mv = [(w, srcs[i], home, len(b)) for i, (w, home, b) in enumerate(merged) if srcs[i] != home]
            order = None
            for perm in itertools.permutations(mv):
                ok = all(not overlap((m_[2], m_[2] + m_[3]), (t.basic_start, blk)) for m_ in perm)
                for i, a in enumerate(perm):
                    for b in perm[i + 1:]:
                        if overlap((a[2], a[2] + a[3]), (b[1], b[1] + b[3])):
                            ok = False
                if ok:
                    order = list(perm)
                    break
            if order is None:
                return None
            stub, slab = assemble_src(stub_src(lay, order) + '\nggdec = $%04x\n' % lab['ggdec']
                                      + ''.join('%s = $%04x\n' % (k, lab[k]) for k in ('ggturb', 'ggbord', 'ggscr')
                                                if k in lab), 'ggsfx-stub')
            if slab['blk'] == blk:
                break
            blk = slab['blk']
        else:
            return None
        if not t.all_ram(lay['mode'].cfg, (t.basic_start, file_end)):
            return None
        out = stub + b''.join(b for _, _, b in merged)
        return out, dict(stub=len(stub) - 2, decoder=len(code) - 2, stream=len(stream), lab=lab, mlab=mlab,
                         file_end=file_end, sys=slab['ggstub'])

    def attempt(o, img, fold=None, pieces_=None, stream=None):
        """plan and build for the image img unpacked into o"""
        if stream is None:
            stream = crunch(img)
        _, excess, total = GG.trace(stream, len(img))
        L = max(total, len(stream)) - 2
        extra = [(load, end)] if fold else []
        reserved = [t.mover] if fold else []
        lay = plan(t, o, o[0] + excess + 3, L, dmaxes[D[0]], stub_end, extra, work_size(fast, D[0]), reserved)
        if lay is None:
            return None, stream
        lay['sei'] = lay['mode'].sei or overlap(o, t.system) or not t.kernal_after(mem) or sei
        lay['cli'] = lay['sei'] and t.kernal_after(mem) and not sei
        r = finish(lay, stream, fold, pieces_)
        return (None if r is None else (lay, r)), stream

    def pack(stream0=None):
        """the whole packing with the decoder D[0] says: (plan and file,
        the fold, its pieces, the unfolded image's stream), the first None
        if nothing fits"""
        res, stream = attempt((load, end), data, stream=stream0)
        fold, pieces_ = None, None
        if res is None:
            # fold: free the top or the bottom of the program, its bytes stored
            # in runs inside the rest and put home by the mover at the end
            _, excess, total = GG.trace(stream, n)
            L = max(total, len(stream)) - 2
            tries = []
            for side in ('top', 'bottom'):
                for k in range(256, n - 0x400, 256):
                    o = (load, end - k) if side == 'top' else (load + k, end)
                    T = (end - k, end) if side == 'top' else (load, load + k)
                    pc = fold_pieces(data, load, o, T, mvmax_pieces)
                    if pc is None:
                        break
                    # (the stream ends about k lower when the top goes; where it did
                    # when the bottom goes)
                    guess = plan(t, o, load + excess + 3 - (k if side == 'top' else 0), L + 64, dmaxes[D[0]], stub_end,
                                 [(load, end)], work_size(fast, D[0]), [t.mover])
                    if guess is not None:
                        tries.append(((guess['mode'].sei, guess['mode'].level, k), side, k))
                        break
            for _, side, k in sorted(tries):
                for kk in range(k, k + 0x800, 256):
                    o = (load, end - kk) if side == 'top' else (load + kk, end)
                    T = (end - kk, end) if side == 'top' else (load, load + kk)
                    pc = fold_pieces(data, load, o, T, mvmax_pieces)
                    if pc is None:
                        break
                    img = bytearray(data[o[0] - load:o[1] - load])
                    for a, tt, m, v in pc:
                        img[a - o[0]:a - o[0] + m] = data[tt - load:tt - load + m]
                    res, _ = attempt(o, bytes(img), fold=(side, kk, T), pieces_=pc)
                    if res is not None:
                        fold, pieces_ = (side, kk, T), pc
                        break
                if res is not None:
                    break
        return res, fold, pieces_, stream

    # with delta matches when they pay: packed with them allowed; if the
    # stream uses none it is an ordinary stream for the smaller decoder; if
    # it uses some, the file without them is built too and the smaller kept
    if delta == 'off':
        res, fold, pieces_, _ = pack()
    else:
        D[0] = True
        res, fold, pieces_, st = pack()
        if delta != 'on' and not (GG.has_delta(st, n)):
            D[0] = False
            res, fold, pieces_, _ = pack(None if fold else st)
        elif delta != 'on':
            D[0] = False
            res0, fold0, pieces0, _ = pack()
            if res0 is not None and (res is None or len(res0[1][0]) <= len(res[1][0])):
                res, fold, pieces_ = res0, fold0, pieces0
            else:
                D[0] = True
    if res is None:
        sys.exit('ggsfx: no room for the decoder and its work area, even folding the program into its runs')
    lay, (out, facts) = res
    in_size = sum(len(d) + 2 for a, d in pieces)
    if len(out) >= in_size:
        sys.exit('ggsfx: the program is too small to gain from packing')
    if hold:
        entry = facts['mlab']['ggmhold'] if fold else facts['lab']['gghold']
    if verbose:
        f = lambda r: '$%04x-$%04x' % (r[0], r[1] - 1)
        print('%s: %d bytes at $%04x (start %s): stream %d, decoder %d, stub %d: %d bytes (%d blocks)' % (
            t.name, n, load, 'BASIC RUN' if basic else '$%04x' % entry, facts['stream'], facts['decoder'],
            facts['stub'], len(out), (len(out) + 253) // 254))
        print('  unpacks with %s%s: stream %s, decoder %s, work %s%s%s' % (
            lay['mode'].what, ', interrupts off' if lay['sei'] else '', f(lay['S']), f(lay['D']), f(lay['W']),
            ', the screen blanked' if lay['blank'] or (tb and tb.get('blank')) else '',
            ', folded: %s (%d bytes) into %d runs' % (f(fold[2]), fold[2][1] - fold[2][0], len(pieces_)) if fold else ''))
        if D[0]:
            print('  with delta matches (the decoder that has them)')
        if gaps:
            print('  gaps filled with zeros: %s' % ', '.join(f(g) for g in gaps))
    return out, dict(target=t, load=load, n=n, entry=entry, data=data, stream=facts['stream'],
                     decoder=facts['decoder'], stub=facts['stub'], turbo=turbo, flash=flash, hold=hold,
                     mode=lay['mode'], fold=fold, blank=lay['blank'], mem=mem, sei=sei, fast=fast, basic=basic,
                     sys=facts['sys'], cli=lay['cli'], seid=lay['sei'],
                     load_end=end & 0xffff if set_end else None, image=(load, end), delta=D[0])


def check(out, info):
    """run the file from its SYS in the simulator, banked as the target is,
    to the program's start: (all as it should be, cycles)"""
    t = info['target']
    s = gg6502.CPU()
    mach = type(t.machine).__new__(type(t.machine))
    mach.__dict__.update(t.machine.__dict__)
    s.banked = mach
    load = out[0] | out[1] << 8
    s.load(load, out[2:])
    mach.reset(s, t.modes[0].cfg)
    for reg, v in t.io_init.items():
        s.io[reg] = v
    s.sp = 0xfd
    s.pc = info['sys']
    lo, hi = info['load'], info['load'] + info['n']
    try:
        s.run_c64(info['entry'], limit=4 * 10 ** 9, after=100)
    except gg6502.SimError as e:
        print('ggsfx: %s' % e)
        return False, s.cycles
    why = []
    if bytes(s.mem[lo:hi]) != info['data']:
        bad = next(a for a in range(lo, hi) if s.mem[a] != info['data'][a - lo])
        why.append('the memory differs from $%04x' % bad)
    for reg, v in t.io_init.items():
        if s.io[reg] != v and not (t.flash and reg == t.flash['reg'] and s.io[reg] & 15 == v & 15):
            why.append('$%04x is $%02x, not $%02x' % (reg, s.io[reg], v))
    if info['blank'] and not info['hold']:
        # a borrowed screen: clean again (where the program does not fill it)
        img = info['image']
        for flo, cnt, val in t.screen_fill:
            if val.startswith('#'):
                v = int(val[2:], 16)
                bad = [a for a in range(flo, flo + cnt) if not img[0] <= a < img[1] and s.mem[a] != v]
                if bad:
                    why.append('the borrowed screen is not clean at $%04x' % bad[0])
    if s.sp != (0xfb if info['basic'] and not info['hold'] else 0xfd):     # (a BASIC RUN: stopped in its first jsr)
        why.append('the stack is off')
    if not info['hold']:
        if mach.cfg(s) != info['mem']:
            why.append('the memory configuration is %s' % mach.state(s))
        if s.i != (bool(info['seid']) and not info['cli']):
            why.append('the interrupt flag is %d' % s.i)
        if info.get('load_end') is not None:
            for a in t.load_end:
                if s.mem[a] | s.mem[a + 1] << 8 != info['load_end']:
                    why.append("BASIC's end-of-program pointer at $%04x is wrong" % a)
        b = info['basic']
        if b:
            for k in ('txt', 'var', 'high'):
                if b[k] is not None:
                    a = t.basic_run[k]
                    if s.mem[a] | s.mem[a + 1] << 8 != b[k]:
                        why.append('the BASIC %s pointer is wrong' % k)
    for w in why:
        print('ggsfx: %s' % w)
    return not why, s.cycles


# ------------------------------------------------------------------ desfx
def desfx(prg, t='c64', limit=4 * 10 ** 9):
    """unpack a self-extracting file by running it in the simulator from
    its SYS: (load address, the unpacked bytes, the start address). The
    program is what code written by the loaded file wrote: the instruction
    that wrote the most of it, and any in the stack page that wrote 16
    bytes or more (a fold's mover; Exomizer's decruncher)"""
    if isinstance(t, str):
        t = ggtarget.target(t)
    load = prg[0] | prg[1] << 8
    data = prg[2:]
    entry = sys_address(data, load, t.basic_start)
    if entry is None:
        sys.exit('ggsfx: no SYS line in the file')
    s = gg6502.CPU()
    base = t.machine

    class Watch(type(base)):
        pass
    mach = Watch.__new__(Watch)
    mach.__dict__.update(base.__dict__)
    gen = bytearray(0x10000)
    writer = [0] * 0x10000

    def wrote(cpu, a, _w=type(base).wrote):
        gen[a] = min(gen[cpu.pc] + 1, 3)
        writer[a] = cpu.pc
        _w(mach, cpu, a)
    mach.wrote = wrote
    s.banked = mach
    s.load(load, data)
    mach.reset(s, t.modes[0].cfg)
    for reg, v in t.io_init.items():
        s.io[reg] = v
    s.sp, s.pc = 0xfd, entry
    steps = 0
    while True:
        try:
            s.step()
        except gg6502.SimError as e:
            sys.exit('ggsfx: the file stops in the simulator: %s' % e)
        steps += 1
        pc = s.pc
        k = mach.kind(s, pc)
        if k != 'ram':
            break                                           # (into a ROM: a BASIC RUN, say)
        if gen[pc] >= 2 and pc >= 0x0200:
            break                                           # (code written by written code: the program)
        if s.cycles > limit:
            sys.exit('ggsfx: the file runs too long in the simulator')
    counts = {}
    for a in range(0x0200, 0x10000):
        if gen[a] >= 2:
            counts[writer[a]] = counts.get(writer[a], 0) + 1
    if not counts:
        sys.exit('ggsfx: nothing unpacked')
    main_writer = max(counts, key=counts.get)
    # (and code in the stack page that wrote a fair amount: our fold's mover,
    # Exomizer's decruncher; not a decrunch effect's odd byte)
    writers = {main_writer} | {w for w, c in counts.items() if 0x0100 <= w < 0x0200 and c >= 16}
    prog = [a for a in range(0x0200, 0x10000) if gen[a] >= 2 and writer[a] in writers]
    lo, hi = prog[0], prog[-1] + 1
    return lo, bytes(s.mem[lo:hi]), pc


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser(description='a self-extracting greygoo program',
                                 epilog='inputs: file.prg, file.prg,ADDR[,OFFSET[,LENGTH]] (moved), '
                                        'file.bin@ADDR[,OFFSET[,LENGTH]] (raw); merged in order')
    ap.add_argument('files', nargs='+', help='the inputs, then the output (unless -o)')
    ap.add_argument('-o', dest='out', help='the output file')
    ap.add_argument('-t', '--target', default='c64',
                    help='c64 (default), c128, plus4, c16, vic20, vic20+3k, vic20+8k, vic20+16k, vic20+24k, '
                         'vic20+32k, vic20+35k, pet')
    ap.add_argument('-s', '--start', default=None,
                    help='sys (default), systrim, basic[,START[,END[,HIGH]]], bin, or an address')
    ap.add_argument('--entry', type=number, help='the address to start (as --start ADDR)')
    ap.add_argument('--no-turbo', action='store_true', help="no turbo (C64: the Ultimate's; C128: 2 MHz; "
                                                            "Plus/4, C16: the blanked screen)")
    ap.add_argument('--no-flash', action='store_true', help='no border effect while unpacking')
    ap.add_argument('--hold', action='store_true',
                    help='end in a loop instead of starting the program (to read the memory back)')
    ap.add_argument('--passes', type=int, default=None, help='parse passes (default 6; fewer is quicker, a little bigger)')
    ap.add_argument('--mem', type=number, default=None,
                    help='the memory configuration for the program: $01 on the C64 (default $37), $ff00 on the '
                         'C128 (default 0), 0 or 1 (the ROMs in or out) on the Plus/4 and C16')
    ap.add_argument('--sei', action='store_true', help='start the program with interrupts off')
    ap.add_argument('--fast', action='store_true',
                    help='the faster decoder: about 1.5 times as fast, 91 bytes more, 4 more pages of work RAM')
    ap.add_argument('--text', default='', help='text after the SYS number (shown by LIST)')
    ap.add_argument('--enter', default='', help='assembly (lines separated by ;) run first')
    ap.add_argument('--exit', default='', help='assembly run before the program starts (the I/O in)')
    ap.add_argument('--effect', default='', help='assembly run on every output page (the I/O in; keep y and c)')
    ap.add_argument('--max-offset', type=number, default=None, help='the longest match offset')
    ap.add_argument('--max-length', type=number, default=None, help='the longest match')
    ap.add_argument('--no-return', action='store_true',
                    help="the program never returns to BASIC or uses its routines: leave BASIC's pointers and "
                         "scratch zero page as the unpacking left them (23 bytes less)")
    ap.add_argument('--delta', choices=('auto', 'on', 'off'), default='auto',
                    help='delta matches (copies with a constant added: tables, ramps): auto keeps the smaller '
                         'file of with and without (default); they need 40 bytes more of decoder and a page '
                         'more of work RAM')
    ap.add_argument('--desfx', action='store_true', help='unpack a self-extracting file instead')
    ap.add_argument('--no-check', action='store_true')
    a = ap.parse_args()
    files = a.files
    out_name = a.out
    if out_name is None:
        if len(files) < 2:
            ap.error('give the output file (or -o)')
        files, out_name = files[:-1], files[-1]
    try:
        t = ggtarget.target(a.target)
    except KeyError as e:
        sys.exit('ggsfx: %s' % e)
    if a.desfx:
        lo, body, pc = desfx(open(files[0], 'rb').read(), t)
        open(out_name, 'wb').write(bytes((lo & 255, lo >> 8)) + body)
        print('unpacked $%04x-$%04x (%d bytes); it starts at $%04x' % (lo, lo + len(body) - 1, len(body), pc))
        return
    start = a.start or ('$%04x' % a.entry if a.entry is not None else 'sys')
    pieces = [load_input(f) for f in files]
    out, info = build(pieces, t, start, turbo=not a.no_turbo, flash=not a.no_flash, hold=a.hold, passes=a.passes,
                      mem=a.mem, sei=a.sei, fast=a.fast, text=a.text, enter=a.enter, exit_=a.exit, effect=a.effect,
                      max_offset=a.max_offset, max_length=a.max_length, basic_end_ptrs=not a.no_return, delta=a.delta)
    if not a.no_check:
        ok, cyc = check(out, info)
        if not ok:
            sys.exit('ggsfx: the simulator did not unpack it')
        print('checked: unpacks in the simulator in %d cycles (%.1f s at %.2f MHz%s)' % (
            cyc, cyc / t.clock, t.clock / 1e6, ', before any turbo' if t.turbo and not a.no_turbo else ''))
    open(out_name, 'wb').write(out)


if __name__ == '__main__':
    main()
