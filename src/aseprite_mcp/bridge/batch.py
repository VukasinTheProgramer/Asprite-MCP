import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from ..errors import AsepriteError, BridgeTimeout

PRELUDE = (Path(__file__).parent / "lua" / "prelude.lua").read_text()

RESULT_MARKER = "__RESULT__"


class BatchBridge:
    """Spawns a fresh `aseprite --batch` process per command.

    No persistent state: every command must open -> mutate -> save itself.
    `app.sprite` is nil at the start of every call.
    """

    def __init__(self, exe: Path):
        self.exe = exe

    def start(self) -> None:
        pass  # nothing to start; each call is its own process

    def stop(self) -> None:
        pass

    @property
    def alive(self) -> bool:
        return True

    def execute(self, lua: str, timeout: float = 10.0) -> Any:
        # 10s matches the AsepriteBridge protocol default and §10.5's
        # resource limit. Was 30.0 here — silently overriding the protocol's
        # own documented default, since Python doesn't enforce a Protocol's
        # default value on implementers (found in M9 audit, 2026-08-10).
        # Same calling convention as the resident bridge: the command is a
        # function body, its `return` value is the result. Tools stay
        # backend-agnostic — they never know which bridge is running them.
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
                raise BridgeTimeout(timeout) from None

            for line in r.stdout.splitlines():
                if line.startswith(RESULT_MARKER):
                    return json.loads(line[len(RESULT_MARKER):])

            # No marker means the script errored before finishing. Aseprite
            # writes the Lua traceback to STDOUT, not stderr — confirmed by
            # direct probe (M0 spike). stderr is checked too in case that
            # changes in a future Aseprite version.
            raise AsepriteError(
                r.stdout.strip() or r.stderr.strip() or f"aseprite exited {r.returncode} with no output"
            )
        finally:
            os.unlink(script)
