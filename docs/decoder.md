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
    python3 ggasm.py --fast --hook --syntax acme -o ggunpack.a

| option | |
|---|---|
| `--input mem` | (the default) `lda #<packed` / `ldx #>packed` / `jsr ggunpack`: `packed` is where the file loaded (its header) |
| `--input get` | `jsr ggunpack`: it calls your `ggget` for every byte, the header first. `ggget` returns the next byte in A and keeps X and Y |
| `--fast` | the quarter-square decoder: about 1.5 times as fast, about 95 bytes more, 4 more pages of work area |
| `--delta` | with delta matches, for streams packed with `greygoo.py --delta`: 40 bytes more, a zero-page byte and a page of work area more |
| `--zp ADDR` | its zero page: 23 bytes (2 more with `--fast`, 1 more with `--delta`), default `$55` |
| `--work ADDR` | its work area: 6 pages from an even page (4 more with `--fast`, 1 more with `--delta`), default `$c000` |
| `--org ADDR` | where it goes, default `$c800` (about 545 bytes; 95 more with `--fast`, 40 more with `--delta`) |
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

900-1,900 cycles a byte at 1 MHz with the normal decoder, 600-1,250 with
`--fast`, depending on the data (code is slower than text or graphics with
long repeats).
