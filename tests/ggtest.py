#!/usr/bin/env python3
"""ggtest.py: greygoo's self-test (a few minutes).

  1. gg6502's and the range coder's own tests
  2. Python round trips on generated data
  3. the 6502 unpacker (in gg6502's simulator) unpacking generated
     streams in place at odd addresses, against the originals
  4. ggsfx.py on a generated program for each kind of machine (C64 below
     $a000 and banked, C128, Plus/4, VIC-20 with 8 KB, PET), each run in
     the simulator from its SYS to its start
  5. ggsfx.py on merged files with a BASIC RUN, and --desfx back
  6. the decoder from ggasm.py: a --mem stream and a --level stream fed
     through ggget, in the simulator
  7. delta matches: a table-heavy program packed by ggsfx.py (it must
     choose them) and by greygoo.py --delta for the ggasm.py --delta decoder

Copyright (c) 2026 Nathan Butcher, driven by Claude Opus 5.5. zlib licence: see LICENSE.
"""
import os, sys, random, subprocess, tempfile
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
sys.path.insert(0, ROOT)
import greygoo as GG
import ggcoder
import gg6502
import ggsfx
import ggtarget
import ggasm

PY = sys.executable
failures = 0


def report(name, ok, extra=''):
    global failures
    failures += not ok
    print('%-4s %s %s' % ('ok' if ok else 'FAIL', name, extra))


def gen(rng):
    kind = rng.choice(['tiny', 'noise', 'text', 'runs', 'mix'])
    if kind == 'tiny':
        return kind, bytes(rng.randrange(256) for _ in range(rng.randint(1, 8)))
    if kind == 'noise':
        return kind, bytes(rng.randrange(256) for _ in range(rng.randint(1, 1500)))
    if kind == 'text':
        words = [bytes(rng.choice(b'etaoinshrdlu') for _ in range(rng.randint(1, 7))) for _ in range(40)]
        out = bytearray()
        n = rng.randint(50, 5000)
        while len(out) < n:
            out += rng.choice(words) + b' '
        return kind, bytes(out[:n])
    if kind == 'runs':
        out = bytearray()
        n = rng.randint(10, 9000)
        while len(out) < n:
            out += bytes([rng.randrange(256)]) * rng.choice([1, 2, 3, 255, 256, 257, 3000])
        return kind, bytes(out[:n])
    out = bytearray(bytes(rng.randrange(256) for _ in range(rng.randint(1, 40))))
    n = rng.randint(50, 6000)
    while len(out) < n:
        r = rng.random()
        if r < 0.3:
            out += bytes(rng.randrange(256) for _ in range(rng.randint(1, 20)))
        elif r < 0.8:
            a = rng.randrange(len(out))
            for i in range(rng.randint(1, 200)):
                out.append(out[a + i] if a + i < len(out) else 0)
        else:
            out += bytes([rng.randrange(256)]) * rng.randint(2, 400)
    return kind, bytes(out[:n])


def code_like(rng, n):
    body = bytearray()
    while len(body) < n:
        r = rng.random()
        if r < 0.5:
            body += bytes(rng.randrange(256) if i % 3 else rng.choice((0xa9, 0x8d, 0x20, 0x60))
                          for i in range(rng.randint(3, 24)))
        elif len(body) > 64 and r < 0.9:
            a = rng.randrange(len(body) - 32)
            body += body[a:a + rng.randint(4, 32)]
        else:
            body += bytes([rng.randrange(256)]) * rng.randint(1, 60)
    return bytes(body[:n])


def sys_program(rng, start, end):
    """10 SYS<start+12>: an rts there, then code-like filler to end"""
    code = start + 12
    line = bytes([(start + 10) & 255, (start + 10) >> 8, 10, 0, 0x9e]) + str(code).encode() + bytes([0, 0, 0])
    body = line + b'\x60' + code_like(rng, end - start - len(line) - 1)
    return bytes([start & 255, start >> 8]) + body


def unpack_6502(data, stream, out):
    body, place, ini = ggsfx.layout(stream, len(data), out)
    src = ggsfx.decoder_src(0x02, 0xe000, place, 0xf200, ini, out + len(data))
    prg, syms = gg6502.assemble(src)
    c = gg6502.CPU()
    c.load(place, body)
    c.load(prg[0] | prg[1] << 8, prg[2:])
    c.call(syms['entry'])
    return bytes(c.mem[out:out + len(data)]) == data, c.cycles


def sfx(args, files, tmp):
    paths = []
    for i, (spec, data) in enumerate(files):
        p = os.path.join(tmp, 'in%d.bin' % i)
        open(p, 'wb').write(data)
        paths.append(p + spec)
    dst = os.path.join(tmp, 'out.prg')
    r = subprocess.run([PY, os.path.join(ROOT, 'ggsfx.py'), '--passes', '2'] + args + paths + ['-o', dst],
                       capture_output=True, text=True)
    good = r.returncode == 0 and 'checked:' in r.stdout
    return good, dst, (r.stdout.strip().split('\n')[0] if good else (r.stdout + r.stderr).strip())


def main():
    rng = random.Random(2026)
    # 1
    gg6502.selftest()
    ggcoder.selftest()
    # 2
    ok = True
    for i in range(24):
        kind, data = gen(rng)
        s = GG.crunch(data, passes=2)
        if GG.decode(s, len(data)) != data:
            ok = False
            print('  round trip failed: %s, %d bytes' % (kind, len(data)))
    report('Python round trips on 24 generated buffers', ok)
    # 3
    ok = True
    for i in range(8):
        kind, data = gen(rng)
        out = rng.choice([0x0200, 0x02ff, 0x0801, 0x10fe])
        s = GG.crunch(data, passes=2)
        good, cyc = unpack_6502(data, s, out)
        if not good:
            ok = False
            print('  6502 unpack failed: %s, %d bytes at $%04x' % (kind, len(data), out))
    report('the 6502 unpacker on 8 generated streams, in place', ok)
    with tempfile.TemporaryDirectory() as tmp:
        # 4
        for t, end in (('c64', 0x3000), ('c64', 0xa800), ('c128', 0x6000), ('plus4', 0x9000), ('vic20+8k', 0x3000),
                       ('pet', 0x3000)):
            start = ggtarget.target(t).basic_start
            good, dst, text = sfx(['-t', t], [('', sys_program(rng, start, end))], tmp)
            report('ggsfx.py -t %s on a generated program ($%04x-$%04x)' % (t, start, end), good, text)
        # 5
        lines = [(10, bytes([0x99]) + b'"GREYGOO"'), (20, bytes([0x80]))]
        prog, a = bytearray(), 0x0801
        for no, toks in lines:
            nxt = a + 4 + len(toks) + 1
            prog += bytes([nxt & 255, nxt >> 8, no, 0]) + toks + b'\x00'
            a = nxt
        prog += b'\x00\x00'
        main_prg = bytes([1, 8]) + bytes(prog) + code_like(rng, 3000)
        raw = code_like(rng, 2000)
        good, dst, text = sfx(['--start', 'basic'], [('', main_prg), ('@0x3000', raw)], tmp)
        report('ggsfx.py on two merged files with a BASIC RUN', good, text)
        back = os.path.join(tmp, 'back.prg')
        subprocess.run([PY, os.path.join(ROOT, 'ggsfx.py'), '--desfx', dst, back], capture_output=True)
        img = bytearray(0x3000 + len(raw) - 0x0801)
        img[:len(main_prg) - 2] = main_prg[2:]
        img[0x3000 - 0x0801:] = raw
        got = open(back, 'rb').read() if os.path.exists(back) else b''
        report('ggsfx.py --desfx back to the merged image', got == bytes([1, 8]) + bytes(img))
        # 6
        data = bytes(rng.choice(b'level data ') for _ in range(3000)) + code_like(rng, 2000)
        src = os.path.join(tmp, 'lvl.prg')
        open(src, 'wb').write(bytes([0x00, 0x40]) + data)
        for inp in ('mem', 'get'):
            out = os.path.join(tmp, 'lvl.' + inp)
            subprocess.run([PY, os.path.join(ROOT, 'greygoo.py'), '-q', '--passes', '2',
                            '--mem' if inp == 'mem' else '--level', src, out], check=True)
            f = open(out, 'rb').read()
            lib = ggasm.source(inp, False, zp=0x02, work=0xe000, org=0xc000)
            if inp == 'mem':
                load = f[0] | f[1] << 8
                body = f[2:]
                drv = '*=$f800\ndrv     lda #<$%04x\n        ldx #>$%04x\n        jsr ggunpack\n        rts\n' % (
                    load, load)
            else:
                load, body = 0x9000, f
                drv = ('*=$f800\ndrv     jsr ggunpack\n        rts\nggget   lda $%04x\n        inc ggget+1\n'
                       '        bne @g\n        inc ggget+2\n@g      rts\n' % load)
            code, syms = gg6502.assemble(lib + '\n' + drv)
            c = gg6502.CPU()
            c.load(code[0] | code[1] << 8, code[2:])
            c.load(load, body)
            c.call(syms['drv'], limit=10 ** 9)
            report('the decoder from ggasm.py, input %s' % inp, bytes(c.mem[0x4000:0x4000 + len(data)]) == data)
        # 7
        tab = bytearray(bytes(rng.randrange(256) for _ in range(40)))
        while len(tab) < 6000:
            a, d = rng.randrange(len(tab)), rng.randrange(1, 256)
            tab += bytes((tab[a + i] + d) & 255 if a + i < len(tab) else d for i in range(rng.randint(3, 120)))
            tab += bytes((7 + i * 3) & 255 for i in range(rng.randint(2, 80)))
        tab = bytes(tab[:6000])
        line = bytes([0x0b, 0x08, 10, 0, 0x9e]) + b'2061' + bytes([0, 0, 0])
        good, dst, text = sfx([], [('', bytes([1, 8]) + line + b'\x60' + tab)], tmp)
        r = subprocess.run([PY, os.path.join(ROOT, 'ggsfx.py'), '--passes', '2', os.path.join(tmp, 'in0.bin'),
                            os.path.join(tmp, 'out2.prg')], capture_output=True, text=True)
        report('ggsfx.py chooses delta matches for a table-heavy program',
               good and 'with delta matches' in r.stdout, text)
        src = os.path.join(tmp, 'tab.prg')
        open(src, 'wb').write(bytes([0x00, 0x40]) + tab)
        out = os.path.join(tmp, 'tab.mem')
        subprocess.run([PY, os.path.join(ROOT, 'greygoo.py'), '-q', '--passes', '2', '--mem', '--delta', src, out],
                       check=True)
        f = open(out, 'rb').read()
        load = f[0] | f[1] << 8
        st = f[6:]
        lib = ggasm.source('mem', False, zp=0x02, work=0xe000, org=0xc000, delta=True)
        drv = '*=$f800\ndrv     lda #<$%04x\n        ldx #>$%04x\n        jsr ggunpack\n        rts\n' % (load, load)
        code, syms = gg6502.assemble(lib + '\n' + drv)
        c = gg6502.CPU()
        c.load(code[0] | code[1] << 8, code[2:])
        c.load(load, f[2:])
        c.call(syms['drv'], limit=10 ** 9)
        report('the --delta decoder from ggasm.py on a --delta stream (%d bytes from 6000)' % len(st),
               bytes(c.mem[0x4000:0x4000 + len(tab)]) == tab and GG.has_delta(st, len(tab)))
    print('all passed' if not failures else '%d failed' % failures)
    sys.exit(1 if failures else 0)


if __name__ == '__main__':
    main()
