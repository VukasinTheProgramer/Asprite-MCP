import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from .bridge.base import AsepriteBridge
from .config import Config
from .errors import ToolError

if TYPE_CHECKING:
    from .style import StyleBible


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
    # Active style bible (style.py). Tools that take palette/canvas arguments
    # fall back to this project's values so the model stops re-choosing them
    # per sprite — that drift is what makes a generated library look unrelated.
    active_project: str | None = None
    # Tools are plain `def`, so the SDK runs each on a worker thread and they
    # mutate this state concurrently. Dict writes are individually safe under
    # the GIL, but push_snapshot's append-then-clear-the-redo-branch is a
    # read-modify-write and is not. Held only around state edits, never around a
    # bridge call -- that is the bridge's own lock's job.
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    # File-snapshot undo/redo (history.py). Keyed by sprite path. Aseprite's
    # own app.undo() is a no-op across batch's per-command fresh processes —
    # there's no persistent in-memory undo stack to call it on. Verified
    # empirically (M9 spike, 2026-08-10).
    undo_stack: dict[str, list[Path]] = field(default_factory=dict)
    redo_stack: dict[str, list[Path]] = field(default_factory=dict)

    def active_style(self) -> "StyleBible | None":
        """The active project's bible, or None if no project is active. Callers
        treat None as "the model supplies everything explicitly" — a missing
        bible is the pre-Phase-A status quo, not an error."""
        if not self.active_project:
            return None
        from .style import StyleBible

        return StyleBible.load(self.config.styles, self.active_project)

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
