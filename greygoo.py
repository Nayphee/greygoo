#!/usr/bin/env python3
"""
greygoo.py - a cruncher for C64 programs and data: LZ77 with an optimal
parse, coded by an adaptive binary range coder (ggcoder.py). It trades
decrunch time for size: on a stock C64 it unpacks several times slower
than the usual crunchers, on an Ultimate 64 at 48 MHz in a blink.

The format (version 5)
----------------------
The output length is known to the decoder (its stub). The stream is one
range-coded bit sequence. The decoder keeps the output so far, the last
offset (1 at the start) and the kinds of the last two tokens (literal L,
match M, repeat R, near N), which select the token contexts.

  every token:
    isM[hist]           0: a literal, 1: a match
    literal: 8 bits through a bit tree; after a match or repeat, while
             its bits agree with the byte at the last offset back, each
             bit also by that byte's bit
    isRep[hist]         0: a new match, 1: a repeat or a near byte
    new match: length L >= 2 as n = L - 1 by the length code 'len';
               offset o >= 1 by the offset code, class min(e_n, 2)
               (e_n: the length's bit length - 1)
    isNear[hist]        0: a repeat, 1: a near byte
    repeat: length L >= 1 by the length code 'rlen'
    near byte: one byte from o back, o = 1..15 as 4 bits through the
               offset slot tree of class 3; o becomes the last offset
    delta match (only in streams packed with delta=True; the decoder must
               have it): the near byte's value 0, then a length and an
               offset exactly as a new match's, then a delta d = 1..255
               as 8 bits through a bit tree: each byte is the byte o back
               plus d (so a copy that overlaps itself makes a ramp). Its
               kind is the near byte's. A stream without one is a
               version 4 stream

  length code (n >= 1): e = n's bit length - 1 in unary (e ones and a
  zero), each bit adaptive by its place; then the mantissa of n.
  offset code (o >= 1): e = o's bit length - 1 through a 4-bit tree by
  class; then the mantissa of o.
  mantissa: the e bits below the top 1, MSB first, each coded inverted,
  as the decoder builds the value: into its complement m (~1, then
  m = 2m + the coded bit). While m's low byte is $f8 or more (the value
  below 8, the first three bits, and again for a few bits of long values)
  a bit is adaptive by (e, m & 7); otherwise at 1/2.

Everything is written from scratch for greygoo: no code from other
crunchers.

usage: python3 greygoo.py in.bin out.gg   (raw: 2-byte length, stream)
       python3 greygoo.py -d in.gg out.bin

Copyright (c) 2026 Nathan Butcher, driven by Claude Opus 5.5. zlib licence: see LICENSE.
"""
import math, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ggcoder as G

FORMAT = 5              # the stream format's version (4 with delta matches; a stream without one is 4)
NREP = 1                # repeat offsets kept (the format: 1)
MAXLEN = 65535
KINDS = {'L': 0, 'M': 1, 'R': 2, 'N': 3}
NEAR = 15               # a near byte's offsets: 1..NEAR (4 bits, 0 unused)
NEARCLS = 3             # its slot tree's class


def offclass(L, e):
    """a new match's offset class, by its length L (e: n = L - 1's bit
    length - 1): min(e, 2)"""
    return min(e, 2)


# ------------------------------------------------------------------ the codec
class Codec:
    """one token at a time through a Model: encode when the model's coder is
    an Encoder, decode when it is a Decoder; the state (output, reps,
    history) is kept here, identically for both"""

    def __init__(self, model):
        self.m = model
        self.out = bytearray()
        self.reps = [1, 2, 3][:NREP]
        self.hist = (0, 0)
        self.deltas = 0         # delta matches coded so far

    def _mant(self, tag, e, v=None):
        """the e bits of v below its top 1 (or decoded): see the format"""
        m = self.m
        c_ = 0xfffe
        for k in range(e):
            c = None if v is None else 1 - ((v >> (e - 1 - k)) & 1)
            if (c_ & 255) >= 0xf8:
                c = m.bit((tag, e, c_ & 7), c)
            else:
                c = m.direct(c, 1)
            c_ = (2 * c_ + c) & 0xffff
        return ~c_ & 0xffff

    def _length(self, ctx, n=None):
        m = self.m
        if n is not None:
            e = n.bit_length() - 1
            for i in range(e):
                m.bit((ctx, 'u', i), 1)
            m.bit((ctx, 'u', e), 0)
        else:
            e = 0
            while m.bit((ctx, 'u', e)) == 1:
                e += 1
        self.elen = e
        return self._mant((ctx, 'm'), e, n)

    def _offset(self, cls, o=None):
        e = self.m.tree(('os', cls), None if o is None else o.bit_length() - 1, 4)
        return self._mant('om', e, o)

    def _literal(self, c=None):
        m, out = self.m, self.out
        pos = len(out)
        lctx = 0
        node = 1
        matched = self.hist[1] != 0 and pos >= self.reps[0]
        mb = out[pos - self.reps[0]] if matched else 0
        for i in range(7, -1, -1):
            b = None if c is None else (c >> i) & 1
            if matched:
                mbit = (mb >> i) & 1
                b = m.bit(('ml', lctx, mbit, node), b)
                matched = b == mbit
            else:
                b = m.bit(('lit', lctx, node), b)
            node = node << 1 | b
        out.append(node & 255)
        self.hist = (self.hist[1], 0)

    def token(self, tok=None):
        """code tok (('L', c), ('M', length, offset), ('R', length, index), ('N', offset),
        ('D', length, offset, delta)),
        or decode one token when tok is None; returns the token"""
        m, out = self.m, self.out
        h = self.hist
        isM = m.bit(('isM', h), None if tok is None else int(tok[0] != 'L'))
        if isM == 0:
            self._literal(None if tok is None else tok[1])
            return ('L', out[-1])
        isRep = m.bit(('isRep', h), None if tok is None else int(tok[0] in 'RND'))
        isNear = 0 if isRep == 0 else m.bit(('isNear', h), None if tok is None else int(tok[0] in 'ND'))
        if isNear:
            o = m.tree(('os', NEARCLS), None if tok is None else (0 if tok[0] == 'D' else tok[1]), 4)
            if o == 0:
                # a delta match: a new match's length and offset, then the delta
                L = self._length('len', None if tok is None else tok[1] - 1) + 1
                o = self._offset(offclass(L, self.elen), None if tok is None else tok[2])
                d = m.tree('dl', None if tok is None else tok[3], 8)
                self.reps = [o] + self.reps[:NREP - 1]
                for _ in range(L):
                    out.append((out[-o] + d) & 255)
                self.hist = (h[1], 3)
                self.deltas += 1
                return ('D', L, o, d)
            self.reps = [o] + self.reps[:NREP - 1]
            out.append(out[-o])
            self.hist = (h[1], 3)
            return ('N', o)
        if isRep == 0:
            L = self._length('len', None if tok is None else tok[1] - 1) + 1
            o = self._offset(offclass(L, self.elen), None if tok is None else tok[2])
            self.reps = [o] + self.reps[:NREP - 1]
            kind = 1
        else:
            if NREP == 1:
                j = 0
            elif tok is None:
                j = 0 if m.bit(('rep0', h)) == 1 else (1 if NREP == 2 or m.bit(('repg', h)) == 1 else 2)
            else:
                j = tok[2]
                m.bit(('rep0', h), int(j == 0))
                if j and NREP > 2:
                    m.bit(('repg', h), int(j == 1))
            L = self._length('rlen', None if tok is None else tok[1])
            o = self.reps[j]
            self.reps = [o] + self.reps[:j] + self.reps[j + 1:]
            kind = 2
        for _ in range(L):
            out.append(out[-o])
        self.hist = (h[1], kind)
        return ('M', L, o) if kind == 1 else ('R', L, j)


def encode(data, tokens, events=None):
    """the stream for tokens; events (a list) gets (position, context, bit)
    for every adaptive decision"""
    enc = G.Encoder()
    m = G.Model(enc)
    cod = Codec(m)
    if events is not None:
        orig_bit = m.bit
        def recording(ctx, b=None):
            r = orig_bit(ctx, b)
            events.append((len(cod.out), ctx, r))
            return r
        m.bit = recording
    for t in tokens:
        cod.token(t)
    assert bytes(cod.out) == data, 'the tokens do not rebuild the data'
    return enc.finish()


def decode(stream, n):
    dec = G.Decoder(stream)
    cod = Codec(G.Model(dec))
    while len(cod.out) < n:
        cod.token()
    return bytes(cod.out[:n])


def has_delta(stream, n):
    """whether the stream uses delta matches (and so needs a decoder with them)"""
    dec = G.Decoder(stream)
    cod = Codec(G.Model(dec))
    while len(cod.out) < n:
        cod.token()
    return cod.deltas > 0


def fetched(shifts):
    """stream bytes the 6502 decoder has read after this many normalising
    shifts: the first two (in its start table), then one every 8 shifts,
    the first at the first shift"""
    return 2 + (shifts + 7) // 8


def trace(stream, n):
    """decode as the 6502 does and measure it for unpacking in place:
    (data, excess, total): each output byte k is written after its token
    is decoded, when fetched(shifts) stream bytes have been read (the
    6502 normalises at the start of a decision, so the token's last
    decision's shifts are still to come); excess
    is the largest k - fetched there (the stream must start more than
    that far past the output's start: see ggsfx.py), total the stream
    bytes read in all (the stream is padded with zeros to that)"""
    dec = G.Decoder(stream)
    cod = Codec(G.Model(dec))
    excess = -1 << 30
    while len(cod.out) < n:
        cod.token()
        k = len(cod.out) - 1
        excess = max(excess, k - fetched(dec.nshift - dec.last))
    return bytes(cod.out[:n]), excess, fetched(dec.nshift - dec.last)


# ------------------------------------------------------------------ prices
class Prices:
    """static bit prices from counts of an earlier encode (or neutral
    guesses): price(ctx, b) = -log2 of the smoothed probability; with
    prior (the whole file's counts) and weight a, local counts are mixed
    with it: p = (n0 + a * p_prior) / (n + a)"""

    def __init__(self, counts=None, prior=None, a=0.0):
        self.c = counts or {}
        self.prior = prior
        self.a = a
        self.cache = {}

    def bit(self, ctx, b):
        key = (ctx, b)
        v = self.cache.get(key)
        if v is None:
            if self.prior is None:
                n0, n1 = self.c.get(ctx, (0, 0))
                p = (n0 + 0.4) / (n0 + n1 + 0.8)
            else:
                g0, g1 = self.prior.get(ctx, (0, 0))
                pg = (g0 + 0.4) / (g0 + g1 + 0.8)
                n0, n1 = self.c.get(ctx, (0, 0))
                p = (n0 + self.a * pg) / (n0 + n1 + self.a)
            v = -math.log2(p if b == 0 else 1 - p)
            self.cache[key] = v
        return v

    def mant(self, tag, e, v):
        key = ('Mn', tag, e, v)
        r = self.cache.get(key)
        if r is None:
            r, c_ = 0.0, 0xfffe
            for k in range(e):
                c = 1 - ((v >> (e - 1 - k)) & 1)
                if (c_ & 255) >= 0xf8:
                    r += self.bit((tag, e, c_ & 7), c)
                else:
                    r += 1
                c_ = (2 * c_ + c) & 0xffff
            self.cache[key] = r
        return r

    def length(self, ctx, n):
        key = ('L', ctx, n)
        v = self.cache.get(key)
        if v is None:
            e = n.bit_length() - 1
            v = sum(self.bit((ctx, 'u', i), 1) for i in range(e)) + self.bit((ctx, 'u', e), 0)
            v += self.mant((ctx, 'm'), e, n)
            self.cache[key] = v
        return v

    def offset(self, cls, o):
        key = ('O', cls, o)
        v = self.cache.get(key)
        if v is None:
            e = o.bit_length() - 1
            v = 0.0
            node = 1
            for i in range(3, -1, -1):
                b = (e >> i) & 1
                v += self.bit((('os', cls), node), b)
                node = node << 1 | b
            v += self.mant('om', e, o)
            self.cache[key] = v
        return v

    def dtree(self, d):
        key = ('DT', d)
        v = self.cache.get(key)
        if v is None:
            v, node = 0.0, 1
            for i in range(7, -1, -1):
                b = (d >> i) & 1
                v += self.bit(('dl', node), b)
                node = node << 1 | b
            self.cache[key] = v
        return v

    def near(self, o):
        key = ('N', o)
        v = self.cache.get(key)
        if v is None:
            v, node = 0.0, 1
            for i in range(3, -1, -1):
                b = (o >> i) & 1
                v += self.bit((('os', NEARCLS), node), b)
                node = node << 1 | b
            self.cache[key] = v
        return v

    def literal(self, prev, c, mb):
        """mb: the match byte, or None for a plain literal"""
        lctx = 0
        key = ('C', lctx, c, mb)
        v = self.cache.get(key)
        if v is None:
            v, node, matched = 0.0, 1, mb is not None
            for i in range(7, -1, -1):
                b = (c >> i) & 1
                if matched:
                    mbit = (mb >> i) & 1
                    v += self.bit(('ml', lctx, mbit, node), b)
                    matched = b == mbit
                else:
                    v += self.bit(('lit', lctx, node), b)
                node = node << 1 | b
            self.cache[key] = v
        return v


# ------------------------------------------------------------------ matches
def find_matches(data, chain_limit=256):
    """for each position, the matches worth having: (length, offset) with
    length rising as offset rises (the nearest offset for each length);
    lengths >= 2"""
    n = len(data)
    head = {}
    prev = [-1] * n
    res = [None] * n
    for i in range(n):
        cands = []
        if i + 1 < n:
            key = data[i] << 8 | data[i + 1]
            j = head.get(key, -1)
            best = 1
            steps = 0
            maxl = min(MAXLEN, n - i)
            while j >= 0 and steps < chain_limit:
                # extend: the first best+1 bytes must agree to beat best
                if data[j + best] == data[i + best] if best < maxl else False:
                    l = 2
                    # fast compare in chunks
                    while l < maxl:
                        k = min(64, maxl - l)
                        if data[j + l:j + l + k] == data[i + l:i + l + k]:
                            l += k
                        else:
                            while data[j + l] == data[i + l]:
                                l += 1
                            break
                    if l > best:
                        best = l
                        cands.append((l, i - j))
                        if l >= maxl:
                            break
                j = prev[j]
                steps += 1
            prev[i] = head.get(key, -1)
            head[key] = i
        res[i] = cands
    return res


def rep_length(data, i, r, maxl):
    if r > i:
        return 0
    l = 0
    while l < maxl:
        k = min(64, maxl - l)
        if data[i + l:i + l + k] == data[i - r + l:i - r + l + k]:
            l += k
        else:
            while data[i + l] == data[i - r + l]:
                l += 1
            break
    return l


def lengths_to_try(lo, hi):
    """every length up to 32, then a thinning set, and hi itself"""
    L = lo
    while L <= hi:
        yield L
        if L < 32:
            L += 1
        else:
            L += max(1, L >> 3)
    if hi > 32 and (hi - lo) >= 0:
        yield hi


# ------------------------------------------------------------------ the parse
def parse(data, prices, K=4, matches=None, shift=30, dmatches=None):
    """the cheapest token sequence under prices (one Prices, or a list of
    them by position >> shift): a forward shortest path whose states
    carry the reps and the last two kinds (the K cheapest distinct states
    a position)"""
    n = len(data)
    if matches is None:
        matches = find_matches(data)
    plist = prices if isinstance(prices, list) else [prices] * ((n >> shift) + 1)
    P = plist[0]
    INF = float('inf')
    # a state: (cost, hist, reps, back) with back = (prev pos, prev state, token)
    states = [dict() for _ in range(n + 1)]     # key (hist, reps) -> [cost, back]

    def relax(j, cost, hist, reps, back):
        d = states[j]
        key = (hist, reps)
        cur = d.get(key)
        if cur is None or cost < cur[0]:
            d[key] = [cost, back]

    # position 0: the first literal (its isM coded as every token's)
    relax(1, P.bit(('isM', (0, 0)), 0) + P.literal(0, data[0], None), (0, 0), (1, 2, 3)[:NREP], None)
    for i in range(1, n):
        d = states[i]
        if not d:
            continue
        P = plist[i >> shift]
        # keep the K cheapest
        items = sorted(d.items(), key=lambda kv: kv[1][0])[:K]
        states[i] = dict(items)
        maxl = min(MAXLEN, n - i)
        prev = data[i - 1]
        best_new = None
        for (hist, reps), (cost, back) in items:
            here = ((hist, reps), i)
            # literal
            mb = data[i - reps[0]] if hist[1] != 0 and i >= reps[0] else None
            c = cost + P.bit(('isM', hist), 0) + P.literal(prev, data[i], mb)
            relax(i + 1, c, (hist[1], 0), reps, (here, ('L', data[i])))
            # repeats
            base = cost + P.bit(('isM', hist), 1) + P.bit(('isRep', hist), 1)
            for j in range(NREP):
                r = reps[j]
                if r > i:
                    continue
                if j and r in reps[:j]:
                    continue
                lr = rep_length(data, i, r, maxl)
                if lr == 0:
                    continue
                if NREP == 1:
                    sel = 0.0
                elif j == 0:
                    sel = P.bit(('rep0', hist), 1)
                else:
                    sel = P.bit(('rep0', hist), 0) + (P.bit(('repg', hist), 1 if j == 1 else 0) if NREP > 2 else 0.0)
                nreps = (r,) + reps[:j] + reps[j + 1:]
                rbase = base + sel + P.bit(('isNear', hist), 0)
                for L in lengths_to_try(1, lr):
                    relax(i + L, rbase + P.length('rlen', L), (hist[1], 2), nreps,
                          (here, ('R', L, j)))
            # near bytes: every offset that is not the last one (each
            # leaves a different last offset)
            for o in range(1, min(NEAR, i) + 1):
                if data[i - o] == data[i] and o != reps[0]:
                    relax(i + 1, base + P.bit(('isNear', hist), 1) + P.near(o), (hist[1], 3),
                          (o,) + reps[:NREP - 1], (here, ('N', o)))
            # delta matches (dmatches: matches of the byte differences, one
            # byte longer as bytes): the bytes at the offset plus a constant
            if dmatches is not None and dmatches[i]:
                sd = base + P.bit(('isNear', hist), 1) + P.near(0)
                lo_ = 2
                for (lc, off) in dmatches[i]:
                    if off > i:
                        continue
                    d = (data[i] - data[i - off]) & 255
                    if d == 0:
                        continue
                    top = min(lc + 1, maxl)
                    for L in lengths_to_try(lo_, top):
                        relax(i + L, sd + P.length('len', L - 1) + P.offset(offclass(L, (L - 1).bit_length() - 1), off)
                              + P.dtree(d), (hist[1], 3), (off,) + reps[:NREP - 1], (here, ('D', L, off, d)))
                    lo_ = max(lo_, top + 1)
            # the cheapest start for a new match
            s = cost + P.bit(('isM', hist), 1) + P.bit(('isRep', hist), 0)
            if best_new is None or s < best_new[0]:
                best_new = (s, hist, reps, here)
        # new matches from the cheapest start
        if best_new is not None and matches[i]:
            s, hist, reps, here = best_new
            lo = 2
            for (lc, off) in matches[i]:
                nreps = (off,) + reps[:NREP - 1]
                for L in lengths_to_try(lo, lc):
                    cls = offclass(L, (L - 1).bit_length() - 1)
                    relax(i + L, s + P.length('len', L - 1) + P.offset(cls, off), (hist[1], 1), nreps,
                          (here, ('M', L, off)))
                lo = lc + 1
    # the cheapest final state
    final = states[n]
    key = min(final, key=lambda k: final[k][0])
    toks = []
    cur = (key, n)
    while True:
        k, pos = cur
        entry = states[pos][k]
        back = entry[1]
        if back is None:
            toks.append(('L', data[0]))
            break
        prevkey, tok = back
        toks.append(tok)
        cur = prevkey
    toks.reverse()
    return toks, final[key][0]


def counts(events):
    c = {}
    for pos, ctx, b in events:
        e = c.get(ctx)
        if e is None:
            c[ctx] = e = [0, 0]
        e[b] += 1
    return c


def local_prices(n, events, shift, window, a):
    """prices by block of 2^shift positions: the counts of the block and
    window blocks either side, over the whole file's as a prior"""
    nb = (n >> shift) + 1
    glob = counts(events)
    blk = [dict() for _ in range(nb)]
    for pos, ctx, b in events:
        d = blk[min(pos >> shift, nb - 1)]
        e = d.get(ctx)
        if e is None:
            d[ctx] = e = [0, 0]
        e[b] += 1
    out = []
    for j in range(nb):
        loc = {}
        for k in range(max(0, j - window), min(nb, j + window + 1)):
            for ctx, (c0, c1) in blk[k].items():
                e = loc.get(ctx)
                if e is None:
                    loc[ctx] = e = [0, 0]
                e[0] += c0
                e[1] += c1
        out.append(Prices(loc, glob, a))
    return out


SHIFT, WINDOW, PRIOR = 11, 1, 16.0


def crunch(data, passes=int(os.environ.get('GG_PASSES', '6')), K=int(os.environ.get('GG_K', '8')), verbose=False,
           max_offset=None, max_length=None, delta=False):
    """parse and encode, pricing each parse by the statistics of the last
    encode: the first by neutral guesses, the second by the whole file's
    counts, the rest by local counts (blocks of 2^SHIFT bytes); the
    smallest stream wins; max_offset, max_length: limits on new matches
    (and on repeats' lengths); delta: allow delta matches (the decoder
    must have them: has_delta says whether the stream came to use any)"""
    global MAXLEN
    keep = MAXLEN
    if max_length is not None:
        MAXLEN = max(2, min(MAXLEN, max_length))
    try:
        return _crunch(data, passes, K, verbose, max_offset, delta)
    finally:
        MAXLEN = keep


def _crunch(data, passes, K, verbose, max_offset, delta=False):
    matches = find_matches(data)
    if max_offset is not None:
        matches = [[(l, o) for l, o in c if o <= max_offset] for c in matches]
    dmatches = None
    if delta and len(data) > 3:
        # data[i..i+L] = data[i-o..] + d  <=>  the differences match from i for L
        diff = bytes((data[i + 1] - data[i]) & 255 for i in range(len(data) - 1))
        dm = find_matches(diff)
        if max_offset is not None:
            dm = [[(l, o) for l, o in c if o <= max_offset] for c in dm]
        dmatches = dm + [[]] * (len(data) + 1 - len(dm))
    prices, shift = Prices(), 30
    best = None
    for p in range(passes):
        toks, est = parse(data, prices, K, matches, shift, dmatches)
        events = []
        stream = encode(data, toks, events)
        if verbose:
            print('  pass %d: %d tokens, estimate %.0f bytes, coded %d bytes' % (p + 1, len(toks), est / 8, len(stream)))
        if best is None or len(stream) < len(best):
            best = stream
        if p == 0:
            prices = Prices(counts(events))
        else:
            prices, shift = local_prices(len(data), events, SHIFT, WINDOW, PRIOR), SHIFT
    return best


def main():
    import argparse
    num = lambda v: int(v[1:], 16) if v.startswith('$') else int(v, 0)
    ap = argparse.ArgumentParser(
        description='greygoo: pack or unpack a raw stream (2-byte length, then the stream, zero padded as far as '
                    'the 6502 decoder reads), or a stream for your own code: --mem (a .prg that loads where it '
                    'unpacks in place) or --level (the same bytes for a loader to feed); see ggasm.py')
    ap.add_argument('-d', action='store_true', help='unpack (a raw stream)')
    ap.add_argument('inp', help='the input: raw bytes, or a .prg with --mem/--level (its load address the '
                                'destination unless --dest)')
    ap.add_argument('out')
    ap.add_argument('--passes', type=int, default=None, help='parse passes (default 6; fewer is quicker)')
    ap.add_argument('--mem', action='store_true', help='a .prg: the destination, the end, the stream, loading '
                                                       'where it unpacks in place (or at --mem-at)')
    ap.add_argument('--mem-at', type=num, default=None, help="the .prg's load address instead of in place")
    ap.add_argument('--level', action='store_true', help='the same bytes without a load address')
    ap.add_argument('--dest', type=num, default=None, help='where it unpacks (default: a .prg\'s load address)')
    ap.add_argument('--delta', action='store_true',
                    help='allow delta matches (copies with a constant added: tables, ramps); the decoder '
                         'must have them (ggasm.py --delta) if the stream comes to use any')
    ap.add_argument('--max-offset', type=num, default=None, help='the longest match offset')
    ap.add_argument('--max-length', type=num, default=None, help='the longest match')
    ap.add_argument('-q', action='store_true', help='quiet')
    a = ap.parse_args()
    if a.d:
        raw = open(a.inp, 'rb').read()
        n = raw[0] | raw[1] << 8
        open(a.out, 'wb').write(decode(raw[2:], n))
        return
    data = open(a.inp, 'rb').read()
    dest = a.dest
    if (a.mem or a.level) and dest is None:
        if len(data) < 3:
            sys.exit('greygoo: give --dest for raw input')
        dest, data = data[0] | data[1] << 8, data[2:]
    if not 1 <= len(data) <= 65535:
        sys.exit('greygoo: the input must be 1 to 65535 bytes')
    kw = {} if a.passes is None else {'passes': a.passes}
    stream = crunch(data, verbose=not a.q, max_offset=a.max_offset, max_length=a.max_length, delta=a.delta, **kw)
    if a.delta and not a.q:
        print('delta matches: %s' % ('used: unpack with a decoder that has them (ggasm.py --delta)'
                                     if has_delta(stream, len(data)) else 'none used: any decoder unpacks it'))
    back, excess, total = trace(stream, len(data))
    assert back == data, 'round trip failed'
    padded = stream + bytes(max(0, total - len(stream)))
    if a.mem or a.level:
        end = dest + len(data)
        if end > 0x10000:
            sys.exit('greygoo: the destination runs past $ffff')
        body = bytes((dest & 255, dest >> 8, end & 255, (end >> 8) & 255)) + padded
        place = dest + excess + 3 - 6           # (the stream from its third byte at dest + excess + 3)
        load = a.mem_at if a.mem_at is not None else place
        if a.mem:
            open(a.out, 'wb').write(bytes((load & 255, load >> 8)) + body)
        else:
            open(a.out, 'wb').write(body)
        if not a.q:
            print('%s: %d -> %d bytes at $%04x-$%04x%s' % (
                a.inp, len(data), len(body), dest, end - 1,
                '; loads at $%04x%s' % (load, ' (in place)' if load == place else '') if a.mem else
                ' (for a loader: --level)'))
        return
    open(a.out, 'wb').write(bytes((len(data) & 255, len(data) >> 8)) + padded)
    if not a.q:
        print('%s: %d -> %d bytes (+2 for the length); to unpack in place, the stream from its '
              'third byte must start %d bytes or more past the output\'s start' % (
                  a.inp, len(data), len(padded), excess + 3))


if __name__ == '__main__':
    main()
