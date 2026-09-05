"""Binary container formats shared by more than one game.

Everything here is pure `bytes` in, `bytes` out: no filesystem, no network, no
import of anything under `games/`. `iso9660` is the one exception, and takes a
`BinaryIO` because a PS2 or PSP disc image is too large to hold as `bytes`; it
still opens, closes, stats and names nothing.

Nothing here is exported from the package root.
"""
