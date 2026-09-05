"""Turning results into bytes on a stream.

Every payload carries a `kind` key: `JsonRenderer` passes it through, the
`HumanRenderer` dispatches on it. Adding a verb means adding a formatter here,
not a `print()` in a handler.
"""

from __future__ import annotations

import json
import sys
from typing import Any, Protocol, TextIO, runtime_checkable


@runtime_checkable
class Renderer(Protocol):
    """What every command handler is allowed to assume about output.

    `runtime_checkable` compares names only, but that is enough to catch a
    method renamed on one renderer and not the other.
    """

    def status(self, msg: str) -> None: ...
    def progress(self, pct: float, msg: str) -> None: ...
    def partial(self, data: Any) -> None: ...
    def result(self, payload: dict[str, Any]) -> None: ...
    def error(self, exc: BaseException) -> None: ...


def _clamp(pct: float) -> float:
    """Callers report progress from loop counters; off-by-ones happen."""
    return max(0.0, min(1.0, float(pct)))


class JsonRenderer:
    """Newline-delimited JSON on stdout, one event per line.

    `err` is accepted and never written to: the signature must stay identical
    to `HumanRenderer`.
    """

    def __init__(self, out: TextIO | None = None, err: TextIO | None = None) -> None:
        self.out = out if out is not None else sys.stdout
        self.err = err if err is not None else sys.stderr

    def _emit(self, event: dict[str, Any]) -> None:
        self.out.write(json.dumps(event, separators=(",", ":")) + "\n")
        self.out.flush()  # a consumer may be reading the pipe line by line

    def status(self, msg: str) -> None:
        self._emit({"event": "status", "msg": msg})

    def progress(self, pct: float, msg: str) -> None:
        self._emit({"event": "progress", "pct": _clamp(pct), "msg": msg})

    def partial(self, data: Any) -> None:
        self._emit({"event": "partial", "data": data})

    def result(self, payload: dict[str, Any]) -> None:
        # Payload first: a handler's own `event` or `ok` key must not win over
        # the two keys the consumer parses on.
        self._emit({**payload, "event": "result", "ok": True})

    def error(self, exc: BaseException) -> None:
        self._emit({"event": "error", "type": type(exc).__name__, "msg": str(exc)})


class HumanRenderer:
    """Readable text: results on stdout, everything else on stderr."""

    def __init__(self, out: TextIO | None = None, err: TextIO | None = None) -> None:
        self.out = out if out is not None else sys.stdout
        self.err = err if err is not None else sys.stderr
        self._progress_open = False

    def _close_progress(self) -> None:
        """End a `\\r`-rewritten progress line before anything else prints."""
        if self._progress_open:
            self.err.write("\n")
            self.err.flush()
            self._progress_open = False

    def _line(self, text: str) -> None:
        self.out.write(text + "\n")

    def status(self, msg: str) -> None:
        self._close_progress()
        self.err.write(msg + "\n")
        self.err.flush()

    def progress(self, pct: float, msg: str) -> None:
        # Return before setting the flag, or `_close_progress` emits a newline
        # for a progress line that was never opened.
        if not self.err.isatty():
            return
        self.err.write(f"\r{_clamp(pct) * 100:5.1f}%  {msg}")
        self.err.flush()
        self._progress_open = True

    def partial(self, data: Any) -> None:
        """Nothing to show. Partials exist for UIs that render incrementally."""

    def error(self, exc: BaseException) -> None:
        self._close_progress()
        self.err.write(f"error: {type(exc).__name__}: {exc}\n")
        self.err.flush()

    def result(self, payload: dict[str, Any]) -> None:
        self._close_progress()
        # `.get`, not `[]`: a missing `kind` must degrade to `_fallback`, not
        # raise. This is the last stop before the user.
        formatter = {
            "patchers": self._patchers,
            "rom_info": self._rom_info,
            "rosters": self._rosters,
            "patch": self._patch,
        }.get(str(payload.get("kind")), self._fallback)
        formatter(payload)
        self.out.flush()

    def _patchers(self, payload: dict[str, Any]) -> None:
        rows = [
            [
                p["game_id"],
                p["platform"],
                p["sport"],
                "yes" if p["requires_slot_mapping"] else "no",
                ",".join(p["providers"]),
            ]
            for p in payload["patchers"]
        ]
        header = ["GAME", "PLATFORM", "SPORT", "SLOT-MAP", "PROVIDERS"]
        widths = [max(len(r[i]) for r in [header, *rows]) for i in range(len(header))]
        for row in [header, *rows]:
            # `strict` guards a later edit that changes the row width; B905
            # requires an explicit `strict=` either way.
            cells = (cell.ljust(w) for cell, w in zip(row, widths, strict=True))
            self._line("  ".join(cells).rstrip())

    def _rom_info(self, payload: dict[str, Any]) -> None:
        matches = payload["matches"]
        if not matches:
            self._line("no registered patcher recognised this ROM")
            return
        for info in matches:
            # Indexed, not `.get`: a `.get` would turn a dropped key into
            # "valid: no" / "0 slots", a plausible falsehood about the ROM.
            slots = len(info["slots"])
            self._line(f"{info['game_id']}")
            self._line(f"  path:   {info['path']}")
            self._line(f"  size:   {info['size']:,} bytes")
            self._line(f"  valid:  {'yes' if info['is_valid'] else 'no'}")
            self._line(f"  slots:  {slots} slot{'' if slots == 1 else 's'}")

    def _rosters(self, payload: dict[str, Any]) -> None:
        self._line(f"{payload['league']} {payload['season']}")
        self._line(f"  {payload['teams']} teams, {payload['players']} players")
        if payload.get("output_path"):
            self._line(f"  written to {payload['output_path']}")

    def _patch(self, payload: dict[str, Any]) -> None:
        self._line(f"wrote {payload['output_path']}")
        self._line(
            f"  {payload['teams_patched']} teams, {payload['players_patched']} players patched"
        )

    def _fallback(self, payload: dict[str, Any]) -> None:
        for key, value in payload.items():
            if key != "kind":
                self._line(f"{key}: {value}")
