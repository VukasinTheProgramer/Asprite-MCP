from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from mcp.server import MCPServer

from .bridge import make_bridge
from .config import load_config
from .state import SessionState


@asynccontextmanager
async def lifespan(server: MCPServer) -> AsyncIterator[SessionState]:
    """One Aseprite process per server run. Started once, stopped once."""
    state = SessionState(config=load_config())
    state.bridge = make_bridge(state.config)
    state.bridge.start()
    try:
        yield state
    finally:
        state.bridge.stop()  # never orphan Aseprite


mcp = MCPServer("aseprite", lifespan=lifespan)

from . import prompts, resources  # noqa: E402
from .tools import (  # noqa: E402
    cleanup_tool,
    document,
    drawing,
    escape,
    export,
    palette,
    reference,
    structure,
    undo,
)

for module in (
    document, drawing, palette, structure, reference, export, escape, undo, cleanup_tool
):
    module.register(mcp)

prompts.register(mcp)
resources.register(mcp)


def main() -> None:
    import sys

    if "--doctor" in sys.argv:
        from . import doctor

        sys.exit(doctor.run())
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
