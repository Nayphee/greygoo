# greygoo 1.0

A cruncher for Commodore 8-bit programs that trades unpacking time for
size. By Nathan Butcher, driven by Claude Opus 5.5.

Grey goo is a hypothetical global catastrophic scenario involving molecular
nanotechnology in which out-of-control self-replicating machines consume all
biomass (and perhaps also everything else) on Earth while building many more
of themselves, a scenario that has been called ecophagy. A perfect name for
an AI slopped cruncher that packs everything so hard that it destroys your
patience while waiting for the decrunch.

TLDR: It's really just a freak-show curiosity. It beats everything in the
crunched size department (yay!) and everything in the time to decrunch
department (boo!). It's really only useful if storage space is at an extreme
premium, you have a very fast Turbo (SuperCPU, Ultimate 64) or you don't mind
waiting and putting some tea on.

MEGA65 support was not built because I didn't have the ROM available to
drive the emulator for Claude to use.

On the seven released C64 games it was tested on, its self-extracting
files are 7-14% smaller than Exomizer 3.1.2's at its best settings (10.9%
overall). They also unpack 13-15 times more slowly: 35 to 70 seconds on a
stock C64. On an Ultimate 64 or C64 Ultimate with its turbo registers
enabled, the unpacker switches the turbo on while it works; there a 47 KB
game unpacks in under a second once the turbo is running.

It packs for the C64, C128, Plus/4, C16, VIC-20 (unexpanded and with every
common expansion) and PET 4032. Programs can sit anywhere in memory, under
the ROMs and the I/O area too, and several files can be merged into one.
It can also produce streams and a decoder for your own programs.

greygoo is written from scratch in Python and 6502 assembly, with no code
from other crunchers. It needs Python 3 and nothing else: it carries its
own 6502 assembler and simulator.

## When to use it

- **Size comes first:** more on a disk side or a tape, a program into a
  cartridge ROM, a smaller download, a size-limited competition entry.
- **On a fast machine:** an Ultimate 64 at 48-64 MHz, or a MEGA65 or a
  turbo card. There the time hardly matters.
- **Not when a stock machine's waiting time matters.** Use Exomizer
  there: a 51 KB game unpacks in 5.0 s with it, 68 s with greygoo
  (45 s with `--fast`).

## Quick start

    python3 ggsfx.py game.prg game-gg.prg
    python3 ggsfx.py -t c128 game.prg game-gg.prg
    python3 ggsfx.py -o demo-gg.prg main.prg music.bin@0x1000 gfx.prg,0x6000 --start 0x0810

`ggsfx.py` packs a program into a self-extracting one. It then runs the
result in its simulator, from the `SYS` to the program's start, and
compares the unpacked memory with the program. Finally it reports how long
unpacking takes at the machine's normal clock.

| option | |
|---|---|
| `-t TARGET` | `c64` (default), `c128`, `plus4`, `c16`, `vic20`, `vic20+3k`, `vic20+8k`, `vic20+16k`, `vic20+24k`, `vic20+32k`, `vic20+35k`, `pet` (see `docs/targets.md`) |
| inputs | `file.prg`; `file.prg,ADDR[,OFFSET[,LENGTH]]` moves a .prg; `file.bin@ADDR[,OFFSET[,LENGTH]]` loads raw data. Several are merged in order; gaps are filled with zeros |
| `-o FILE` | the output (or give it last) |
| `--start` | `sys` (default: the first file's `SYS` line), `systrim` (the same, with the BASIC line left out), `basic[,START[,END[,HIGH]]]` (set BASIC's pointers and RUN), `bin` (the first file's load address), or an address |
| `--mem VALUE` | the memory configuration for the program: `$01` on the C64 (default `$37`), `$ff00` on the C128 (default `$00`), 0 or 1 on the Plus/4 and C16 |
| `--sei` | start the program with interrupts off |
| `--fast` | the faster decoder: about 1.5 times as fast, 95-120 bytes more, 4 more pages of work RAM |
| `--no-flash` | no border effect while unpacking |
| `--no-turbo` | no turbo (C64: the Ultimate's register; C128: 2 MHz; Plus/4, C16: the blanked screen) |
| `--no-return` | the program never returns to BASIC or calls its routines: about 25 bytes less (see `docs/targets.md`) |
| `--text TEXT` | text after the `SYS` number, shown by `LIST` |
| `--enter`, `--exit`, `--effect` | your own assembly (lines separated by `;`): run first, run before the program starts, run on every output page |
| `--max-offset N`, `--max-length N` | limits on matches |
| `--delta auto\|on\|off` | delta matches (see How it works). `auto` (default) packs both ways and keeps the smaller file; `off` halves the packing time |
| `--hold` | end in a loop instead of starting the program, before the memory configuration goes back: for reading the unpacked memory back from real hardware |
| `--desfx` | unpack a self-extracting file instead (ours or another cruncher's), by running it in the simulator |
| `--passes N` | parse passes (default 6); fewer is quicker and a little bigger |
| `--no-check` | skip the simulator run |

For your own programs:

    python3 greygoo.py --mem level1.prg level1.gg     # a .prg that loads where it unpacks in place
    python3 greygoo.py --level level1.prg level1.lvl  # the same bytes, for a loader to feed
    python3 ggasm.py -o ggunpack.asm                  # the decoder: jsr ggunpack
    python3 greygoo.py data.bin data.gg               # a raw stream (2-byte length first)
    python3 greygoo.py -d data.gg data.bin            # and back

`docs/decoder.md` has the details. The cruncher is Python and thorough:
about 90 s and 260 MB of memory for a 47 KB program (half the time with
`--delta off`).

## Results

Self-extracting C64 files with the defaults (grey border, turbo pokes,
BASIC left as the program's LOAD would). They are compared with Exomizer
3.1.2's `sfx sys` at the best of four settings (default, `-P+16`,
`-M256`, both). Unpacking times are at 1 MHz, from the cycle-counting
simulator, and every file was checked there byte for byte.

| program | size | Exomizer | greygoo | change |
|---|---|---|---|---|
| *Asteroids* 1.1 | 50,175 | 17,462 (3.7 s) | 15,907 (47.6 s) | -8.9% |
| *Asteroids Deluxe* 1.1 | 50,175 | 24,754 (4.7 s) | 22,362 (66.4 s) | -9.7% |
| *Claw Machine Simulator* 1.0 | 22,462 | 15,015 (2.7 s) | 13,941 (40.5 s) | -7.2% |
| *International Table Soccer* 1.0 | 36,360 | 12,954 (2.8 s) | 11,763 (35.3 s) | -9.2% |
| *Monaco GP 64*: Monaco GP | 51,069 | 25,188 (5.0 s) | 21,596 (67.6 s) | -14.3% |
| *Monaco GP 64*: Pro Monaco GP | 51,104 | 25,845 (5.1 s) | 22,204 (69.3 s) | -14.1% |
| *Shuttle Invader* 1.0 | 33,881 | 16,228 (3.2 s) | 14,752 (43.9 s) | -9.1% |
| total | | 137,446 | 122,525 | -10.9% |

These are finished, released games, each as its release build before any
packing. All seven came out smaller with delta matches (Monaco GP would
be 22,087 without them). With `--no-return` each is about 25 bytes
smaller. With `--fast` they unpack in about two-thirds of the time
(Monaco GP in 45 s; 123,282 in total) for 95-120 bytes more each.

Other crunchers on the same seven: Dali 0.3.5 137,767 (1.6-2.9 s),
Subsizer 0.6 140,719 (3.2-6.1 s), TSCrunch 1.3.1 166,524 (0.8-1.8 s).
Raw streams on a mixed set (the seven programs, a Python source, English
text):

| | total |
|---|---|
| Exomizer 3.1.2 (best of four settings) | 167,892 |
| Shrinkler, `-9` (Amiga intro packer, LZ + range coder) | 158,638 |
| greygoo | 148,753 |

**On real hardware** (a C64 Ultimate, PAL, with an earlier version's
unpacker, 2026-09-29): with the turbo off, a 47 KB game unpacked in
56.1 s, and all 46,774 bytes read back from the machine were identical to
the original. With Turbo Control set to *U64 Turbo Registers* at 64 MHz,
it took 3.1 s after a reset. The firmware kept the CPU at 1 MHz for the
first 2.3 s, then the remaining 45 KB took 0.85 s. Every target in this
release was run in VICE (see Checks).

## What a packed program does to the machine

- It loads where the machine's BASIC programs start, as `10 SYS<n>`
  followed by a short stub. The stub moves the packed data and the
  unpacker into place and jumps to the unpacker. The unpacker rebuilds
  the program in place from its load address upwards.
- **Memory:** the unpacker (530-570 bytes) and a 1.5-1.75 KB work area go
  wherever there is room, above or below the program. They can sit under
  the ROMs, and the work area even under the I/O. The screen is used as a
  last resort, blanked meanwhile and cleaned afterwards. If no room is
  left, part of the program is folded into its own runs of one byte and
  put back at the end.
- **Banking:** the least the layout needs. On the C64, that means
  nothing, BASIC out (the KERNAL stays, so interrupts and RESTORE work),
  the KERNAL out too, or all RAM, with interrupts off for the last two.
  The C128 does the same through its MMU, and the Plus/4 switches its
  ROMs in or out.
- **Zero page:** 23-30 bytes of BASIC's floating-point scratch (`$55-$72`
  on the C64), cleared again at the end.
- **Border:** steps through the greys (C64, C128, Plus/4), one step for
  each 256 bytes unpacked; the original colour comes back at the end.
- **Turbo:** on the C64 `$d031` is written with `$0f` (the Ultimate's top
  speed) on every page, and its original value comes back at the end.
  Elsewhere it is an unused VIC register. The C128 runs at 2 MHz, and the
  Plus/4 blanks its screen to run at 1.77 MHz.
- **At the end:** the memory configuration for the program, the
  interrupts as they were, the stack as `SYS` left it, BASIC's pointers
  as the program's own LOAD would leave them, then the program starts.

## Limits

- 13-15 times slower than Exomizer on a stock machine (9-10 times with
  `--fast`).
- On an unexpanded VIC-20 only small programs fit (about 1.5 KB), because
  the unpacker needs about 2 KB of its own.
- The MEGA65 / C65, Atari, Apple II, BBC Micro and Oric are not supported
  yet: there was nothing here to test them on.
- In the layouts that bank out the KERNAL, pressing RESTORE while it
  unpacks crashes it.
- A program loaded over the KERNAL's vectors (`$0314`) needs `--sei`.
  Without it the first interrupt goes through whatever the program put
  there, just as with a normal LOAD.

## How it works

**The model.** An LZ77 parse is coded bit by bit by an adaptive binary
range coder, whose probabilities are 8 bits, each moving a sixteenth of
the way towards every bit it sees. A token is one of five kinds:
- a literal
- a new match
- a repeat of the last offset
- a near byte: one byte copied from 1-15 back, an idea from Subsizer's
  one-byte matches
- a delta match: a match whose every byte has the same constant added,
  an idea from pucrunch's delta LZ77. It catches tables that repeat a
  step higher (sprite positions, sines, screen-row addresses) and
  counting runs. Its decoder costs 40 bytes and a page of work area, so
  `ggsfx.py` uses it only where the file comes out smaller

The kinds of the last two tokens pick the contexts. A literal is an 8-bit
tree. After a match it is predicted by the byte at the last offset, until
the first bit that differs (LZMA's "matched literal" idea, implemented
anew). Lengths and offsets are Elias-gamma-like codes whose first bits are
adaptive. `docs/format.md` has every detail.

**The parse** is optimal for the bit prices it is given: a shortest path
over positions whose states carry the last two token kinds and the repeat
offset. There are six passes, each priced by the statistics of the
previous one: the second by the whole file's, the rest by those of each
2 KB stretch and its neighbours, mixed with the whole file's. That way the
parse follows the file's changing contents.

**The unpacker** is 466 bytes of 6502 including its start table (506
with delta matches), and
follows the Python coder to the bit. A few techniques keep it small:
- The multiply has no loop counter: a marker bit above the multiplier
  ends the loop. (`--fast` swaps in quarter-square tables: the same
  product in well under half the time.)
- It normalises at the start of each decision, so the bit comes back in
  the carry.
- It builds lengths and offsets as complements (the stream codes their
  bits inverted), so a copy counts up to zero and an offset adds.
- On BASIC V2 machines the BASIC line's end-of-program zero doubles as the
  operand of the stub's first instruction.

**In place.** `greygoo.trace` decodes the stream exactly as the 6502 will
and measures how far output writing can run ahead of input reading. The
packed data is placed just far enough above the program's start (3 bytes
spare) that the output never overwrites input not yet read.

## Checks

Everything below passed for this release.

- **Real programs:** the seven above, packed and run in the simulator from
  `SYS` to the program's start, then compared byte for byte. The
  simulator models each machine's banking. It stops with an error if code
  ever runs outside RAM or reads a ROM where RAM was meant. Two of the
  packed games (Monaco GP and *Asteroids Deluxe*) were also run in VICE:
  unpacked byte for byte, and each game started.
- **Every target in VICE:** C64, C128, Plus/4, C16 and PET 4032, and the
  VIC-20 unexpanded and with 3 KB, 8 KB and all its expansions. Each
  packed program unpacked byte for byte, started and returned to BASIC
  (these runs used generated programs and the decoder without delta
  matches; the one with them ran the two games above).
  BASIC worked afterwards (floating point, strings, loops), and
  `--start basic` RUN a BASIC program on every machine.
- **Edge cases:** programs reaching under the KERNAL and over the I/O
  area, from `$0200`, to `$ffff` (folded), and refusals (too small, no
  room).
- **Features:** merged files, `systrim`, `basic`, `bin`, text after the
  `SYS`, custom code, offset limits, and `--desfx` back to the input (on
  Exomizer's files too).
- **The decoder for your own programs:** both input modes and both
  decoders unpacked in the simulator. The CBM prg Studio syntax was
  assembled byte for byte the same by another assembler.
- **Generated data:** streams unpacked in place by the 6502 decoder at odd
  addresses (tiny files, 50 KB runs, offsets up to 44 KB, noise, text,
  tables and ramps), with each of the four decoders.
- **gg6502 against other tools:** the same bytes as another 6502
  assembler, and the same registers, flags, memory and cycle counts as
  another simulator.

`python3 tests/ggtest.py` runs a quicker version (a few minutes).

## Files

| file | |
|---|---|
| `ggsfx.py` | builds and checks self-extracting programs |
| `ggtarget.py` | the machines: memory maps, banking, BASIC, effects |
| `greygoo.py` | the cruncher: the model, the parse, raw, `--mem` and `--level` streams |
| `ggasm.py` | writes the decoder for your own programs |
| `ggcoder.py` | the range coder, exactly as the 6502 does it |
| `gg6502.py` | the 6502 assembler and cycle-counting simulator |
| `ggdec.asm`, `ggdec-fast.asm` | the unpacker's 6502 source, small and fast |
| `docs/format.md` | the stream format |
| `docs/decoder.md` | using the decoder in your own program |
| `docs/targets.md` | the machines |
| `tests/ggtest.py` | the self-test |

## Licence

zlib licence (see `LICENSE`): use it for anything, commercially too. Don't
claim you wrote the original, and mark changed versions as changed.
Packed files carry no conditions at all, and neither does the unpacker
inside them or the decoder in your own programs.
