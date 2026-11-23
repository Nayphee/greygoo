# Using the decoder in your own program

`ggsfx.py` makes self-extracting programs. To unpack greygoo streams
inside your own program (levels, pictures, music, a second part), pack
them with `greygoo.py --mem` or `--level` and include the decoder that
`ggasm.py` writes.

## The streams

    python3 greygoo.py --mem level1.prg level1.gg       # a .prg: loads where it unpacks in place
    python3 greygoo.py --mem --mem-at 0x9000 level1.prg level1.gg   # loads at $9000 instead
    python3 greygoo.py --mem --dest 0x4000 pic.bin pic.gg            # raw input: say where it goes
    python3 greygoo.py --level level1.prg level1.lvl     # the same bytes, no load address
    python3 greygoo.py --mem --delta tables.prg tables.gg   # allow delta matches

The input is a .prg (its load address is the destination) or, with
`--dest`, raw bytes. Both outputs hold the destination and the end (two
bytes each, low first), then the stream with the zero bytes the decoder
reads past its end. A `--mem` file's load address puts the stream just far
enough above the destination's start that the output never overtakes
unread input: load it with `,8,1` and unpack it where it lies.

`--delta` lets the cruncher use delta matches: copies with a constant
added to each byte, which catch tables repeated at another level and
ramps. They can make a table-heavy file much smaller. A stream that comes
to use any needs the decoder built with `--delta`; `greygoo.py` says
whether it did. Without the option, any decoder unpacks the stream.

## The decoder

    python3 ggasm.py -o ggunpack.asm                       # streams in memory
    python3 ggasm.py --input get -o ggunpack.asm           # bytes from your routine
    python3 ggasm.py --zp '$02' --work '$c000' --org '$c800' -o ggunpack.asm
    python3 ggasm.py --speed 2 --work '$a000' --hook --syntax acme -o ggunpack.a

| option | |
|---|---|
| `--input mem` | (the default) `lda #<packed` / `ldx #>packed` / `jsr ggunpack`: `packed` is where the file loaded (its header) |
| `--input get` | `jsr ggunpack`: it calls your `ggget` for every byte, the header first. `ggget` returns the next byte in A and keeps X and Y |
| `--speed N` | the decoder for a speed level (see below). 0 (default) and 1 unpack the same streams; 2 and 3 need streams packed with `greygoo.py --speed 2` or `--speed 3`, the same level |
| `--delta` | with delta matches, for streams packed with `greygoo.py --delta`: 40 bytes more, a zero-page byte and a page of work area more |
| `--zp ADDR` | its zero page: 23 bytes (25 with `--speed 1`, 21 with `--speed 2` or `3`; 1 more with `--delta`), default `$55` |
| `--work ADDR` | its work area, from an even page: 6 pages (10 with `--speed 1`, 16 with `--speed 2` or `3`; 1 more with `--delta`). Default `$c000`, or `$a000` with `--speed 2` or `3` (RAM under the BASIC ROM: bank it out, or give another address) |
| `--org ADDR` | where it goes, default `$c800` (`$cc00` with `--speed 1`): about 545 bytes; about 100 more with `--speed 1`, 170 with `--speed 2`, 140 with `--speed 3`; 40 more with `--delta` |
| `--hook` | call your `ggpage` each time the output enters a new page: for an effect, or a loader's bookkeeping. Keep Y and the carry; A and X are free |
| `--syntax` | `greygoo` (the default: CBM prg Studio's syntax too), `acme` or `ca65` |

`ggunpack` returns when the destination is full. It uses A, X, Y, its zero
page and work area, and two levels of stack. It changes the operand of
its own input instruction, so it can't run from ROM. It touches no I/O and
uses no decimal mode, so interrupts can stay on (save the zero page in
your handler if it shares any).

The greygoo syntax was checked byte for byte against the c64-assembly
skill's prg Studio assembler. The ACME and ca65 versions are mechanical
translations that have not been assembled here: ACME needs version 0.97
or later (for `@` local labels); ca65 needs a linker configuration that
puts the code at the `.org`.

## In place

When the output and the stream overlap, the stream (from its third byte)
must start a margin past the output's start. `greygoo.py --mem` works the
load address out; for your own placement, `greygoo.trace(stream, n)`
decodes as the 6502 does and returns the largest lead of output bytes
written over stream bytes read: the margin is that plus 3.

## From Python

```python
import greygoo
stream = greygoo.crunch(data)              # the raw stream (delta=True allows delta matches)
assert greygoo.decode(stream, len(data)) == data
greygoo.has_delta(stream, len(data))       # whether it needs the decoder with delta matches
```

`ggsfx.layout` and `ggsfx.decoder_src` assemble a decoder with the
addresses fixed at assembly time, as `ggsfx.py` itself does.

## Speed

Cycles a byte at 1 MHz, depending on the data (code is slower than text
or graphics with long repeats):

| `--speed` | cycles a byte | what changes |
|---|---|---|
| 0 | 900-1,900 | nothing: the smallest stream and decoder |
| 1 | 600-1,250 | the same stream; the decoder multiplies by quarter-square tables (4 more pages) |
| 2 | 310-650 | the table coder: no multiply (10 more pages); the stream about 0.15% bigger |
| 3 | 200-400 | the table coder, with plain literals, mantissas and other poorly compressible bits left uncompressed; the stream about 4% bigger |

Pack with the same level: `greygoo.py --speed 2 --mem level1.prg level1.gg`.
A stream packed for level 2 or 3 only unpacks with that level's decoder.
