"""Binary container formats shared by more than one game.

A format lands here when a second game needs it, and not before. Everything in
this package is pure `bytes` in, `bytes` out: no filesystem, no network, no
import of anything under `games/`. That is what makes a format testable without
a real image, which matters more here than anywhere else in the tree — no real
ROM or ISO may enter this repository, so every fixture is fabricated in-test and
a format that could only be exercised through a game's reader could not be
exercised at all.

**`iso9660` is the one exception, and it is a narrow one.** Its functions take a
`BinaryIO` rather than `bytes`, because a PS2 or PSP disc image is 500 MB to
1.5 GB and handing one over as a `bytes` object is not possible on the handhelds
this library targets. Nothing in it opens, closes, stats or names a file, so the
property the rule was actually protecting survives intact: a test hands it an
`io.BytesIO` over a fabricated image and never touches a filesystem. A `Path`
parameter is what would have broken it, and that is the version this package
does not have.

Nothing here is exported from the package root. The two consumers this library
was extracted for are a pygame launcher and a Flutter app over embedded
CPython, and neither of them parses an EA archive; they call `analyze`, `fetch`
and `patch`. `tests/test_public_api.py` pins the root surface exactly, so a name
added to it is a name this project owes compatibility on forever, and these are
implementation detail of three game packages. The precedent is
`games/we2002/ppf.py`, which has the same shape — bytes in, bytes out, its own
error class — and is likewise reachable only by its dotted path.

`formats/` deliberately does NOT hold WE2002's Mode2/2352 CD-ROM sector code.
That format carries a 12-byte sync pattern, a 4-byte header, an 8-byte
subheader and a trailing EDC/ECC block, and the games here use Mode1/2048 where
a sector is nothing but its 2048 payload bytes. The two share the word "sector"
and no arithmetic; unifying them would produce a helper whose every caller
passes a flag saying which of two unrelated layouts it meant.
"""
