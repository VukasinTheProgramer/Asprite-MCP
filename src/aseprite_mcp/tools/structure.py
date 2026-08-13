from typing import Literal

from mcp.server.mcpserver import Image as MCPImage
from mcp.server.mcpserver import MCPServer

from ..deps import Bridge, Session
from ..errors import ToolError
from ..history import push_snapshot
from ..render import emit
from ..validation import lua_str

_BLEND_MODES = {
    "normal": "NORMAL", "multiply": "MULTIPLY", "screen": "SCREEN",
    "overlay": "OVERLAY", "darken": "DARKEN", "lighten": "LIGHTEN",
    "color_dodge": "COLOR_DODGE", "color_burn": "COLOR_BURN",
    "hard_light": "HARD_LIGHT", "soft_light": "SOFT_LIGHT",
    "difference": "DIFFERENCE", "exclusion": "EXCLUSION",
    "addition": "ADDITION", "subtract": "SUBTRACT", "divide": "DIVIDE",
}

_ANI_DIRS = {"forward": "FORWARD", "reverse": "REVERSE", "pingpong": "PING_PONG"}


def _require(action: str, **params: object) -> None:
    missing = [name for name, val in params.items() if val is None]
    if missing:
        raise ToolError(
            code="missing_parameter",
            message=f"action='{action}' requires: {', '.join(missing)}",
            hint=f"Pass {missing[0]} (and any others listed) for this action.",
            context={"action": action, "missing": missing},
        )


def _find_layer_lua(name: str) -> str:
    return (
        "local __target = nil\n"
        f"for _, l in ipairs(spr.layers) do if l.name == {lua_str(name)} then __target = l break end end\n"
        f"if not __target then error({lua_str('layer_not_found: ' + name)}) end"
    )


def register(mcp: MCPServer) -> None:
    @mcp.tool(structured_output=False)
    def layers(
        bridge: Bridge,
        session: Session,
        action: Literal["add", "delete", "rename", "reorder", "set", "list", "duplicate", "merge_down"],
        sprite: str | None = None,
        name: str | None = None,
        new_name: str | None = None,
        index: int | None = None,
        visible: bool | None = None,
        opacity: int | None = None,
        blend_mode: Literal[
            "normal", "multiply", "screen", "overlay", "darken", "lighten",
            "color_dodge", "color_burn", "hard_light", "soft_light",
            "difference", "exclusion", "addition", "subtract", "divide",
        ] | None = None,
        is_group: bool = False,
        preview: bool = True,
    ) -> list[str | MCPImage]:
        """Manage layers. Use action='add' with is_group=True for layer groups.

        add: creates a layer (optionally group), named `name` if given.
        delete/duplicate/merge_down: need `name`.
        rename: needs `name` and `new_name`.
        reorder: needs `name` and `index` (new stack position, 1 = bottom).
        set: needs `name`; any of visible/opacity/blend_mode to change.
        list: no other params — reports current layers.
        """
        path = session.resolve_sprite(sprite)
        if action != "list":
            push_snapshot(session, path)
        summary: str

        if action == "add":
            ctor = "spr:newGroup()" if is_group else "spr:newLayer()"
            result = bridge.execute(
                f"local spr = J.sprite({lua_str(path)})\n"
                "local l = J.tx(function()\n"
                f"  local __l = {ctor}\n"
                + (f"  __l.name = {lua_str(name)}\n" if name else "")
                + "  return __l\nend)\n"
                + "J.save(spr)\nreturn { name = l.name }"
            )
            summary = f"Added layer '{result['name']}'."

        elif action == "delete":
            _require(action, name=name)
            assert name is not None
            bridge.execute(
                f"local spr = J.sprite({lua_str(path)})\n"
                f"{_find_layer_lua(name)}\n"
                "J.tx(function() spr:deleteLayer(__target) end)\nJ.save(spr)\nreturn { ok = true }"
            )
            summary = f"Deleted layer '{name}'."

        elif action == "rename":
            _require(action, name=name, new_name=new_name)
            assert name is not None and new_name is not None
            bridge.execute(
                f"local spr = J.sprite({lua_str(path)})\n"
                f"{_find_layer_lua(name)}\n"
                f"J.tx(function() __target.name = {lua_str(new_name)} end)\nJ.save(spr)\nreturn {{ ok = true }}"
            )
            summary = f"Renamed layer '{name}' to '{new_name}'."

        elif action == "reorder":
            _require(action, name=name, index=index)
            assert name is not None and index is not None
            bridge.execute(
                f"local spr = J.sprite({lua_str(path)})\n"
                f"{_find_layer_lua(name)}\n"
                f"J.tx(function() __target.stackIndex = {index} end)\nJ.save(spr)\nreturn {{ ok = true }}"
            )
            summary = f"Moved layer '{name}' to stack position {index}."

        elif action == "set":
            _require(action, name=name)
            assert name is not None
            sets = []
            if visible is not None:
                sets.append(f"__target.isVisible = {'true' if visible else 'false'}")
            if opacity is not None:
                if not 0 <= opacity <= 255:
                    raise ToolError(code="opacity_out_of_range", message="opacity must be 0-255.")
                sets.append(f"__target.opacity = {opacity}")
            if blend_mode is not None:
                sets.append(f"__target.blendMode = BlendMode.{_BLEND_MODES[blend_mode]}")
            if not sets:
                raise ToolError(
                    code="missing_parameter",
                    message="action='set' needs at least one of visible/opacity/blend_mode.",
                )
            bridge.execute(
                f"local spr = J.sprite({lua_str(path)})\n"
                f"{_find_layer_lua(name)}\n"
                + "J.tx(function()\n" + "\n".join(sets) + "\nend)\n"
                + "J.save(spr)\nreturn { ok = true }"
            )
            summary = f"Updated layer '{name}'."

        elif action == "duplicate":
            _require(action, name=name)
            assert name is not None
            result = bridge.execute(
                f"local spr = J.sprite({lua_str(path)})\n"
                f"{_find_layer_lua(name)}\n"
                "app.activeSprite = spr\napp.activeLayer = __target\n"
                "local before = #spr.layers\n"
                "J.tx(function() app.command.DuplicateLayer() end)\n"
                "if #spr.layers == before then error('duplicate_failed: layer count unchanged') end\n"
                "J.save(spr)\nreturn { count = #spr.layers }"
            )
            summary = f"Duplicated layer '{name}' ({result['count']} layers total)."

        elif action == "merge_down":
            _require(action, name=name)
            assert name is not None
            result = bridge.execute(
                f"local spr = J.sprite({lua_str(path)})\n"
                f"{_find_layer_lua(name)}\n"
                "app.activeSprite = spr\napp.activeLayer = __target\n"
                "local before = #spr.layers\n"
                "J.tx(function() app.command.MergeDownLayer() end)\n"
                # app.command silently no-ops on invalid targets (e.g. the
                # bottom-most layer has nothing below it) — verified
                # empirically (M5 spike, 2026-08-10). Check it actually did
                # something rather than trust a clean return.
                "if #spr.layers == before then error('nothing_to_merge: "
                + name.replace("'", "") + " has no layer below it in its group') end\n"
                "J.save(spr)\nreturn { count = #spr.layers }"
            )
            summary = f"Merged layer '{name}' down ({result['count']} layers remain)."

        elif action == "list":
            info = bridge.execute(
                f"local spr = J.sprite({lua_str(path)})\n"
                "local rows = {}\n"
                "for i, l in ipairs(spr.layers) do\n"
                "  rows[i] = { name = l.name, visible = l.isVisible, opacity = l.opacity or 255, is_group = l.isGroup }\n"
                "end\nreturn { layers = rows }"
            )
            lines = ["| name | visible | opacity | group |", "|---|---|---|---|"]
            for row in info["layers"]:
                lines.append(f"| {row['name']} | {row['visible']} | {row['opacity']} | {row['is_group']} |")
            summary = "\n".join(lines)

        else:  # pragma: no cover — Literal exhausts this at the schema level
            raise ToolError(code="unknown_action", message=f"Unknown action '{action}'.")

        return emit(bridge, session.config.previews, path, summary, preview)

    @mcp.tool(structured_output=False)
    def frames(
        bridge: Bridge,
        session: Session,
        action: Literal["add", "delete", "duplicate", "set_duration", "list"],
        sprite: str | None = None,
        index: int | None = None,
        duration: float | None = None,
        preview: bool = True,
    ) -> list[str | MCPImage]:
        """Manage animation frames. Frame numbers are 1-indexed.

        add: appends a blank frame. `duration` (seconds) optional, default 0.1.
        delete/duplicate: need `index`.
        set_duration: needs `index` and `duration`.
        list: no other params — reports current frames.

        No reorder action: Aseprite's frame position isn't settable and
        app.command.MoveFrame doesn't exist on this build (verified, M5).
        """
        path = session.resolve_sprite(sprite)
        if action != "list":
            push_snapshot(session, path)
        summary: str

        if action == "add":
            result = bridge.execute(
                f"local spr = J.sprite({lua_str(path)})\n"
                "J.tx(function()\n"
                "  local f = spr:newEmptyFrame()\n"
                + (f"  f.duration = {duration}\n" if duration is not None else "")
                + "end)\n"
                + "J.save(spr)\nreturn { count = #spr.frames }"
            )
            summary = f"Added frame ({result['count']} frames total)."

        elif action == "delete":
            _require(action, index=index)
            result = bridge.execute(
                f"local spr = J.sprite({lua_str(path)})\n"
                f"if not spr.frames[{index}] then error('frame_out_of_range: {index}') end\n"
                f"J.tx(function() spr:deleteFrame({index}) end)\nJ.save(spr)\nreturn {{ count = #spr.frames }}"
            )
            summary = f"Deleted frame {index} ({result['count']} frames remain)."

        elif action == "duplicate":
            _require(action, index=index)
            result = bridge.execute(
                f"local spr = J.sprite({lua_str(path)})\n"
                f"if not spr.frames[{index}] then error('frame_out_of_range: {index}') end\n"
                + "J.tx(function()\n"
                f"  local f = spr:newFrame({index})\n"
                + (f"  f.duration = {duration}\n" if duration is not None else "")
                + "end)\n"
                + "J.save(spr)\nreturn { count = #spr.frames }"
            )
            summary = f"Duplicated frame {index} ({result['count']} frames total)."

        elif action == "set_duration":
            _require(action, index=index, duration=duration)
            bridge.execute(
                f"local spr = J.sprite({lua_str(path)})\n"
                f"if not spr.frames[{index}] then error('frame_out_of_range: {index}') end\n"
                f"J.tx(function() spr.frames[{index}].duration = {duration} end)\nJ.save(spr)\nreturn {{ ok = true }}"
            )
            summary = f"Frame {index} duration set to {duration}s."

        elif action == "list":
            info = bridge.execute(
                f"local spr = J.sprite({lua_str(path)})\n"
                "local rows = {}\n"
                "for i, f in ipairs(spr.frames) do rows[i] = { duration = f.duration } end\n"
                "return { frames = rows }"
            )
            lines = ["| # | duration |", "|---|---|"]
            for i, row in enumerate(info["frames"], start=1):
                lines.append(f"| {i} | {row['duration']}s |")
            summary = "\n".join(lines)

        else:  # pragma: no cover — Literal exhausts this at the schema level
            raise ToolError(code="unknown_action", message=f"Unknown action '{action}'.")

        return emit(bridge, session.config.previews, path, summary, preview)

    @mcp.tool(structured_output=False)
    def tags(
        bridge: Bridge,
        session: Session,
        action: Literal["add", "delete", "rename", "set", "list"],
        sprite: str | None = None,
        name: str | None = None,
        new_name: str | None = None,
        from_frame: int | None = None,
        to_frame: int | None = None,
        direction: Literal["forward", "reverse", "pingpong"] | None = None,
        preview: bool = True,
    ) -> list[str | MCPImage]:
        """Manage animation tags (named frame ranges).

        add: needs `name`, `from_frame`, `to_frame`. `direction` optional,
        default forward.
        delete/rename/set: need `name`. rename also needs `new_name`. set
        takes any of from_frame/to_frame/direction to change.
        list: no other params — reports current tags.
        """
        path = session.resolve_sprite(sprite)
        if action != "list":
            push_snapshot(session, path)
        summary: str

        def find_tag_lua() -> str:
            assert name is not None
            return (
                "local __tag = nil\n"
                f"for _, t in ipairs(spr.tags) do if t.name == {lua_str(name)} then __tag = t break end end\n"
                f"if not __tag then error({lua_str('tag_not_found: ' + name)}) end"
            )

        if action == "add":
            _require(action, name=name, from_frame=from_frame, to_frame=to_frame)
            assert name is not None and from_frame is not None and to_frame is not None
            dir_lua = f"AniDir.{_ANI_DIRS[direction or 'forward']}"
            bridge.execute(
                f"local spr = J.sprite({lua_str(path)})\n"
                f"if not spr.frames[{from_frame}] then error('frame_out_of_range: {from_frame}') end\n"
                f"if not spr.frames[{to_frame}] then error('frame_out_of_range: {to_frame}') end\n"
                "J.tx(function()\n"
                f"  local t = spr:newTag({from_frame}, {to_frame})\n"
                f"  t.name = {lua_str(name)}\n"
                f"  t.aniDir = {dir_lua}\n"
                "end)\n"
                "J.save(spr)\nreturn { ok = true }"
            )
            summary = f"Added tag '{name}' ({from_frame}-{to_frame})."

        elif action == "delete":
            _require(action, name=name)
            assert name is not None
            bridge.execute(
                f"local spr = J.sprite({lua_str(path)})\n"
                f"{find_tag_lua()}\n"
                "J.tx(function() spr:deleteTag(__tag) end)\nJ.save(spr)\nreturn { ok = true }"
            )
            summary = f"Deleted tag '{name}'."

        elif action == "rename":
            _require(action, name=name, new_name=new_name)
            assert name is not None and new_name is not None
            bridge.execute(
                f"local spr = J.sprite({lua_str(path)})\n"
                f"{find_tag_lua()}\n"
                f"J.tx(function() __tag.name = {lua_str(new_name)} end)\nJ.save(spr)\nreturn {{ ok = true }}"
            )
            summary = f"Renamed tag '{name}' to '{new_name}'."

        elif action == "set":
            _require(action, name=name)
            assert name is not None
            sets = []
            if from_frame is not None:
                sets.append(f"__tag.fromFrame = spr.frames[{from_frame}]")
            if to_frame is not None:
                sets.append(f"__tag.toFrame = spr.frames[{to_frame}]")
            if direction is not None:
                sets.append(f"__tag.aniDir = AniDir.{_ANI_DIRS[direction]}")
            if not sets:
                raise ToolError(
                    code="missing_parameter",
                    message="action='set' needs at least one of from_frame/to_frame/direction.",
                )
            bridge.execute(
                f"local spr = J.sprite({lua_str(path)})\n"
                f"{find_tag_lua()}\n"
                + "J.tx(function()\n" + "\n".join(sets) + "\nend)\n"
                + "J.save(spr)\nreturn { ok = true }"
            )
            summary = f"Updated tag '{name}'."

        elif action == "list":
            info = bridge.execute(
                f"local spr = J.sprite({lua_str(path)})\n"
                "local rows = {}\n"
                "for i, t in ipairs(spr.tags) do\n"
                "  rows[i] = { name = t.name, from_frame = t.fromFrame.frameNumber, to_frame = t.toFrame.frameNumber }\n"
                "end\nreturn { tags = rows }"
            )
            lines = ["| name | from | to |", "|---|---|---|"]
            for row in info["tags"]:
                lines.append(f"| {row['name']} | {row['from_frame']} | {row['to_frame']} |")
            summary = "\n".join(lines)

        else:  # pragma: no cover — Literal exhausts this at the schema level
            raise ToolError(code="unknown_action", message=f"Unknown action '{action}'.")

        return emit(bridge, session.config.previews, path, summary, preview)
