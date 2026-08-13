import json

from mcp.server.mcpserver import Image as MCPImage
from mcp.server.mcpserver import MCPServer

from ..deps import Bridge, Session
from ..history import push_snapshot
from ..logs import get_logger
from ..render import emit
from ..validation import lua_str


_log = get_logger("run_lua")


def register(mcp: MCPServer) -> None:
    @mcp.tool(structured_output=False)
    def run_lua(
        bridge: Bridge,
        session: Session,
        script: str,
        sprite: str | None = None,
        preview: bool = True,
        timeout: float = 15.0,
    ) -> list[str | MCPImage]:
        """Execute arbitrary Lua against a sprite. Use when no other tool
        covers what you need — a missing wrapper should never block you.

        A local `path` variable holds the resolved sprite path — start with
        `local spr = J.sprite(path)`. Wrap mutations in `J.tx(function() ...
        end)` yourself for one clean undo step and rollback on error — same
        pattern every other tool here uses; `app.transaction()` needs a
        sprite already open, so it can't be applied automatically before
        your script has had the chance to open one. `J.save(spr)` persists
        changes (required — batch mode has no state between calls).
        `os.execute`/`os.remove`/`io.popen` are disabled. Every script is
        logged. `return` a plain value (usually a table) to get data back —
        it's JSON-encoded automatically, including strings with embedded
        newlines.

        Example: run_lua(script='local spr = J.sprite(path); return {w = spr.width}')
        """
        path = session.resolve_sprite(sprite)

        # Was a hand-rolled append to run_lua.log with no rotation, so it grew
        # forever. Same requirement, now through the shared rotating logger.
        _log.info("run_lua", extra={"context": {
            "sprite": path, "timeout_s": timeout, "script": script[:4000]
        }})

        push_snapshot(session, path)
        result = bridge.execute(
            f"local path = {lua_str(path)}\n{script}",
            timeout=timeout,
        )

        summary = "Ran Lua script."
        if result is not None:
            summary += f" Result: {json.dumps(result)}"
        return emit(bridge, session.config.previews, path, summary, preview)
