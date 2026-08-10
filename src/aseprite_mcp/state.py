from dataclasses import dataclass, field

from .bridge.base import AsepriteBridge
from .config import Config


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
