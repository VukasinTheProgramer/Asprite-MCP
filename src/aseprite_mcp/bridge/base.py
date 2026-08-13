from collections.abc import Iterator
from contextlib import AbstractContextManager
from typing import Any, Protocol


class AsepriteBridge(Protocol):
    def execute(self, lua: str, timeout: float = 10.0) -> Any:
        """Run a Lua chunk. Returns the decoded JSON result.
        Raises AsepriteError on Lua-side failure."""
        ...

    def serialized(self) -> AbstractContextManager[None]:
        """Hold the bridge across several execute() calls, so a read-modify-write
        cannot interleave with another tool's write (CLAUDE.md #2)."""
        ...

    def start(self) -> None: ...
    def stop(self) -> None: ...

    @property
    def alive(self) -> bool: ...
