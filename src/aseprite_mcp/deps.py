from typing import Annotated

from mcp.server.mcpserver import Context, Resolve

from .bridge.base import AsepriteBridge
from .state import SessionState


async def _session(ctx: Context[SessionState]) -> SessionState:
    return ctx.request_context.lifespan_context


async def _bridge(ctx: Context[SessionState]) -> AsepriteBridge:
    st = ctx.request_context.lifespan_context
    assert st.bridge is not None, "bridge not started — lifespan should have set this"
    return st.bridge


# Import these in every tool module. Invisible to the model: absent from the
# input schema, resolved server-side before the tool body runs (CLAUDE.md #28).
Session = Annotated[SessionState, Resolve(_session)]
Bridge = Annotated[AsepriteBridge, Resolve(_bridge)]
