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

from .tools import document, drawing  # noqa: E402 — after `mcp` exists, before registration

for module in (document, drawing):
    module.register(mcp)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
