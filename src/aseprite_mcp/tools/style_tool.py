"""The `style` tool — the project style bible. See upgrade-plan A2.

Schema/persistence logic lives in ../style.py (pure, unit-tested); this module
is the model-facing surface.
"""

from typing import Literal

from mcp.server.mcpserver import Image as MCPImage
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from ..deps import Session
from ..errors import ToolError
from ..palettes import available_presets, load_preset
from ..render import preview_image, render_palette_swatch
from ..style import StyleBible, available_projects


def _resolve_palette(palette: str | list[str]) -> list[str]:
    if isinstance(palette, str):
        try:
            return list(load_preset(palette)["colors"])
        except ToolError:
            raise
        except Exception:
            raise ToolError(
                code="unknown_palette_preset",
                message=f"No bundled preset named {palette!r}.",
                hint=f"Bundled presets: {available_presets()}. Or pass an explicit hex list.",
                context={"presets": available_presets()},
            ) from None
    return list(palette)


def _describe(bible: StyleBible) -> str:
    lines = [
        f"project: {bible.project}",
        f"palette: {len(bible.palette)} colors",
        f"light_source: {bible.light_source}",
        f"outline: {bible.outline} (indices {bible.outline_indices})",
        f"shading_steps: {bible.shading_steps}   dithering: {bible.dithering}",
    ]
    if bible.ramps:
        lines.append("ramps (dark -> light):")
        lines += [f"  {n}: {idxs}" for n, idxs in bible.ramps.items()]
    if bible.roles:
        lines.append("roles: " + ", ".join(f"{i}={r}" for i, r in sorted(bible.roles.items())))
    lines.append("canvas_defaults: " + ", ".join(f"{k}={v[0]}x{v[1]}" for k, v in bible.canvas_defaults.items()))
    lines.append("avoid: " + "; ".join(bible.negative))
    return "\n".join(lines)


def register(mcp: MCPServer) -> None:
    @mcp.tool(structured_output=False, annotations=ToolAnnotations(idempotent_hint=True))
    def style(
        session: Session,
        action: Literal["create", "get", "update", "list", "set_active"],
        project: str | None = None,
        palette: str | list[str] | None = None,
        roles: dict[int, str] | None = None,
        ramps: dict[str, list[int]] | None = None,
        light_source: str | None = None,
        outline: str | None = None,
        outline_indices: list[int] | None = None,
        shading_steps: int | None = None,
        dithering: str | None = None,
        canvas_defaults: dict[str, list[int]] | None = None,
        negative: list[str] | None = None,
        reference_sprites: list[str] | None = None,
    ) -> list[str | MCPImage]:
        """Manage the project style bible — the palette, ramps, light direction
        and canvas sizes that every asset in a project shares.

        **Call `style(action="get")` before starting any asset work.** Once a
        project is active, `create_sprite` takes its palette and canvas size
        automatically, so you stop choosing those per sprite and the library
        stays consistent.

        - `create` — needs `project` and `palette` (a preset name or hex list).
          Ramps and outline indices are derived from the palette for you.
        - `get` — the bible plus a labelled palette swatch strip. With no
          `project`, returns the active one.
        - `update` — change any field on an existing bible. Replacing `palette`
          regenerates `ramps` unless you pass those too.
        - `set_active` — every other tool then defaults to this project.
        - `list` — known projects.

        Example — start a project on db16 and make it active:
            style(action="create", project="dungeon", palette="db16")
        """
        root = session.config.styles

        if action == "list":
            names = available_projects(root)
            active = f"\nactive: {session.active_project}" if session.active_project else ""
            return [("projects: " + (", ".join(names) if names else "(none)")) + active]

        if action == "create":
            if not project or palette is None:
                raise ToolError(
                    code="style_create_needs_args",
                    message="create requires both `project` and `palette`.",
                    hint="e.g. style(action='create', project='dungeon', palette='db16')",
                )
            if StyleBible.path_for(root, project).exists():
                raise ToolError(
                    code="style_project_exists",
                    message=f"Project {project!r} already has a style bible.",
                    hint="Use action='update' to change it, or pick another name.",
                )
            bible = StyleBible.create(project, _resolve_palette(palette))
            bible.apply(
                {
                    k: v
                    for k, v in {
                        "roles": roles, "ramps": ramps, "light_source": light_source,
                        "outline": outline, "outline_indices": outline_indices,
                        "shading_steps": shading_steps, "dithering": dithering,
                        "canvas_defaults": canvas_defaults, "negative": negative,
                        "reference_sprites": reference_sprites,
                    }.items()
                    if v is not None
                }
            )
            bible.save(root)
            session.active_project = project
            return [
                f"Created style bible for {project!r} and made it active.\n{_describe(bible)}",
                preview_image(render_palette_swatch(bible.palette)),
            ]

        target = project or session.active_project
        if not target:
            raise ToolError(
                code="no_active_project",
                message="No project given and none is active.",
                hint=(
                    f"Pass project=..., or style(action='set_active', project=...). "
                    f"Known projects: {available_projects(root) or '(none)'}."
                ),
                context={"available": available_projects(root)},
            )

        if action == "set_active":
            StyleBible.load(root, target)  # raises style_project_not_found if absent
            session.active_project = target
            return [f"Active project is now {target!r}."]

        bible = StyleBible.load(root, target)

        if action == "get":
            return [_describe(bible), preview_image(render_palette_swatch(bible.palette))]

        # action == "update"
        fields = {
            k: v
            for k, v in {
                "palette": _resolve_palette(palette) if palette is not None else None,
                "roles": roles, "ramps": ramps, "light_source": light_source,
                "outline": outline, "outline_indices": outline_indices,
                "shading_steps": shading_steps, "dithering": dithering,
                "canvas_defaults": canvas_defaults, "negative": negative,
                "reference_sprites": reference_sprites,
            }.items()
            if v is not None
        }
        if not fields:
            raise ToolError(
                code="style_update_no_fields",
                message="update was called with nothing to change.",
                hint="Pass at least one field, e.g. shading_steps=4.",
            )
        changed = bible.apply(fields)
        bible.save(root)
        return [
            f"Updated {target!r}: {', '.join(changed) or 'no change'}.\n{_describe(bible)}",
            preview_image(render_palette_swatch(bible.palette)),
        ]
