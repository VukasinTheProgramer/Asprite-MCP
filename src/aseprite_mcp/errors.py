import json
from dataclasses import dataclass, field


@dataclass
class ToolError(Exception):
    """Model-visible failure. RAISE this — never return it.

    Raising an ordinary exception yields a result with is_error=True and __str__ in
    `content`, which the model reads and corrects. RETURNING an error dict yields
    is_error=False — it reads as success to the client and the loop never closes.
    """

    code: str
    message: str
    hint: str | None = None
    context: dict = field(default_factory=dict)

    def __str__(self) -> str:
        out = [f"{self.code}: {self.message}"]
        if self.hint:
            out.append(f"Hint: {self.hint}")
        if self.context:
            out.append(json.dumps(self.context))
        return " ".join(out)


class AsepriteError(ToolError):
    """A Lua-side command failed. Model can usually retry with different input."""

    def __init__(self, lua_message: str):
        super().__init__(code="aseprite_error", message=lua_message)


class BridgeTimeout(ToolError):
    def __init__(self, seconds: float):
        super().__init__(
            code="bridge_timeout",
            message=f"Command did not complete within {seconds}s.",
            hint="The Aseprite process may be stuck. Try a shorter script or check bridge health.",
        )
