# The greygoo stream format (version 5)

Everything here is what `greygoo.py` and `ggcoder.py` do and what
`ggdec.asm` (and `ggdec-fast.asm`, the same format) decodes, bit for bit. Numbers with `$` are hexadecimal.

## Raw files

Two bytes, low first: the unpacked length n (1 to 65535). Then the
stream, followed by the zero bytes the 6502 decoder reads past its end
(usually none to two). The decoder needs n from outside the stream: there
is no end marker.

## The range decoder

State: a range R and a code C, 24 bits each.

- **Start:** R = `$80ff00`. C = the stream's first three bytes, most
  significant first. Bits past the end of the stream read as 0.
- **A decision** with probability p (P(bit = 0) = p / 256): the bound is
  ((R >> 16) * p) << 8. If C < bound, the bit is 0 and R = bound.
  Otherwise the bit is 1, C = C - bound and R = R - bound. Then, while
  R < `$800000`: R = R << 1, C = (C << 1) | the next stream bit (each
  byte's bits most significant first).
- **Adaptive bits** use a probability kept per context. Every context
  starts at 128. After a 0 bit: p = p + ((256 - p) >> 4). After a 1:
  p = p - (p >> 4). From 128 these stay within 15 to 241.
- **Direct bits** use p = 128 and change nothing.

R's low byte is always 0, so a decoder can keep 16 bits of R and C. The
6502 decoder does, with the code's low byte as a delay line of input bits.

The encoder ends the stream with the shortest byte string whose
continuation by zero bits decodes the same.

## Tokens

State: the output so far; the last offset r0, 1 at the start; the kinds
(L literal, M match, R repeat, N near byte) of the last two tokens, (L, L)
at the start.
"hist" below is that pair. Tokens follow one another until the output
has n bytes.

Each token:

1. **isM**, adaptive, context (isM, hist). 0: a literal. 1: a match, then:
2. **isRep**, adaptive, context (isRep, hist). 0: a new match. 1: a
   repeat or a near byte, then:
3. **isNear**, adaptive, context (isNear, hist). 0: a repeat. 1: a near
   byte.

**A literal** is 8 bits, most significant first, through a binary tree:
the node starts at 1 and becomes node * 2 + bit. If the previous token
was a match, a repeat or a near byte, the literal starts matched. The match byte is
the byte r0 back from the current position. While matched, each bit is
adaptive with context (ml, the match byte's bit at that place, node), and
the literal stays matched while its bits equal the match byte's. After a
literal, or once a bit has differed, bits use context (lit, node).

**A new match:** its length L >= 2 by the length code with context set
`len`, coded as n = L - 1. Then its offset o >= 1 by the offset code with
class min(e, 2), where e = (the bit length of n) - 1. r0 becomes o. Copy L
bytes from o back, one at a time (they may overlap what is being written).

**A repeat:** its length L >= 1 by the length code with context set
`rlen`, coded as n = L. Copy L bytes from r0 back.

**A near byte:** its offset o, 1 to 15, as 4 bits most significant first
through a binary tree with contexts (offset slot class 3, node) (the
offset code's slot trees, a fourth class). r0 becomes o. Copy one byte
from o back. (The encoder never codes the last offset this way: that is a
repeat.)

**A delta match** is the near byte's value 0. A length L >= 2 and an
offset o follow, coded exactly as a new match's (the same contexts). Then
comes a delta d, 1 to 255, as 8 bits most significant first through a
binary tree with contexts (`dl`, node). r0 becomes o. Copy L bytes from o
back, one at a time, adding d to each (modulo 256): a copy that overlaps
what it writes makes a ramp. Its kind in hist is the near byte's (N).

Delta matches are optional. A stream packed without them never codes the
value 0, and is exactly a version 4 stream: the decoder without delta
matches (40 bytes smaller, a page less of work area) unpacks it.
`greygoo.has_delta(stream, n)` tells which a stream needs.

After each token, hist = (hist's second kind, this token's kind).

## Numbers

**The length code** (n >= 1, context set c): e = (the bit length of n) - 1
in unary: e ones, then a zero, the i-th bit adaptive with context
(c, unary, i). Then the mantissa of n, context set (c, mantissa).

**The offset code** (o >= 1, class k): e = (the bit length of o) - 1, as 4
bits most significant first through a binary tree with contexts
(k, node). Then the mantissa of o, context set `om`.

**The mantissa** of a value v with top bit e: its e bits below the top
bit, most significant first, coded as the decoder builds the value. It
keeps m, the complement of the value so far, in 16 bits, starting at
`$fffe` (the complement of 1). For each bit:

- the coded bit c is the value's bit inverted;
- if m's low byte is `$f8` or more, c is adaptive with context
  (the set, e, m AND 7); otherwise c is direct;
- m = (m * 2 + c) AND `$ffff`.

At the end v = m XOR `$ffff`. While the value so far is below 8 (the first
three bits) m's low byte is `$f8` or more. The rule can hold again for a few
bits of values of 256 and more, and those bits are adaptive too: that is
part of the format.

## Context numbering in ggdec.asm

Six 256-byte pages of probabilities, from an even page; a seventh with
delta matches (`ggdec-fast.asm` adds four more after them: its
quarter-square tables, built at start):

| page | contents |
|---|---|
| 0, 1 | matched literals: page = 0 + the match bit, index = node |
| 2 | plain literals, index = node |
| 3 | isM at hist AND 15; `len` unary at 32 + i; `rlen` unary at 48 + i; isNear at 128 + (hist AND 63) (3 mod 4); isRep at 224 + (hist AND 31) (odd) |
| 4 | `len` mantissas at e * 8 + (m AND 7); `rlen` mantissas at 128 + e * 8 + (m AND 7) |
| 5 | offset slot trees at node, starting from node 8 + class (so 8-95; class 3, the near byte's, from node 11); `om` mantissas at 128 + e * 8 + (m AND 7) |
| 6 | (with delta matches) the delta's tree, index = node |

hist keeps two bits a token, shifted in from the right: L = 00, M = 10,
R = 11, N = 01. isRep and isNear are read with hist already shifted by
one 1 bit (by two for isNear), so their indices take in the token so far. Index 128 of pages 4 and 5 is a dummy that direct bits update and
never read.
