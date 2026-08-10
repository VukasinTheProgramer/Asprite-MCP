from dataclasses import dataclass, field

from .bridge.base import AsepriteBridge
from .config import Config
from .errors import ToolError


@dataclass
class SpriteHandle:
    path: str
    width: int
    height: int
    color_mode: str
    palette: list[str] = field(default_factory=list)
    palette_names: dict[int, str] = field(default_factory=dict)


@dataclass
class SessionState:
    """Yielded by the lifespan; reached via ctx.request_context.lifespan_context."""

    config: Config
    bridge: AsepriteBridge | None = None
    sprites: dict[str, SpriteHandle] = field(default_factory=dict)
    active: str | None = None

    def resolve_sprite(self, sprite: str | None) -> str:
        """Which sprite a tool should act on: the explicit param, or the active one."""
        target = sprite or self.active
        if not target:
            raise ToolError(
                code="no_active_sprite",
                message="No sprite specified and none is active.",
                hint="Call create_sprite first, or pass sprite=<path>.",
            )
        return target
