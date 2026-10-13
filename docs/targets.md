# The machines

`ggsfx.py -t TARGET` (default `c64`). Every target was run in VICE: the
packed program unpacked byte for byte, started, returned to BASIC, and
BASIC worked afterwards; `--start basic` RUNs a BASIC program on all of
them.

| target | machine | BASIC at | what the unpacking may switch | border effect | turbo |
|---|---|---|---|---|---|
| `c64` | Commodore 64 | `$0801` | `$01`: BASIC out (`$36`), the KERNAL too (`$35`), all RAM (`$34`) | greys | the Ultimate 64's turbo register |
| `c128` | Commodore 128 in C128 mode, bank 0 | `$1c01` | the MMU at `$ff00`: `$0e`, `$3e`, `$3f` | greys | 2 MHz (the 40-column screen blanked) |
| `plus4` | Plus/4 | `$1001` | the ROMs out (`$ff3f`), back (`$ff3e`) | greys (TED luminances) | the screen blanked: the TED clocks the CPU at 1.77 MHz |
| `c16` | C16 with 16 KB | `$1001` | nothing (RAM ends at `$4000`) | greys | the screen blanked |
| `vic20` | VIC-20, unexpanded | `$1001` | nothing | blue, cyan, white | none |
| `vic20+3k` | with the 3 KB expansion | `$0401` | nothing | | none |
| `vic20+8k` .. `vic20+24k` | with 8, 16 or 24 KB | `$1201` | nothing | | none |
| `vic20+32k` | blocks 1, 2, 3 and 5 | `$1201` | nothing | | none |
| `vic20+35k` | everything (3 KB as well) | `$1201` | nothing | | none |
| `pet` | PET 4032 (BASIC 4) | `$0401` | nothing | none | none |

Exomizer's numbers work too: `-t 64`, `128`, `4`, `16`, `20`, `23`, `52`,
`55`, `4032`.

## How much room it needs

Besides the program and its stream, the decoder needs about 2 KB: about
520 bytes of code and 6 pages of work area, from an even page (a page
more when the file uses delta matches, 4 more with `--fast`). `ggsfx.py` finds the room itself: above or below the
program, under the ROMs, the work area even under the I/O, the screen
(blanked, then cleaned) as a last resort, or by folding part of the
program into its own runs of one byte. On an unexpanded VIC-20, that
leaves room for programs of about 1.5 KB.

## The memory configuration for the program

`--mem` sets it: `$01` on the C64 (default `$37`), `$ff00` on the C128
(default `$00`), 0 or 1 (the ROMs in or out) on the Plus/4 and C16. If it
leaves the KERNAL out, interrupts stay off; `--sei` keeps them off anyway.

## BASIC afterwards

By default greygoo leaves BASIC as the program's own LOAD would: its
end-of-program pointers at the program's end (for a program that loads
where BASIC programs start) and its floating-point scratch zero page
clear. That costs about 25 bytes; `--no-return` drops it for programs that
never return to BASIC or call its routines.

## Zero page

The decoder borrows 23-30 bytes of BASIC's floating-point scratch:
`$55-$72` on the C64, VIC-20, Plus/4 and C16, `$59-$73` on the C128,
`$54-$6f` on the PET. The KERNAL's own zero page is left alone.
