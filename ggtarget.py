#!/usr/bin/env python3
"""
ggtarget.py - the machines greygoo's self-extractors run on: for each, what
memory it has and how the ROMs and I/O are switched (as a simulator memory
model, gg6502's CPU.banked, and for planning a layout), where BASIC programs
start, the zero page the decoder may use, the border, the screen blanking,
the turbo, and how BASIC is RUN.

A machine's memory configuration ("cfg") is the value that selects it: $01
on the C64, the MMU's $ff00 on the C128, 0 (ROMs in) or 1 (RAM) on the
C16 and Plus/4 (switched by writing $ff3e or $ff3f), and 0 on the VIC-20
and the PET, which have nothing to switch.

The facts here are the machines' own (memory maps, registers, ROM entry
points), checked against the emulators in VICE.

Copyright (c) 2026 Nathan Butcher, driven by Claude Opus 5.5. zlib licence: see LICENSE.
"""


class Machine:
    """a memory model: kind(cfg, address) is 'ram', 'rom', 'io' or 'none'
    (nothing there); for gg6502 the cfg comes from the running CPU"""

    def kind_cfg(self, cfg, a):
        raise NotImplementedError

    def cfg(self, cpu):
        return 0

    def kind(self, cpu, a):
        return self.kind_cfg(self.cfg(cpu), a)

    def wrote(self, cpu, a):
        pass

    def state(self, cpu):
        return 'cfg $%02x' % self.cfg(cpu)

    def reset(self, cpu, cfg):
        pass


class C64(Machine):
    def kind_cfg(self, port, a):
        if a < 0xa000 or 0xc000 <= a < 0xd000:
            return 'ram'
        if a < 0xc000:
            return 'rom' if port & 3 == 3 else 'ram'
        if a < 0xe000:
            if port & 3 == 0:
                return 'ram'
            return 'io' if port & 4 else 'rom'
        return 'rom' if port & 2 else 'ram'

    def cfg(self, cpu):
        return cpu.mem[1]

    def state(self, cpu):
        return '$01 = $%02x' % cpu.mem[1]

    def reset(self, cpu, cfg):
        cpu.mem[0], cpu.mem[1] = 0x2f, cfg


class C128(Machine):
    """bank 0, the MMU's configuration register at $ff00 ($ff00-$ff04 are
    the MMU in every configuration)"""

    def kind_cfg(self, m, a):
        if a < 0x4000:
            return 'ram'
        if 0xff00 <= a <= 0xff04:
            return 'io'
        if a < 0x8000:
            return 'ram' if m & 2 else 'rom'
        if a < 0xc000:
            return 'ram' if m >> 2 & 3 == 3 else 'rom'
        if 0xd000 <= a < 0xe000 and not m & 1:
            return 'io'
        return 'ram' if m >> 4 & 3 == 3 else 'rom'

    def cfg(self, cpu):
        return cpu.io[0xff00]

    def state(self, cpu):
        return '$ff00 = $%02x' % cpu.io[0xff00]

    def reset(self, cpu, cfg):
        cpu.io[0xff00] = cfg


class TED(Machine):
    """the C16 (16 KB) and the Plus/4 (64 KB): the ROMs from $8000 in or out
    by a write to $ff3e or $ff3f; $fd00-$ff3f is always I/O"""

    def __init__(self, ram_top):
        self.ram_top = ram_top
        self.rom = True

    def kind_cfg(self, cfg, a):
        if 0xfd00 <= a < 0xff40:
            return 'io'
        if a < self.ram_top and a < 0x8000:
            return 'ram'
        if a >= 0x8000:
            if cfg == 0:
                return 'rom'
            return 'ram' if a < self.ram_top else 'none'
        return 'none'

    def cfg(self, cpu):
        return 0 if self.rom else 1

    def wrote(self, cpu, a):
        if a == 0xff3e:
            self.rom = True
        elif a == 0xff3f:
            self.rom = False

    def state(self, cpu):
        return 'the ROMs %s' % ('in' if self.rom else 'out')

    def reset(self, cpu, cfg):
        self.rom = cfg == 0


class VIC20(Machine):
    """RAM blocks as fitted; $8000-$8fff character ROM, $9000-$9fff I/O
    (with the colour RAM), $c000-$ffff BASIC and KERNAL"""

    def __init__(self, blocks):
        self.blocks = blocks

    def kind_cfg(self, cfg, a):
        for lo, hi in self.blocks:
            if lo <= a < hi:
                return 'ram'
        if 0x8000 <= a < 0x9000 or a >= 0xc000:
            return 'rom'
        if 0x9000 <= a < 0xa000:
            return 'io'
        return 'none'


class PET(Machine):
    """a 32 KB PET 4032: RAM to $7fff, the screen at $8000, BASIC 4 and the
    KERNAL from $b000, the I/O at $e800"""

    def kind_cfg(self, cfg, a):
        if a < 0x8000 or 0x8000 <= a < 0x8800:
            return 'ram'
        if 0xe800 <= a < 0xf000:
            return 'io'
        if a >= 0xb000:
            return 'rom'
        return 'none'


class Mode:
    """a memory configuration the decoder may unpack under: its level (the
    least wins), cfg, whether interrupts must be off, how to enter it (the
    accumulator free), and, when it hides the I/O, how the page hook lets
    the I/O in (io_cfg) and out again"""

    def __init__(self, level, cfg, sei, enter, io_cfg=None, io_in='', io_out='', what=''):
        self.level, self.cfg, self.sei, self.enter = level, cfg, sei, enter
        self.io_cfg, self.io_in, self.io_out, self.what = io_cfg, io_in, io_out, what


def _store(reg_asm):
    return reg_asm


class Target:
    """everything ggsfx.py needs to know about one machine"""

    def __init__(self, key, name, machine, basic_start, modes, default_mem, zp, low, screen, system,
                 load_limit, flash, blank, turbo, basic_run, io_init, exit_store, kernal_after,
                 mover=(0x0100, 0x01c0), aliases=(), vice=None, notes='', clock=985248, link_trick=False, zp_budget=30,
                 load_end=(0x2d, 0x2f, 0x31), screen_fill=None):
        self.key, self.name, self.machine = key, name, machine
        self.basic_start, self.modes, self.default_mem = basic_start, modes, default_mem
        self.zp, self.low, self.screen, self.system = zp, low, screen, system
        self.load_limit, self.flash, self.blank, self.turbo = load_limit, flash, blank, turbo
        self.basic_run, self.io_init = basic_run, io_init
        self.exit_store = exit_store            # (mem) -> (asm storing A, its bytes for a trampoline)
        self.kernal_after = kernal_after        # (mem) -> the KERNAL's interrupt handler is visible
        self.mover, self.aliases, self.vice, self.notes = mover, aliases, vice, notes
        self.clock = clock          # (Hz, PAL, the CPU's normal clock)
        # the BASIC line's link may point at the stub's ldy #0, its 0 the
        # end of the program: BASIC V2 (C64, VIC-20) checks only a link's
        # high byte; BASIC 7 (C128) relinks unless both are 0
        self.link_trick = link_trick
        # the zero page from zp that BASIC uses only as scratch (its
        # floating-point accumulators and temporaries; checked against each
        # machine's zero-page map): the decoder's 23-25 bytes and the exit
        # trampoline when it fits, else the trampoline goes to the stack page
        self.zp_budget = zp_budget
        # the pointers a LOAD (and RUN's CLR) leave at the end of a BASIC
        # program: set to the unpacked program's end when it loads where
        # BASIC programs start, or BASIC's variables land in it on return
        self.load_end = load_end
        # when the screen was borrowed: (address, bytes, the fill: an
        # immediate '#$20' or a variable) for each area to clean up
        self.screen_fill = screen_fill or ((screen[0], 1000, '#$20'),)
        self._maps = {}

    def kinds(self, cfg):
        """the kind of every address under cfg (cached)"""
        k = self._maps.get(cfg)
        if k is None:
            k = self._maps[cfg] = [self.machine.kind_cfg(cfg, a) for a in range(0x10000)]
        return k

    def ram_prefix(self, cfg):
        """prefix sums of non-RAM addresses under cfg, for O(1) range checks"""
        key = ('p', cfg)
        p = self._maps.get(key)
        if p is None:
            k = self.kinds(cfg)
            p = [0] * 0x10001
            for a in range(0x10000):
                p[a + 1] = p[a] + (k[a] != 'ram')
            self._maps[key] = p
        return p

    def all_ram(self, cfg, r):
        p = self.ram_prefix(cfg)
        lo, hi = r
        return lo >= 0 and hi <= 0x10000 and p[hi] - p[lo] == 0

    def ram_anywhere(self):
        """the RAM that exists under some configuration"""
        best = self.kinds(self.modes[-1].cfg)
        out, s = [], None
        for a in range(0x10001):
            r = a < 0x10000 and best[a] == 'ram'
            if r and s is None:
                s = a
            elif not r and s is not None:
                out.append((s, a))
                s = None
        return out


# ------------------------------------------------------------------ the targets
def _c64():
    MODES = []
    MODES.append(Mode(0, 0x37, False, '', what='nothing banked out'))
    MODES.append(Mode(1, 0x36, False, '        lda #$36                ; BASIC out (the KERNAL stays: interrupts\n'
                                      '        sta $01                 ; and RESTORE work as usual)\n', what='BASIC out'))
    MODES.append(Mode(2, 0x35, True, '        lda #$35                ; BASIC and the KERNAL out\n        sta $01\n',
                      what='BASIC and the KERNAL out'))
    MODES.append(Mode(3, 0x34, True, '        lda #$34                ; all RAM: the I/O out too\n        sta $01\n',
                      io_cfg=0x35, io_in='        lda #$35                ; the I/O in\n        sta $01\n',
                      io_out='        lda #$34                ; and out again\n        sta $01\n', what='all RAM'))
    return Target(
        'c64', 'Commodore 64', C64(), 0x0801, MODES, 0x37, zp=0x55, low=0x0400, screen=(0x0400, 0x0800),
        system=(0x0200, 0x0400), load_limit=0xd000,
        flash=dict(reg=0xd020, colours=(11, 12, 15, 12), rmw=None),
        blank=dict(reg=0xd011, keep=0x7f, off=0xef),
        turbo=dict(reg=0xd031, value=0x0f, per_page=True, blank=False,
                   what="the Ultimate 64's turbo register at its top speed"),
        basic_run=dict(txt=0x2b, var=0x2d, high=0x37, calls=(0xa659, 0xa533), jump=0xa7ae),
        io_init={0xd020: 0xf6, 0xd031: 0x00, 0xd011: 0x1b},
        exit_store=lambda mem: ('        sta $01\n', [0x85, 0x01]),
        kernal_after=lambda mem: bool(mem & 2),
        aliases=('64',), vice=dict(machine='c64'), link_trick=True)


def _c128():
    MODES = [
        Mode(0, 0x00, False, '', what='the ROMs in'),
        Mode(1, 0x0e, False, '        lda #$0e                ; RAM at $4000-$bfff (the KERNAL and\n'
                             '        sta $ff00               ; the I/O stay)\n', what='BASIC out'),
        Mode(2, 0x3e, True, '        lda #$3e                ; all RAM but the I/O\n        sta $ff00\n',
             what='BASIC and the KERNAL out'),
        Mode(3, 0x3f, True, '        lda #$3f                ; all RAM\n        sta $ff00\n', io_cfg=0x3e,
             io_in='        lda #$3e                ; the I/O in\n        sta $ff00\n',
             io_out='        lda #$3f                ; and out again\n        sta $ff00\n', what='all RAM'),
    ]
    return Target(
        'c128', 'Commodore 128 (C128 mode, bank 0)', C128(), 0x1c01, MODES, 0x00, zp=0x59, low=0x0400,
        screen=(0x0400, 0x0800), system=(0x0800, 0x1300), load_limit=0xff00,
        flash=dict(reg=0xd020, colours=(11, 12, 15, 12), rmw=None),
        blank=dict(reg=0xd011, keep=0x7f, off=0xef),
        turbo=dict(reg=0xd030, value=0x01, per_page=False, blank=True,
                   what='2 MHz (the 40-column screen blanked)'),
        basic_run=dict(txt=0x2d, var=0x1210, high=0x1212, calls=(0x5ab5, 0x4f4f), jump=0x4af6),
        io_init={0xd020: 0xfd, 0xd030: 0x00, 0xd011: 0x1b},
        exit_store=lambda mem: ('        sta $ff00\n', [0x8d, 0x00, 0xff]),
        kernal_after=lambda mem: (mem >> 4 & 3) != 3,
        aliases=('128',), vice=dict(machine='c128', extra=['-chargfr', 'chargen-325167-02.bin']),
        zp_budget=27,     # ($59-$73: from $74 BASIC 7 keeps AUTO, graphics, DS$ and its stack pointer)
        load_end=(0x1210,))     # (the end of BASIC text; its variables are in bank 1)


def _ted(key, name, ram_top, aliases, vice):
    MODES = [Mode(0, 0, False, '', what='the ROMs in')]
    if ram_top > 0x8000:
        MODES.append(Mode(1, 1, True, '        sta $ff3f               ; the ROMs out\n', what='the ROMs out'))
    return Target(
        key, name, TED(ram_top), 0x1001, MODES, 0, zp=0x55, low=0x0800, screen=(0x0800, 0x1000),
        system=(0x0200, 0x0800), load_limit=min(ram_top, 0xfd00),
        flash=dict(reg=0xff19, colours=(0x11, 0x31, 0x51, 0x31), rmw=None),
        blank=dict(reg=0xff06, keep=0xff, off=0xef),
        turbo=dict(reg=None, per_page=False, blank=True,
                   what='the screen blanked, so the TED clocks the CPU at 1.77 MHz throughout'),
        basic_run=dict(txt=0x2b, var=0x2d, high=0x37, calls=(0x8bbe, 0x8818, 0xf3b5), jump=0x8bdc),
        io_init={0xff19: 0xee, 0xff06: 0x1b},
        exit_store=lambda mem: (('        sta $ff3e\n', [0x8d, 0x3e, 0xff]) if mem == 0
                                else ('        sta $ff3f\n', [0x8d, 0x3f, 0xff])),
        kernal_after=lambda mem: mem == 0,
        aliases=aliases, vice=vice, clock=886724,
        screen_fill=((0x0c00, 1000, '#$20'), (0x0800, 1000, '$053b')))   # (the attributes: the current colour)


VIC_BLOCKS = {          # the fitted RAM: (BASIC start, blocks)
    'vic20': (0x1001, [(0x0000, 0x0400), (0x1000, 0x2000)]),
    'vic20+3k': (0x0401, [(0x0000, 0x2000)]),
    'vic20+8k': (0x1201, [(0x0000, 0x0400), (0x1000, 0x4000)]),
    'vic20+16k': (0x1201, [(0x0000, 0x0400), (0x1000, 0x6000)]),
    'vic20+24k': (0x1201, [(0x0000, 0x0400), (0x1000, 0x8000)]),
    'vic20+32k': (0x1201, [(0x0000, 0x0400), (0x1000, 0x8000), (0xa000, 0xc000)]),
    'vic20+35k': (0x1201, [(0x0000, 0x8000), (0xa000, 0xc000)]),
}
VIC_VICE = {'vic20': 'none', 'vic20+3k': '3k', 'vic20+8k': '8k', 'vic20+16k': '16k', 'vic20+24k': '24k',
            'vic20+32k': '1,2,3,5', 'vic20+35k': 'all'}


def _vic(key):
    start, blocks = VIC_BLOCKS[key]
    expanded = start == 0x1201
    screen = (0x1000, 0x1200) if expanded else (0x1e00, 0x2000)
    return Target(
        key, 'VIC-20' + (' (%s KB more)' % key[6:-1] if '+' in key else ' (unexpanded)'), VIC20(blocks), start,
        [Mode(0, 0, False, '', what='nothing to switch')], 0, zp=0x55, low=0x0400 if start == 0x0401 else 0x1000,
        screen=screen, system=(0x0200, 0x0400), load_limit=max(hi for lo, hi in blocks if lo <= start < hi),
        flash=dict(reg=0x900f, colours=(6, 3, 1, 3), rmw=0xf8),
        blank=dict(reg=0x9002, keep=0xff, off=0x80),
        turbo=None,
        basic_run=dict(txt=0x2b, var=0x2d, high=0x37, calls=(0xc659, 0xc533), jump=0xc7ae),
        io_init={0x900f: 0x1b, 0x9002: 0x96 if not expanded else 0x16},
        exit_store=lambda mem: ('', []),
        kernal_after=lambda mem: True,
        aliases=(), vice=dict(machine='vic20', memory=VIC_VICE[key]), clock=1108405, link_trick=True,
        screen_fill=((screen[0], 22 * 23, '#$20'),))


def _pet():
    return Target(
        'pet', 'PET 4032', PET(), 0x0401, [Mode(0, 0, False, '', what='nothing to switch')], 0, zp=0x54, zp_budget=28,
        low=0x0400, screen=(0x8000, 0x8800), system=(0x0200, 0x0400), load_limit=0x8000,
        flash=None, blank=None, turbo=None,
        basic_run=dict(txt=0x28, var=0x2a, high=0x34, calls=(0xb622, 0xb5f0, 0xb4b6), jump=0xb74a),
        io_init={},
        exit_store=lambda mem: ('', []),
        kernal_after=lambda mem: True,
        aliases=('4032', 'pet4032'), vice=dict(machine='pet', model='4032'), clock=1000000,
        load_end=(0x2a, 0x2c, 0x2e))


def all_targets():
    t = [_c64(), _c128(),
         _ted('plus4', 'Plus/4', 0x10000, ('4', '+4'), dict(machine='plus4')),
         _ted('c16', 'C16 (16 KB)', 0x4000, ('16',), dict(machine='plus4', model='c16')),
         _pet()]
    t += [_vic(k) for k in VIC_BLOCKS]
    return t


TARGETS = {}
for _t in all_targets():
    TARGETS[_t.key] = _t
    for _a in _t.aliases:
        TARGETS[_a] = _t
TARGETS['20'] = TARGETS['vic20']
TARGETS['23'] = TARGETS['vic20+3k']
TARGETS['52'] = TARGETS['vic20+32k']
TARGETS['55'] = TARGETS['vic20+35k']


def target(name):
    t = TARGETS.get(name.lower())
    if t is None:
        raise KeyError('unknown target %r: one of %s' % (name, ', '.join(sorted({v.key for v in TARGETS.values()}))))
    return t
