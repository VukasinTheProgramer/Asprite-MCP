from typing import Annotated

from mcp.server.mcpserver import Image as MCPImage
from mcp.server.mcpserver import MCPServer
from pydantic import Field

from .. import history
from ..deps import Bridge, Session
from ..errors import ToolError
from ..render import emit


def register(mcp: MCPServer) -> None:
    @mcp.tool(structured_output=False)
    def undo(
        bridge: Bridge,
        session: Session,
        sprite: str | None = None,
        steps: Annotated[int, Field(ge=1)] = 1,
        preview: bool = True,
    ) -> list[str | MCPImage]:
        """Undo the last `steps` mutations to a sprite.

        File-snapshot based, not Aseprite's own undo — every mutating tool
        snapshots the sprite before it changes anything, and this restores
        from that history. Safe across the whole session, not just one tool
        call."""
        path = session.resolve_sprite(sprite)
        applied = history.undo(session, path, steps)
        if applied == 0:
            raise ToolError(
                code="nothing_to_undo",
                message="No earlier state recorded for this sprite.",
                hint="This is the earliest tracked state — nothing before it to undo to.",
            )
        summary = f"Undid {applied} step(s)."
        if applied < steps:
            summary += f" (requested {steps}, only {applied} available)"
        return emit(bridge, session.config.previews, path, summary, preview)

    @mcp.tool(structured_output=False)
    def redo(
        bridge: Bridge,
        session: Session,
        sprite: str | None = None,
        steps: Annotated[int, Field(ge=1)] = 1,
        preview: bool = True,
    ) -> list[str | MCPImage]:
        """Redo the last `steps` undone mutations. Cleared by any new
        mutation after an undo, same as any standard undo/redo model."""
        path = session.resolve_sprite(sprite)
        applied = history.redo(session, path, steps)
        if applied == 0:
            raise ToolError(
                code="nothing_to_redo",
                message="No undone state to redo.",
                hint="Either nothing has been undone, or a new edit already cleared the redo history.",
            )
        summary = f"Redid {applied} step(s)."
        if applied < steps:
            summary += f" (requested {steps}, only {applied} available)"
        return emit(bridge, session.config.previews, path, summary, preview)
