from typing import Any, Protocol


class AsepriteBridge(Protocol):
    def execute(self, lua: str, timeout: float = 10.0) -> Any:
        """Run a Lua chunk. Returns the decoded JSON result.
        Raises AsepriteError on Lua-side failure."""
        ...

    def start(self) -> None: ...
    def stop(self) -> None: ...

    @property
    def alive(self) -> bool: ...
