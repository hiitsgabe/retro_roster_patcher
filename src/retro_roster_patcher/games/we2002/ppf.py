"""PPF (PlayStation Patch Format) applier for PS1 BIN images.

Applies PPF1, PPF2 and PPF3. PPF2 and PPF3 are matched on their first four
bytes, PPF1 on all five of `PPF10`. Keep the magic check strict: a community
`w202-english.ppf` supplied through `assets_dir` is applied with
`skip_validation=True` (its stored size and 0x9320 block belong to a different
dump), so the magic is the only guard left before an in-place write.

Reference implementation: github.com/sahlberg/pop-fe/blob/master/ppf.py
PPF3 spec: github.com/meunierd/ppf/blob/master/ppfdev/PPF3.txt
"""

import os
import struct

from ...core.errors import RomError


class PPFError(RomError):
    """Raised when a PPF patch cannot be applied.

    Must stay a `RomError`: `WE2002Patcher.patch` promises `RomError` on any
    write failure.
    """

    pass


def apply_ppf(bin_path: str, ppf_path: str, skip_validation: bool = False) -> str:
    """Apply a PPF patch in-place to `bin_path` and return its description.

    `skip_validation` drops the PPF2/PPF3 size and 0x9320 block checks, for
    patches that should apply regardless of the exact ROM dump variant.
    """
    with open(ppf_path, "rb") as f:
        patch = f.read()

    magic = patch[:5]

    if magic[:4] == b"PPF2":
        return _apply_ppf2(bin_path, patch, skip_validation=skip_validation)
    elif magic[:4] == b"PPF3":
        return _apply_ppf3(bin_path, patch, skip_validation=skip_validation)
    elif magic == b"PPF10":
        return _apply_ppf1(bin_path, patch)
    else:
        raise PPFError(f"Unsupported PPF format: {magic!r}")


def _apply_ppf1(bin_path: str, buf: bytes) -> str:
    """PPF1: 50-byte description at 6, records from 56.

    Record = u32 offset, u8 count, `count` patch bytes.
    """
    description = buf[6:56].decode("ascii", errors="replace").rstrip("\x00")

    data = buf[56:]
    with open(bin_path, "r+b") as f:
        while len(data) >= 5:
            offset = struct.unpack_from("<I", data, 0)[0]
            count = data[4]
            if len(data) < 5 + count:
                break
            f.seek(offset)
            f.write(data[5 : 5 + count])
            data = data[5 + count :]

    return description


def _apply_ppf2(bin_path: str, buf: bytes, skip_validation: bool = False) -> str:
    """PPF2: u32 image size at 56, 1024-byte copy of the image at 0x9320 from
    60, records from 1084 (u32 offset, u8 count, `count` patch bytes)."""
    description = buf[6:56].decode("ascii", errors="replace").rstrip("\x00")

    # Strip FILE_ID.DIZ if present
    if len(buf) > 38 and buf[-8:-4] == b".DIZ":
        idlen = struct.unpack_from("<I", buf, len(buf) - 4)[0]
        buf = buf[: -(idlen + 38)]

    if not skip_validation:
        expected_size = struct.unpack_from("<I", buf, 56)[0]
        actual_size = os.path.getsize(bin_path)
        if actual_size != expected_size:
            raise PPFError(
                f"Size mismatch: patch expects {expected_size:,} bytes, "
                f"ROM is {actual_size:,} bytes"
            )

        with open(bin_path, "rb") as f:
            f.seek(0x9320)
            block = f.read(1024)
        if buf[60 : 60 + 1024] != block:
            raise PPFError("Validation failed — PPF patch is for a different ROM dump")

    data = buf[1084:]
    with open(bin_path, "r+b") as f:
        while len(data) >= 5:
            offset = struct.unpack_from("<I", data, 0)[0]
            count = data[4]
            if len(data) < 5 + count:
                break
            f.seek(offset)
            f.write(data[5 : 5 + count])
            data = data[5 + count :]

    return description


def _apply_ppf3(bin_path: str, buf: bytes, skip_validation: bool = False) -> str:
    """PPF3: encoding method at 5, blockcheck flag at 57, undo flag at 58.

    Records start at 1084 when blockcheck is set (the 1024-byte 0x9320 copy sits
    at 60), otherwise at 60. Record = u64 offset, u8 count, `count` patch bytes,
    then `count` bytes of original data when undo is set.
    """
    description = buf[6:56].decode("ascii", errors="replace").rstrip("\x00")

    method = buf[5]
    if method != 2:
        raise PPFError(f"Unsupported PPF3 encoding method: {method}")

    blockcheck = buf[57]
    undo = buf[58]

    # Strip FILE_ID.DIZ if present
    if len(buf) > 38 and buf[-6:-4] == b".DIZ":
        idlen = struct.unpack_from("<H", buf, len(buf) - 2)[0]
        buf = buf[: -(idlen + 38)]

    if blockcheck and not skip_validation:
        with open(bin_path, "rb") as f:
            f.seek(0x9320)
            block = f.read(1024)
        if buf[60 : 60 + 1024] != block:
            raise PPFError("Validation failed — PPF patch is for a different ROM dump")

    if blockcheck:
        data = buf[1084:]
    else:
        data = buf[60:]

    with open(bin_path, "r+b") as f:
        while len(data) >= 9:
            offset = struct.unpack_from("<Q", data, 0)[0]
            count = data[8]
            if len(data) < 9 + count:
                break
            f.seek(offset)
            f.write(data[9 : 9 + count])
            data = data[9 + count :]
            if undo:
                data = data[count:]  # skip undo (original) data

    return description


def get_ppf_info(ppf_path: str) -> dict:
    """Read PPF header without applying.

    Returns {version, description, expected_size}.
    expected_size is only set for PPF2 (uint32 at offset 56).
    """
    with open(ppf_path, "rb") as f:
        header = f.read(60)

    magic = header[:5]
    if magic[:4] == b"PPF2":
        version = 2
    elif magic[:4] == b"PPF3":
        version = 3
    elif magic == b"PPF10":
        version = 1
    else:
        return {
            "version": 0,
            "description": f"Unknown format: {magic!r}",
            "expected_size": 0,
        }

    description = header[6:56].decode("ascii", errors="replace").rstrip("\x00")
    expected_size = 0
    if version == 2 and len(header) >= 60:
        expected_size = struct.unpack_from("<I", header, 56)[0]
    return {
        "version": version,
        "description": description,
        "expected_size": expected_size,
    }
