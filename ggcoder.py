#!/usr/bin/env python3
"""
ggcoder.py - greygoo's binary range coder, the arithmetic exactly as the
6502 decoder does it.

The coder keeps a 24-bit range R, normalised bit by bit to 2^23 <= R <
2^24, so its top byte R2 = R >> 16 is 128-255. R starts at $80ff00 (a
bit less than it could, which lets the decoder's start-up reuse the $80)
and
every split has a zero low byte, so R's low byte is always 0 (the 6502
keeps 16 bits of range and 16 of code, the code's low byte being only a
delay line of input bits). A decision with P(bit =
0) = p/256 (p = 1..255) splits the range at

    bound = (R2 * p) << 8

(one 8x8 multiply; the split's low byte is always 0, so the decoder
compares and subtracts 16 bits only). Bit 0 takes [0, bound), bit 1
[bound, R). Then R and the code shift left until R >= 2^23, one input
bit at a time.

Probabilities are bytes, P(0) = p/256, adapted after each decision:
p += (256 - p) >> SHIFT after a 0, p -= p >> SHIFT after a 1. From 128
they stay within 15..241 (a step of 0 there), so no clamping: the most
certain bit costs 0.09 bits, the least likely 4.1.

Copyright (c) 2026 Nathan Butcher, driven by Claude Opus 5.5. zlib licence: see LICENSE.
"""

SHIFT = 4
TOP = 1 << 24
HALF = 1 << 23
START = 0x80ff00


def make_tables(shift=SHIFT):
    up0 = [p + ((256 - p) >> shift) for p in range(256)]
    up1 = [p - (p >> shift) for p in range(256)]
    return up0, up1


UP0, UP1 = make_tables()


class Encoder:
    """bits accumulate in low (a Python int): each renormalising shift is
    one output bit; the final value has nbits + 24 bits"""

    def __init__(self):
        self.low = 0
        self.range = START
        self.nshift = 0

    def encode(self, p, bit):
        bound = ((self.range >> 16) * p) << 8
        if bit == 0:
            self.range = bound
        else:
            self.low += bound
            self.range -= bound
        while self.range < HALF:
            self.range <<= 1
            self.low <<= 1
            self.nshift += 1

    def finish(self):
        """the fewest whole bytes that decode the same: the value in the
        final interval [low, low + range) with the most trailing zeros,
        which the decoder reads back as the zeros past the end"""
        nbits = self.nshift + 24
        lo, hi = self.low, self.low + self.range - 1
        k = nbits
        while k > 0 and ((hi >> k) << k) < lo:
            k -= 1
        v = (hi >> k) << k
        total = nbits - k
        v >>= k
        nbytes = (total + 7) // 8
        v <<= nbytes * 8 - total
        return v.to_bytes(nbytes, 'big') if nbytes else b''

    def cost_bits(self):
        return self.nshift


class Decoder:
    def __init__(self, data):
        self.data = data
        self.pos = 0            # bit position
        self.range = START
        self.code = 0
        self.nshift = 0         # normalising shifts so far (a bit each)
        self.last = 0           # of them, the last decision's
        for _ in range(24):
            self.code = self.code << 1 | self.inbit()

    def inbit(self):
        i = self.pos >> 3
        b = (self.data[i] >> (7 - (self.pos & 7))) & 1 if i < len(self.data) else 0
        self.pos += 1
        return b

    def decode(self, p):
        bound = ((self.range >> 16) * p) << 8
        if self.code < bound:
            bit = 0
            self.range = bound
        else:
            bit = 1
            self.code -= bound
            self.range -= bound
        n0 = self.nshift
        while self.range < HALF:
            self.range <<= 1
            self.code = self.code << 1 | self.inbit()
            self.nshift += 1
        self.last = self.nshift - n0
        return bit

    def fetched(self):
        """stream bytes the 6502 decoder has read by now: the first two (in
        its start table), then one every 8 normalising shifts, the first
        at the first shift (it normalises at the start of a decision, so
        the last decision's shifts are still to come)"""
        return 2 + (self.nshift - self.last + 7) // 8


# ------------------------------------------------------------------
# the table coder (ggsfx.py --speed 2 and up): the same decisions with no
# multiply. The range is 8 bits, 128-255 when normalised, and the split
# comes from a table by the range's cell (16 wide: 8 cells) and p:
#
#     split = min(lo - 1, ((lo + 8) * p + 128) >> 8)     lo: the cell's start
#
# (p stays within 15..241, so 0 < split < range). The range and the code
# are normalised before a decision, not after it.
#
# Bypass bits and raw bytes do not go through the coder at all. The
# stream is a sequence of bytes that the decoder takes in order, each as
# it needs it: a byte of bits for its bit buffer (the coder's bits as it
# normalises, and bypass bits, most significant first), or a raw byte.
# So the encoder first codes everything, then lays the stream out by
# replaying what the decoder will ask for. The first byte is the code's
# first 8 bits. The second is the first bit buffer, and holds 7 bits over
# a 1 (the 6502's marker for the buffer's end).
CELL = 16


def split_table():
    """split[cell][p] for cell = (range - 128) // CELL"""
    t = []
    for lo in range(128, 256, CELL):
        t.append([min(lo - 1, ((lo + CELL // 2) * p + 128) >> 8) for p in range(256)])
    return t


SPLIT = split_table()


class TabEncoder:
    def __init__(self):
        self.low = 0
        self.range = 255
        self.nshift = 0         # the coder's bits out (but its first 8)
        self.pending = 0        # of them, the last decision's: the decoder takes them at the next
        self.events = []        # what the decoder asks for, in order: ('c', n) coder bits, ('r', bit), ('b', byte)
        self.raw = 0            # bypass bits and raw bytes' bits

    def encode(self, p, bit):
        if self.pending:
            self.events.append(('c', self.pending))
            self.pending = 0
        bound = SPLIT[(self.range - 128) // CELL][p]
        if bit == 0:
            self.range = bound
        else:
            self.low += bound
            self.range -= bound
        while self.range < 128:
            self.range <<= 1
            self.low <<= 1
            self.nshift += 1
            self.pending += 1

    def bypass(self, bit):
        self.events.append(('r', bit))
        self.raw += 1

    def rawbyte(self, v):
        self.events.append(('b', v))
        self.raw += 8

    def finish(self):
        """the coder's bits (the value in the final interval with the most
        trailing zeros: bits past it read as 0), then the stream by
        replaying the decoder's requests"""
        nbits = self.nshift + 8
        lo, hi = self.low, self.low + self.range - 1
        k = nbits
        while k > 0 and ((hi >> k) << k) < lo:
            k -= 1
        bits = bin(hi >> k)[2:].zfill(nbits - k) if nbits > k else ''
        bits = bits.ljust(nbits, '0')
        out = bytearray([int(bits[:8], 2), 0])
        at = [8]                # the coder's next bit
        box = [1, 0, 7]         # the bit buffer being filled: its place, bits in it, its size

        def put(b):
            if box[1] == box[2]:
                box[0], box[1], box[2] = len(out), 0, 8
                out.append(0)
            out[box[0]] |= b << (7 - box[1])
            box[1] += 1
        for kind, v in self.events:
            if kind == 'c':
                for _ in range(v):
                    put(int(bits[at[0]]))
                    at[0] += 1
            elif kind == 'r':
                put(v)
            else:
                out.append(v)
        out[1] |= 1             # the first buffer's marker
        return bytes(out)

    def cost_bits(self):
        return self.nshift + self.raw


class TabDecoder:
    def __init__(self, data):
        self.data = data
        self.range = 255
        self.code = data[0] if data else 0
        self.pos = 2            # the stream bytes taken so far
        self.buf = (data[1] if len(data) > 1 else 0) >> 1
        self.left = 7
        self.nshift = 0

    def byte(self):
        b = self.data[self.pos] if self.pos < len(self.data) else 0
        self.pos += 1
        return b

    def bypass(self):
        if self.left == 0:
            self.buf, self.left = self.byte(), 8
        self.left -= 1
        return (self.buf >> self.left) & 1

    def rawbyte(self):
        return self.byte()

    def decode(self, p):
        while self.range < 128:
            self.range <<= 1
            self.code = self.code << 1 | self.bypass()
            self.nshift += 1
        bound = SPLIT[(self.range - 128) // CELL][p]
        if self.code < bound:
            self.range = bound
            return 0
        self.code -= bound
        self.range -= bound
        return 1

    def fetched(self):
        return self.pos


def coders(speed=0):
    """(Encoder, Decoder) for a speed level: the table coder from 2 up"""
    return (TabEncoder, TabDecoder) if speed >= 2 else (Encoder, Decoder)


class Model:
    """adaptive probabilities by context key, coded through an Encoder or
    Decoder (or only priced: coder None counts -log2 p)"""

    def __init__(self, coder=None, up=(UP0, UP1), init=128):
        self.p = {}
        self.coder = coder
        self.up0, self.up1 = up
        self.init = init
        self.bits = 0.0
        self.decoding = isinstance(coder, (Decoder, TabDecoder))
        self.raw = hasattr(coder, 'bypass')

    def bit(self, ctx, b=None):
        p = self.p.get(ctx, self.init)
        if self.decoding:
            b = self.coder.decode(p)
        elif self.coder is not None:
            self.coder.encode(p, b)
        else:
            import math
            self.bits -= math.log2(p / 256 if b == 0 else 1 - p / 256)
        self.p[ctx] = self.up0[p] if b == 0 else self.up1[p]
        return b

    def direct(self, v, n):
        """n raw bits, MSB first, at p = 1/2 (not adapted)"""
        out = 0
        for i in range(n - 1, -1, -1):
            b = None if self.decoding else (v >> i) & 1
            if self.decoding:
                b = self.coder.bypass() if self.raw else self.coder.decode(128)
            elif self.coder is not None:
                self.coder.bypass(b) if self.raw else self.coder.encode(128, b)
            else:
                self.bits += 1
            out = out << 1 | b
        return out

    def rawbyte(self, v=None):
        """a byte outside the coder where the coder has them (the table
        coder), else 8 direct bits"""
        if self.coder is not None and not self.raw:
            return self.direct(v, 8)
        if self.decoding:
            return self.coder.rawbyte()
        if self.coder is not None:
            self.coder.rawbyte(v)
        else:
            self.bits += 8
        return v

    def tree(self, ctx, v, n):
        """n bits MSB first through a bit tree (contexts (ctx, node))"""
        node = 1
        for i in range(n - 1, -1, -1):
            b = self.bit((ctx, node), None if self.decoding else (v >> i) & 1)
            node = node << 1 | b
        return node - (1 << n)

    def rtree(self, ctx, v, n):
        """n bits LSB first through a bit tree"""
        node, out = 1, 0
        for i in range(n):
            b = self.bit((ctx, node), None if self.decoding else (v >> i) & 1)
            node = node << 1 | b
            out |= b << i
        return out


def selftest():
    import random
    rng = random.Random(1)
    for E, D in (coders(0), coders(2)):
        for trial in range(200):
            n = rng.randint(0, 3000)
            skew = rng.choice((0.5, 0.9, 0.99, 0.02))
            bits = [1 if rng.random() > skew else 0 for _ in range(n)]
            ctxs = [rng.randrange(5) for _ in range(n)]     # (4: a bypass bit)
            enc = E()
            m = Model(enc)
            for c, b in zip(ctxs, bits):
                m.direct(b, 1) if c == 4 else m.bit(c, b)
                if c == 3 and b:
                    m.rawbyte(n & 255)
            data = enc.finish()
            dec = D(data)
            md = Model(dec)
            got = []
            for c in ctxs:
                got.append(md.direct(None, 1) if c == 4 else md.bit(c))
                if c == 3 and got[-1]:
                    assert md.rawbyte() == n & 255
            assert got == bits, (E.__name__, trial, n, skew)
    print('range coders: 2 x 200 random streams decode exactly')


if __name__ == '__main__':
    selftest()
