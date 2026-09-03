"""Turning results into bytes on a stream.

The rest of the package returns objects and raises exceptions. Only this module
knows whether a human or a machine is reading, which is what lets the same
command handlers serve a terminal and the `retro_toolbox` Dart bridge.

Every payload carries a `kind` key. `JsonRenderer` passes it through; the
`HumanRenderer` dispatches on it. Adding a verb means adding a formatter here,
not a `print()` somewhere in a handler.
"""

from __future__ import annotations

import json
import sys
from typing import Any, Protocol, TextIO, runtime_checkable


@runtime_checkable
class Renderer(Protocol):
    """What every command handler is allowed to assume about output.

    `runtime_checkable` so the suite can assert both concrete renderers still
    answer to this surface. That check compares names only — it says nothing
    about signatures — but it is enough to catch a method renamed on one
    renderer and not the other.
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

    `err` is accepted and never written to. Keeping the signature identical to
    `HumanRenderer` means the CLI picks a renderer once and forgets about it.
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
        # Payload first: a handler that happens to carry an `event` or `ok` key
        # must not be able to overwrite the two keys the consumer parses on.
        self._emit({**payload, "event": "result", "ok": True})

    def error(self, exc: BaseException) -> None:
        self._emit({"event": "error", "type": type(exc).__name__, "msg": str(exc)})


class HumanRenderer:
    """Readable text: results on stdout, everything else on stderr."""

    def __init__(self, out: TextIO | None = None, err: TextIO | None = None) -> None:
        self.out = out if out is not None else sys.stdout
        self.err = err if err is not None else sys.stderr
        self._progress_open = False

    # -- stream bookkeeping -------------------------------------------------

    def _close_progress(self) -> None:
        """End a `\\r`-rewritten progress line before anything else prints."""
        if self._progress_open:
            self.err.write("\n")
            self.err.flush()
            self._progress_open = False

    def _line(self, text: str) -> None:
        self.out.write(text + "\n")

    # -- Renderer -----------------------------------------------------------

    def status(self, msg: str) -> None:
        self._close_progress()
        self.err.write(msg + "\n")
        self.err.flush()

    def progress(self, pct: float, msg: str) -> None:
        # Redirected to a file, rewritten lines are noise. Status still prints,
        # so a captured log remains readable. Returning before the flag is set
        # keeps `_close_progress` from emitting a newline no line ever opened.
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
        # `.get`, not `[]`: a payload with no `kind` violates this module's
        # docstring, but raising here would replace a producer bug with no output
        # at all, and this is the last stop before the user. `_fallback` still
        # prints every key, so the degraded rendering is the useful failure. That
        # is contract, not accident: the suite pins the absent-`kind` case as
        # well as the unrecognised-`kind` one.
        formatter = {
            "patchers": self._patchers,
            "rom_info": self._rom_info,
            "rosters": self._rosters,
            "patch": self._patch,
        }.get(str(payload.get("kind")), self._fallback)
        formatter(payload)
        self.out.flush()

    # -- per-kind formatters ------------------------------------------------

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
            # `strict` catches a row longer than the header; a short one never
            # reaches here, because sizing `widths` indexes every row by every
            # header position and raises IndexError first. Neither is reachable
            # from a payload — the comprehension above always yields six cells —
            # so this is belt-and-braces against a later edit to this function,
            # and B905 requires an explicit `strict=` either way.
            cells = (cell.ljust(w) for cell, w in zip(row, widths, strict=True))
            self._line("  ".join(cells).rstrip())

    def _rom_info(self, payload: dict[str, Any]) -> None:
        matches = payload["matches"]
        if not matches:
            self._line("no registered patcher recognised this ROM")
            return
        for info in matches:
            # Indexed, not `.get`: every match is a `RomInfo.to_dict()`, which
            # emits `slots` and `is_valid` unconditionally. A `.get` would turn a
            # producer that dropped one into "valid: no" / "0 slots" — a
            # plausible falsehood about the user's ROM rather than a traceback.
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
        """A kind with no formatter still prints something useful."""
        for key, value in payload.items():
            if key != "kind":
                self._line(f"{key}: {value}")
