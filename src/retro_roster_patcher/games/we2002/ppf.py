"""PPF (PlayStation Patch Format) applier for PS1 BIN images.

Applies PPF1, PPF2 and PPF3. `apply_ppf` dispatches on the magic: PPF2 and PPF3
match on their first four bytes, PPF1 on all five of `PPF10`, and anything else
raises `PPFError`. That asymmetry is deliberate — see the tests — because the
one production call passes `skip_validation=True`, which leaves the magic as the
only thing between a wrong file and an in-place write into the output ISO.

In-product only PPF1 is ever applied: the sole call site is
`patcher._apply_translation`, and every patch this project generates carries the
`PPF10` magic. PPF2 and PPF3 exist here for externally supplied patches — a
community translation a user drops into `assets_dir`, which this project does
not redistribute — and for `get_ppf_info` to report on.

Reference implementation: github.com/sahlberg/pop-fe/blob/master/ppf.py
PPF3 spec: github.com/meunierd/ppf/blob/master/ppfdev/PPF3.txt
"""

import os
import struct


class PPFError(Exception):
    """Raised when a PPF patch cannot be applied."""

    pass


def apply_ppf(bin_path: str, ppf_path: str, skip_validation: bool = False) -> str:
    """Apply a PPF patch directly to a BIN file (modifies in-place).

    Args:
        bin_path: Path to the BIN file to patch.
        ppf_path: Path to the .ppf patch file.
        skip_validation: If True, skip PPF2/PPF3 size and block validation.
            Use for trusted/bundled patches that should apply regardless of
            the exact ROM dump variant.

    Returns:
        Description string from the PPF header.

    Raises:
        PPFError: If the patch format is unsupported or validation fails.
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
    """Apply PPF 1.0 patch."""
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
    """Apply PPF 2.0 patch."""
    description = buf[6:56].decode("ascii", errors="replace").rstrip("\x00")

    # Strip FILE_ID.DIZ if present
    if len(buf) > 38 and buf[-8:-4] == b".DIZ":
        idlen = struct.unpack_from("<I", buf, len(buf) - 4)[0]
        buf = buf[: -(idlen + 38)]

    if not skip_validation:
        # Validate file size
        expected_size = struct.unpack_from("<I", buf, 56)[0]
        actual_size = os.path.getsize(bin_path)
        if actual_size != expected_size:
            raise PPFError(
                f"Size mismatch: patch expects {expected_size:,} bytes, "
                f"ROM is {actual_size:,} bytes"
            )

        # Validate block at offset 0x9320
        with open(bin_path, "rb") as f:
            f.seek(0x9320)
            block = f.read(1024)
        if buf[60 : 60 + 1024] != block:
            raise PPFError("Validation failed — PPF patch is for a different ROM dump")

    # Apply patch records (start at offset 1084)
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
    """Apply PPF 3.0 patch."""
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

    # Validate block at 0x9320 if blockcheck enabled (unless skipping)
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

    # Apply patch records
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
