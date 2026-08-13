import json
import os
import subprocess
import tempfile
import threading
from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ..errors import AsepriteError, BridgeTimeout
from ..logs import get_logger

_log = get_logger("bridge.batch")

PRELUDE = (Path(__file__).parent / "lua" / "prelude.lua").read_text()

RESULT_MARKER = "__RESULT__"


class BatchBridge:
    """Spawns a fresh `aseprite --batch` process per command.

    No persistent state: every command must open -> mutate -> save itself.
    `app.sprite` is nil at the start of every call.
    """

    def __init__(self, exe: Path):
        self.exe = exe
        # CLAUDE.md #2. Not defensive -- load-bearing (#30): tools are plain
        # `def`, so the SDK runs each on a worker thread and real concurrent
        # calls reach here. Every command is open -> mutate -> save on the whole
        # file, so two overlapping calls are a lost update: measured, four
        # concurrent draw_grid calls landed one and silently discarded three,
        # every one of them returning success. Serialising makes concurrency
        # slow rather than wrong, which is the right trade for a single-threaded
        # backend.
        # Reentrant so a tool holding serialized() can still call execute().
        self._lock = threading.RLock()

    def start(self) -> None:
        pass  # nothing to start; each call is its own process

    def stop(self) -> None:
        pass

    @property
    def alive(self) -> bool:
        return True

    @contextmanager
    def serialized(self) -> Iterator[None]:
        """Hold the bridge for a whole read-modify-write.

        Locking each execute() alone fixes the common case (one call that opens,
        mutates and saves) but not a tool that reads pixels, computes, then
        writes them back: another call landing between the two would be clobbered
        by the stale write. `cleanup`, `conform_image` and `import_reference` all
        have that shape.
        """
        with self._lock:
            yield

    def execute(self, lua: str, timeout: float = 10.0) -> Any:
        # 10s matches the AsepriteBridge protocol default and §10.5's
        # resource limit. Was 30.0 here — silently overriding the protocol's
        # own documented default, since Python doesn't enforce a Protocol's
        # default value on implementers (found in M9 audit, 2026-08-10).
        # Same calling convention as the resident bridge: the command is a
        # function body, its `return` value is the result. Tools stay
        # backend-agnostic — they never know which bridge is running them.
        with self._lock:
            return self._execute_locked(lua, timeout)

    def _execute_locked(self, lua: str, timeout: float) -> Any:
        chunk = (
            f"local J = (function()\n{PRELUDE}\nend)()\n"
            f"local __out = (function()\n{lua}\nend)()\n"
            f'print("{RESULT_MARKER}" .. J.encode(__out))'
        )
        with tempfile.NamedTemporaryFile(
            "w", suffix=".lua", delete=False, encoding="utf-8"
        ) as f:
            f.write(chunk)
            script = f.name
        try:
            try:
                r = subprocess.run(
                    [str(self.exe), "--batch", "--script", script],
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
            except subprocess.TimeoutExpired:
                # The one failure a user cannot reproduce from the model's
                # transcript: record the script that hung, not just that it did.
                _log.error("command timed out", extra={"context": {
                    "timeout_s": timeout, "lua": lua[:2000]
                }})
                raise BridgeTimeout(timeout) from None

            for line in r.stdout.splitlines():
                if line.startswith(RESULT_MARKER):
                    return json.loads(line[len(RESULT_MARKER):])

            # No marker means the script errored before finishing. Aseprite
            # writes the Lua traceback to STDOUT, not stderr — confirmed by
            # direct probe (M0 spike). stderr is checked too in case that
            # changes in a future Aseprite version.
            detail = (
                r.stdout.strip() or r.stderr.strip()
                or f"aseprite exited {r.returncode} with no output"
            )
            _log.error("lua command failed", extra={"context": {
                "returncode": r.returncode, "detail": detail[:2000], "lua": lua[:2000]
            }})
            raise AsepriteError(detail)
        finally:
            os.unlink(script)
