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


class Model:
    """adaptive probabilities by context key, coded through an Encoder or
    Decoder (or only priced: coder None counts -log2 p)"""

    def __init__(self, coder=None, up=(UP0, UP1), init=128):
        self.p = {}
        self.coder = coder
        self.up0, self.up1 = up
        self.init = init
        self.bits = 0.0
        self.decoding = isinstance(coder, Decoder)

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
                b = self.coder.decode(128)
            elif self.coder is not None:
                self.coder.encode(128, b)
            else:
                self.bits += 1
            out = out << 1 | b
        return out

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
    for trial in range(200):
        n = rng.randint(0, 3000)
        skew = rng.choice((0.5, 0.9, 0.99, 0.02))
        bits = [1 if rng.random() > skew else 0 for _ in range(n)]
        ctxs = [rng.randrange(4) for _ in range(n)]
        enc = Encoder()
        m = Model(enc)
        for c, b in zip(ctxs, bits):
            m.bit(c, b)
        data = enc.finish()
        dec = Decoder(data)
        md = Model(dec)
        got = [md.bit(c) for c in ctxs]
        assert got == bits, (trial, n, skew)
    print('range coder: 200 random streams decode exactly')


if __name__ == '__main__':
    selftest()
