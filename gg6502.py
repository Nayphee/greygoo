#!/usr/bin/env python3
"""
gg6502.py - a small 6502 assembler and cycle-counting simulator, enough
for greygoo's tools to build and check self-extracting files with
nothing but Python. Written from scratch for greygoo.

Assembler (assemble(source) -> (the .prg bytes, the symbols)):
  label   mnemonic operand      ; comment
  name = expression             (an equate)
  *=expression                  (the origin; the first one is the load address)
  label   byte expression, ...
Labels start in the first column; @name labels are local to the last
global label. Operands: #expr, expr, expr,x, expr,y, (expr,x), (expr),y,
(expr) (jmp), a (the accumulator). Expressions: $hex, %binary, decimal,
symbols, + - * /, a leading < or > for the low or high byte of all that
follows. An operand below $100 takes the zero page form when there is
one. It passes over the source until every symbol settles.

Simulator (CPU): the documented NMOS 6502 instructions (an undocumented
opcode or decimal-mode arithmetic stops it with an error), real cycle
counts (+1 for an indexed read crossing a page, +1 for a taken branch,
+1 more into another page). Memory is flat; run_c64() stops with an
error if the CPU ever runs code where the C64's $01 would put a ROM or
the I/O.

python3 gg6502.py --selftest

Copyright (c) 2026 Nathan Butcher, driven by Claude Opus 5.5. zlib licence: see LICENSE.
"""
import re, sys


class AsmError(Exception):
    pass


class SimError(Exception):
    pass


# ------------------------------------------------------------ the instruction set
# opcode: mnemonic, mode
_TABLE = """
adc 69 imm 65 zp 75 zpx 6d abs 7d abx 79 aby 61 izx 71 izy
and 29 imm 25 zp 35 zpx 2d abs 3d abx 39 aby 21 izx 31 izy
cmp c9 imm c5 zp d5 zpx cd abs dd abx d9 aby c1 izx d1 izy
eor 49 imm 45 zp 55 zpx 4d abs 5d abx 59 aby 41 izx 51 izy
lda a9 imm a5 zp b5 zpx ad abs bd abx b9 aby a1 izx b1 izy
ora 09 imm 05 zp 15 zpx 0d abs 1d abx 19 aby 01 izx 11 izy
sbc e9 imm e5 zp f5 zpx ed abs fd abx f9 aby e1 izx f1 izy
sta 85 zp 95 zpx 8d abs 9d abx 99 aby 81 izx 91 izy
asl 0a acc 06 zp 16 zpx 0e abs 1e abx
lsr 4a acc 46 zp 56 zpx 4e abs 5e abx
rol 2a acc 26 zp 36 zpx 2e abs 3e abx
ror 6a acc 66 zp 76 zpx 6e abs 7e abx
inc e6 zp f6 zpx ee abs fe abx
dec c6 zp d6 zpx ce abs de abx
ldx a2 imm a6 zp b6 zpy ae abs be aby
ldy a0 imm a4 zp b4 zpx ac abs bc abx
stx 86 zp 96 zpy 8e abs
sty 84 zp 94 zpx 8c abs
cpx e0 imm e4 zp ec abs
cpy c0 imm c4 zp cc abs
bit 24 zp 2c abs
bpl 10 rel
bmi 30 rel
bvc 50 rel
bvs 70 rel
bcc 90 rel
bcs b0 rel
bne d0 rel
beq f0 rel
jmp 4c abs 6c ind
jsr 20 abs
rts 60 imp
rti 40 imp
brk 00 imp
php 08 imp
plp 28 imp
pha 48 imp
pla 68 imp
dey 88 imp
tay a8 imp
iny c8 imp
inx e8 imp
clc 18 imp
sec 38 imp
cli 58 imp
sei 78 imp
tya 98 imp
clv b8 imp
cld d8 imp
sed f8 imp
txa 8a imp
txs 9a imp
tax aa imp
tsx ba imp
dex ca imp
nop ea imp
"""
OPCODE = {}                 # (mnemonic, mode) -> opcode
DECODE = [None] * 256       # opcode -> (mnemonic, mode)
for _line in _TABLE.strip().split('\n'):
    _p = _line.split()
    for _i in range(1, len(_p), 2):
        _op = int(_p[_i], 16)
        OPCODE[(_p[0], _p[_i + 1])] = _op
        DECODE[_op] = (_p[0], _p[_i + 1])
MNEMONICS = {m for m, _ in OPCODE}
SIZE = {'imp': 1, 'acc': 1, 'imm': 2, 'zp': 2, 'zpx': 2, 'zpy': 2, 'izx': 2, 'izy': 2, 'rel': 2,
        'abs': 3, 'abx': 3, 'aby': 3, 'ind': 3}
READS = {'adc', 'and', 'cmp', 'eor', 'lda', 'ora', 'sbc', 'ldx', 'ldy'}
RMW = {'asl', 'lsr', 'rol', 'ror', 'inc', 'dec'}
WRITES = {'sta', 'stx', 'sty'} | RMW


def cycles_of(mn, mode):
    """base cycles (no page-crossing or branch extras)"""
    if mode == 'rel':
        return 2
    if mn in RMW:
        return {'acc': 2, 'zp': 5, 'zpx': 6, 'abs': 6, 'abx': 7}[mode]
    if mn in ('sta', 'stx', 'sty'):
        return {'zp': 3, 'zpx': 4, 'zpy': 4, 'abs': 4, 'abx': 5, 'aby': 5, 'izx': 6, 'izy': 6}[mode]
    if mn in ('jsr', 'rts', 'rti'):
        return 6
    if mn == 'brk':
        return 7
    if mn == 'jmp':
        return 3 if mode == 'abs' else 5
    if mn in ('pha', 'php'):
        return 3
    if mn in ('pla', 'plp'):
        return 4
    return {'imp': 2, 'imm': 2, 'zp': 3, 'zpx': 4, 'zpy': 4, 'abs': 4, 'abx': 4, 'aby': 4,
            'izx': 6, 'izy': 5}[mode]


CYCLES = [cycles_of(*d) if d else 0 for d in DECODE]


# ------------------------------------------------------------ the assembler
_NUM = re.compile(r'\$[0-9a-fA-F]+|%[01]+|\d+')
_SYM = re.compile(r'@?[A-Za-z_][A-Za-z0-9_]*')


def _eval(text, lookup, line):
    """the value of an expression, or None while a symbol is still unknown"""
    t = text.strip()
    if not t:
        raise AsmError('line %d: an empty expression' % line)
    part = None
    if t[0] in '<>':
        v = _eval(t[1:], lookup, line)
        return None if v is None else (v & 255 if t[0] == '<' else (v >> 8) & 255)
    # + and - (left to right), over * and /
    tokens = re.findall(r'\s*(\$[0-9a-fA-F]+|%[01]+|\d+|@?[A-Za-z_][A-Za-z0-9_]*|[-+*/()])', t)
    if ''.join(tokens).replace(' ', '') != t.replace(' ', ''):
        raise AsmError('line %d: cannot read the expression %r' % (line, text))
    pos = 0
    unknown = False

    def atom():
        nonlocal pos, unknown
        tok = tokens[pos].strip()
        pos += 1
        if tok == '-':
            v = atom()
            return -v
        if tok == '(':
            v = expr()
            if pos >= len(tokens) or tokens[pos].strip() != ')':
                raise AsmError('line %d: a ( without its )' % line)
            pos += 1
            return v
        if tok[0] == '$':
            return int(tok[1:], 16)
        if tok[0] == '%':
            return int(tok[1:], 2)
        if tok[0].isdigit():
            return int(tok)
        v = lookup(tok)
        if v is None:
            unknown = True
            return 0
        return v

    def term():
        nonlocal pos
        v = atom()
        while pos < len(tokens) and tokens[pos].strip() in '*/':
            op = tokens[pos].strip()
            pos += 1
            w = atom()
            v = v * w if op == '*' else (v // w if w else 0)
        return v

    def expr():
        nonlocal pos
        v = term()
        while pos < len(tokens) and tokens[pos].strip() in '+-':
            op = tokens[pos].strip()
            pos += 1
            w = term()
            v = v + w if op == '+' else v - w
        return v

    v = expr()
    if pos != len(tokens):
        raise AsmError('line %d: cannot read the expression %r' % (line, text))
    return None if unknown else v


def _split_label(raw):
    """(label or None, the rest) of a source line (comment removed)"""
    code = raw.split(';', 1)[0].rstrip()
    if not code.strip():
        return None, ''
    if code[0] not in ' \t':
        p = code.split(None, 1)
        return p[0], (p[1] if len(p) > 1 else '')
    return None, code.strip()


def assemble(source):
    """assemble source: (the .prg bytes: load address and image, symbols)"""
    lines = source.split('\n')
    known = {}              # symbol -> value from the last pass
    for npass in range(12):
        syms = {}
        mem = {}
        pc = None
        origin = None
        scope = ''
        changed = False
        final = npass > 0

        def full(name):
            return scope + name if name.startswith('@') else name

        def lookup(name):
            k = full(name)
            if k in syms:
                return syms[k]
            return known.get(k)

        for no, raw in enumerate(lines, 1):
            label, rest = _split_label(raw)
            # an equate: name = expr (also "* = expr")
            m = re.match(r'^\s*(\*|@?[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+)$', raw.split(';', 1)[0])
            if m:
                v = _eval(m.group(2), lookup, no)
                if m.group(1) == '*':
                    if v is None:
                        raise AsmError('line %d: the origin must be known' % no)
                    pc = v
                    if origin is None:
                        origin = v
                    continue
                name = full(m.group(1))
                if v is None:
                    if final and npass > 1:
                        raise AsmError('line %d: %s is undefined' % (no, m.group(2).strip()))
                    continue
                syms[name] = v
                continue
            if label:
                if not label.startswith('@'):
                    scope = label
                if pc is None:
                    raise AsmError('line %d: a label before any origin' % no)
                name = full(label)
                if name in syms:
                    raise AsmError('line %d: %s is defined twice' % (no, label))
                syms[name] = pc
            if not rest:
                continue
            p = rest.split(None, 1)
            mn = p[0].lower()
            operand = p[1].strip() if len(p) > 1 else ''
            if pc is None:
                raise AsmError('line %d: code before any origin' % no)
            if mn == 'byte':
                for item in operand.split(','):
                    v = _eval(item, lookup, no)
                    if v is None and final and npass > 1:
                        raise AsmError('line %d: %s is undefined' % (no, item.strip()))
                    v = v or 0
                    if not -128 <= v <= 255:
                        raise AsmError('line %d: %d does not fit a byte' % (no, v))
                    mem[pc] = v & 255
                    pc += 1
                continue
            if mn not in MNEMONICS:
                raise AsmError('line %d: unknown instruction %r' % (no, mn))
            mode, val = _operand(mn, operand, lookup, no, pc)
            if mode is None:
                raise AsmError('line %d: %s cannot take %r' % (no, mn, operand))
            op = OPCODE[(mn, mode)]
            size = SIZE[mode]
            mem[pc] = op
            if val is None:
                val = 0 if mode != 'rel' else pc + 2
                if npass > 1:
                    raise AsmError('line %d: %r is undefined' % (no, operand))
            if mode == 'rel':
                d = val - (pc + 2)
                if not -128 <= d <= 127:
                    if npass > 1:
                        raise AsmError('line %d: branch out of range (%d bytes)' % (no, d))
                    d = 0
                mem[pc + 1] = d & 255
            elif size == 2:
                if not -128 <= val <= 255:
                    raise AsmError('line %d: %d does not fit a byte' % (no, val))
                mem[pc + 1] = val & 255
            elif size == 3:
                mem[pc + 1] = val & 255
                mem[pc + 2] = (val >> 8) & 255
            pc += size
        if syms != known:
            changed = True
        known = syms
        if not changed and npass >= 1:
            break
    else:
        raise AsmError('the symbols never settled')
    if not mem:
        raise AsmError('no code')
    lo, hi = min(mem), max(mem)
    image = bytearray(hi - lo + 1)
    for a, v in mem.items():
        image[a - lo] = v
    return bytes([origin & 255, origin >> 8]) + bytes(image), dict(known)


def _operand(mn, text, lookup, line, pc):
    """(the mode, the value) of an operand"""
    t = text.strip()
    tl = t.lower()
    if not t:
        if (mn, 'imp') in OPCODE:
            return 'imp', 0
        if (mn, 'acc') in OPCODE:
            return 'acc', 0
        return None, None
    if tl == 'a' and (mn, 'acc') in OPCODE:
        return 'acc', 0
    if (mn, 'rel') in OPCODE:
        return 'rel', _eval(t, lookup, line)
    if t.startswith('#'):
        return ('imm' if (mn, 'imm') in OPCODE else None), _eval(t[1:], lookup, line)
    m = re.match(r'^\((.*)\)\s*,\s*y$', t, re.I)
    if m:
        return ('izy' if (mn, 'izy') in OPCODE else None), _eval(m.group(1), lookup, line)
    m = re.match(r'^\((.*),\s*x\s*\)$', t, re.I)
    if m:
        return ('izx' if (mn, 'izx') in OPCODE else None), _eval(m.group(1), lookup, line)
    m = re.match(r'^\((.*)\)$', t)
    if m and (mn, 'ind') in OPCODE:
        return 'ind', _eval(m.group(1), lookup, line)
    idx = ''
    m = re.match(r'^(.*),\s*([xy])$', t, re.I)
    if m:
        t, idx = m.group(1), m.group(2).lower()
    v = _eval(t, lookup, line)
    zp, ab = ('zp' + idx, 'ab' + (idx or 's'))
    if v is not None and 0 <= v < 256 and (mn, zp) in OPCODE:
        return zp, v
    if (mn, ab) in OPCODE:
        return ab, v
    if (mn, zp) in OPCODE and v is None:
        return zp, v
    return None, None


# ------------------------------------------------------------ the simulator
class CPU:
    """a 6502 over 64 KB of flat memory; cycles counts real cycles"""

    def __init__(self):
        self.mem = bytearray(65536)
        self.a = self.x = self.y = 0
        self.sp = 0xfd
        self.pc = 0
        self.n = self.v = self.d = self.i = self.z = self.c = 0
        self.cycles = 0
        # banked: None (flat memory), or a machine (ggtarget.Machine) whose
        # kind(cpu, address) says what each data access reaches: RAM, a ROM
        # (reading one is an error; writes go to the RAM under it), I/O
        # (kept in self.io, apart from the RAM under it) or nothing (an error)
        self.banked = None
        self.io = bytearray(0x10000)

    def load(self, addr, data):
        self.mem[addr:addr + len(data)] = data

    def flags(self):
        return (self.n << 7 | self.v << 6 | 0x20 | self.d << 3 | self.i << 2 | self.z << 1 | self.c)

    def set_flags(self, p):
        self.n, self.v, self.d = p >> 7 & 1, p >> 6 & 1, p >> 3 & 1
        self.i, self.z, self.c = p >> 2 & 1, p >> 1 & 1, p & 1

    def push(self, v):
        self.mem[0x100 + self.sp] = v & 255
        self.sp = (self.sp - 1) & 255

    def pull(self):
        self.sp = (self.sp + 1) & 255
        return self.mem[0x100 + self.sp]

    def step(self):
        """one instruction"""
        mem = self.mem
        pc = self.pc
        op = mem[pc]
        d = DECODE[op]
        if d is None:
            raise SimError('an undocumented opcode $%02x at $%04x' % (op, pc))
        mn, mode = d
        cyc = CYCLES[op]
        b1 = mem[(pc + 1) & 0xffff]
        # the effective address
        ea = None
        if mode == 'imm':
            pass
        elif mode == 'zp':
            ea = b1
        elif mode == 'zpx':
            ea = (b1 + self.x) & 255
        elif mode == 'zpy':
            ea = (b1 + self.y) & 255
        elif mode in ('abs', 'abx', 'aby', 'ind'):
            base = b1 | mem[(pc + 2) & 0xffff] << 8
            if mode == 'abs':
                ea = base
            elif mode == 'ind':
                ea = mem[base] | mem[(base & 0xff00) | ((base + 1) & 255)] << 8
            else:
                ea = (base + (self.x if mode == 'abx' else self.y)) & 0xffff
                if mn in READS and (ea ^ base) & 0xff00:
                    cyc += 1
        elif mode == 'izx':
            z = (b1 + self.x) & 255
            ea = mem[z] | mem[(z + 1) & 255] << 8
        elif mode == 'izy':
            base = mem[b1] | mem[(b1 + 1) & 255] << 8
            ea = (base + self.y) & 0xffff
            if mn in READS and (ea ^ base) & 0xff00:
                cyc += 1
        npc = (pc + SIZE[mode]) & 0xffff
        swap = None
        machine = self.banked
        if machine is not None and ea is not None and mn not in ('jmp', 'jsr'):
            kind = machine.kind(self, ea)
            if kind != 'ram':
                reads = mn not in ('sta', 'stx', 'sty')
                if kind == 'io':
                    swap = mem[ea]              # the I/O in place of the RAM for this instruction
                    mem[ea] = self.io[ea]
                elif kind == 'none' or reads:
                    raise SimError('%s $%04x (%s, %s) at $%04x' % ('reads' if reads else 'writes', ea,
                                   'no memory there' if kind == 'none' else 'a ROM', machine.state(self), pc))
        m = mem[ea] if (ea is not None and mn not in ('sta', 'stx', 'sty', 'jmp', 'jsr')) else (
            b1 if mode == 'imm' else 0)
        a, x, y = self.a, self.x, self.y

        def nz(v):
            self.n = v >> 7 & 1
            self.z = int(v == 0)
            return v

        if mn == 'lda':
            self.a = nz(m)
        elif mn == 'ldx':
            self.x = nz(m)
        elif mn == 'ldy':
            self.y = nz(m)
        elif mn == 'sta':
            mem[ea] = a
        elif mn == 'stx':
            mem[ea] = x
        elif mn == 'sty':
            mem[ea] = y
        elif mn in ('adc', 'sbc'):
            if self.d:
                raise SimError('decimal mode arithmetic at $%04x' % pc)
            if mn == 'sbc':
                m ^= 255
            r = a + m + self.c
            self.c = r >> 8
            r &= 255
            self.v = ((~(a ^ m)) & (a ^ r)) >> 7 & 1
            self.a = nz(r)
        elif mn == 'and':
            self.a = nz(a & m)
        elif mn == 'ora':
            self.a = nz(a | m)
        elif mn == 'eor':
            self.a = nz(a ^ m)
        elif mn in ('cmp', 'cpx', 'cpy'):
            r = {'cmp': a, 'cpx': x, 'cpy': y}[mn]
            self.c = int(r >= m)
            nz((r - m) & 255)
        elif mn == 'bit':
            self.z = int(a & m == 0)
            self.n = m >> 7 & 1
            self.v = m >> 6 & 1
        elif mn in RMW:
            v = a if mode == 'acc' else m
            if mn == 'asl':
                self.c = v >> 7
                v = (v << 1) & 255
            elif mn == 'lsr':
                self.c = v & 1
                v >>= 1
            elif mn == 'rol':
                v, self.c = ((v << 1) | self.c) & 255, v >> 7
            elif mn == 'ror':
                v, self.c = (v >> 1) | (self.c << 7), v & 1
            elif mn == 'inc':
                v = (v + 1) & 255
            else:
                v = (v - 1) & 255
            nz(v)
            if mode == 'acc':
                self.a = v
            else:
                mem[ea] = v
        elif mode == 'rel':
            take = {'bpl': not self.n, 'bmi': self.n, 'bvc': not self.v, 'bvs': self.v,
                    'bcc': not self.c, 'bcs': self.c, 'bne': not self.z, 'beq': self.z}[mn]
            if take:
                t = (npc + (b1 - 256 if b1 > 127 else b1)) & 0xffff
                cyc += 1 + int((t ^ npc) & 0xff00 != 0)
                npc = t
        elif mn == 'jmp':
            npc = ea
        elif mn == 'jsr':
            ret = (pc + 2) & 0xffff
            self.push(ret >> 8)
            self.push(ret & 255)
            npc = ea
        elif mn == 'rts':
            lo = self.pull()
            npc = ((self.pull() << 8) | lo) + 1 & 0xffff
        elif mn == 'rti':
            self.set_flags(self.pull())
            lo = self.pull()
            npc = (self.pull() << 8) | lo
        elif mn == 'brk':
            ret = (pc + 2) & 0xffff
            self.push(ret >> 8)
            self.push(ret & 255)
            self.push(self.flags() | 0x10)
            self.i = 1
            npc = mem[0xfffe] | mem[0xffff] << 8
        elif mn == 'pha':
            self.push(a)
        elif mn == 'php':
            self.push(self.flags() | 0x10)
        elif mn == 'pla':
            self.a = nz(self.pull())
        elif mn == 'plp':
            self.set_flags(self.pull())
        elif mn in ('inx', 'dex'):
            self.x = nz((x + (1 if mn == 'inx' else -1)) & 255)
        elif mn in ('iny', 'dey'):
            self.y = nz((y + (1 if mn == 'iny' else -1)) & 255)
        elif mn == 'tax':
            self.x = nz(a)
        elif mn == 'tay':
            self.y = nz(a)
        elif mn == 'txa':
            self.a = nz(x)
        elif mn == 'tya':
            self.a = nz(y)
        elif mn == 'tsx':
            self.x = nz(self.sp)
        elif mn == 'txs':
            self.sp = x
        elif mn == 'clc':
            self.c = 0
        elif mn == 'sec':
            self.c = 1
        elif mn == 'cli':
            self.i = 0
        elif mn == 'sei':
            self.i = 1
        elif mn == 'clv':
            self.v = 0
        elif mn == 'cld':
            self.d = 0
        elif mn == 'sed':
            self.d = 1
        # nop: nothing
        if swap is not None:
            self.io[ea] = mem[ea]
            mem[ea] = swap
        if machine is not None and ea is not None and mn in WRITES:
            machine.wrote(self, ea)
        self.pc = npc
        self.cycles += cyc

    def call(self, addr, limit=10 ** 9):
        """run a routine to its rts (a return address of $ffff pushed)"""
        self.push(0xff)
        self.push(0xfe)
        self.pc = addr
        start = self.cycles
        while self.pc != 0xffff:
            self.step()
            if self.cycles - start > limit:
                raise SimError('more than %d cycles' % limit)

    def run_c64(self, stop, limit=10 ** 9, after=0):
        """run until the pc reaches stop (after that many instructions);
        a SimError if code ever runs anywhere but RAM (as the machine in
        self.banked says; without one, as a C64's $01 says)"""
        steps = 0
        start = self.cycles
        mem = self.mem
        machine = self.banked
        while True:
            self.step()
            steps += 1
            pc = self.pc
            if pc == stop and steps > after:
                return
            if machine is not None:
                if machine.kind(self, pc) != 'ram':
                    raise SimError('runs at $%04x (%s)' % (pc, machine.state(self)))
            elif pc >= 0xa000:
                port = mem[1]
                if (pc < 0xc000 and port & 3 == 3) or (pc >= 0xe000 and port & 2) or (
                        0xd000 <= pc < 0xe000 and port & 3):
                    raise SimError('runs at $%04x with $01 = $%02x (a ROM or the I/O there)' % (pc, port))
            if self.cycles - start > limit:
                raise SimError('more than %d cycles' % limit)


# ------------------------------------------------------------ self-test
def selftest():
    src = """
; a sum of the bytes of a table, a multiply, flags
*=$c000
tab     = $c100
n       = 5
sum     lda #0
        tax
        clc
@l      adc tab,x
        inx
        cpx #n
        bne @l
        sta $02
        lda #$50                ; overflow: $50 + $50
        clc
        adc #$50
        php
        pla
        sta $03
        lda #3                  ; 3 * 7 by shift and add
        sta $04
        lda #7
        sta $05
        lda #0
        ldy #8
@m      lsr $04
        bcc @n
        clc
        adc $05
@n      asl $05
        dey
        bne @m
        sta $06
        jsr sub
        rts
sub     ldy #$ff
        iny
        rts
"""
    prg, syms = assemble(src)
    assert prg[:2] == bytes([0x00, 0xc0]), prg[:2]
    c = CPU()
    c.load(0xc000, prg[2:])
    c.load(0xc100, bytes([1, 2, 3, 4, 250]))
    c.call(syms['sum'])
    assert c.mem[2] == 4, c.mem[2]                   # 260 & 255
    assert c.mem[3] & 0xc3 == 0xc0, hex(c.mem[3])     # n, v set; z, c clear
    assert c.mem[6] == 21, c.mem[6]
    assert c.y == 0 and c.z == 1
    # zero page forms, indirect modes, branches across a page
    prg2, s2 = assemble('*=$10fe\nstart lda ($fb),y\n sta ($fb,x)\n jmp (vec)\nvec byte <start,>start\n')
    assert prg2[2:4] == bytes([0xb1, 0xfb]) and prg2[4:6] == bytes([0x81, 0xfb]) and prg2[6] == 0x6c
    print('gg6502: assembler and simulator self-test passed')


if __name__ == '__main__':
    if '--selftest' in sys.argv:
        selftest()
    else:
        print(__doc__)
