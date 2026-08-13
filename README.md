# Aseprite MCP

An MCP server that drives [Aseprite](https://www.aseprite.org/) — create sprites, draw with a
text-grid interface, manage layers/frames/tags, import and quantize reference images, and export —
all as tool calls an LLM can make, with a rendered preview after every mutation so the model sees
what it drew.

**Requires your own licensed copy of Aseprite ≥1.3 — not included or bundled.** See
[Licensing](aseprite-mcp-build-flow.md#13-licensing-and-engaging-the-aseprite-devs).

Supported Aseprite versions: **1.3.0–1.3.x tested** (developed against `1.3.18.1-dev`); 1.4-beta
best-effort. The Lua scripting API drifts between versions — if a tool call fails on your build with
an error naming a Lua field or method, that's the most likely cause.

## 60-second quickstart

```bash
uv tool install aseprite-mcp   # or: uvx aseprite-mcp
aseprite-mcp --doctor          # confirms Aseprite is found and the bridge works
```

Add to your MCP client config (e.g. Claude Desktop's `claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "aseprite": {
      "command": "uvx",
      "args": ["aseprite-mcp"],
      "env": {
        "ASEPRITE_PATH": "/Applications/Aseprite.app/Contents/MacOS/aseprite",
        "ASEPRITE_MCP_WORKSPACE": "~/pixel-art",
        "ASEPRITE_MCP_STYLES": "~/pixel-art/styles"
      }
    }
  }
}
```

`ASEPRITE_PATH` is optional — the server searches common install locations first (see
[discovery.py](src/aseprite_mcp/discovery.py)) and only needs it if your install is somewhere
unusual. `ASEPRITE_MCP_WORKSPACE` defaults to `~/.aseprite-mcp/workspace`; every sprite path a tool
call uses is relative to it (a jailed directory, not an arbitrary filesystem path).
`ASEPRITE_MCP_STYLES` defaults to `~/.aseprite-mcp/styles` and holds the style bibles.

Then, from your MCP client: *"Set up a style project on db16, then make me a 32x32 knight."*

Start with the `style-setup` prompt, then `sprite-character`. Anchoring on a style project first is
what keeps a whole asset set on one palette and one light direction — without it the model re-picks
both on every sprite and the library drifts.

## Style projects

A **style bible** is a project's visual language stored on disk: palette, hue-clustered shading
ramps, light direction, outline treatment, and a default canvas size per asset type.

```
style(action="create", project="dungeon", palette="db16")
create_sprite(name="knight", asset_type="character")   # size + palette come from the project
conform_image(image_path="ref.png", target_size=[32, 32])   # locked to the project palette
```

With a project active you stop passing dimensions and palettes per call. `set_palette` warns (never
blocks) when a sprite diverges from the project, so going off-palette stays possible but never
silent.

Bibles live under `~/.aseprite-mcp/styles/` — override with `ASEPRITE_MCP_STYLES`.

> Palette entry 0 is always reserved for transparency: Aseprite renders it transparent whatever
> color sits there, so any palette you supply is shifted up by one to make room.

## Tools

| Tool | What it does |
|---|---|
| `style` | Create/get/update/list style projects, set the active one |
| `create_sprite` | New sprite; takes canvas size + palette from the active project via `asset_type` |
| `get_sprite_info` | Live dimensions, color mode, layers, frames, palette size, tags |
| `draw_grid` | Draw a block of pixels from a text grid (palette-index characters) |
| `draw_shape` | Line/rect/ellipse/polyline/point, with mirroring |
| `fill` | Flood fill from a seed point |
| `get_region_as_grid` | Read pixels back in the same grid format `draw_grid` accepts |
| `set_palette` | Preset (`pico8`/`db16`/`sweetie16`/`gameboy`) or explicit hex list |
| `get_ramp` | Hue-shifted shading ramp from a base color |
| `layers` | Add/delete/rename/reorder/set/duplicate/merge_down/list |
| `frames` | Add/delete/duplicate/set_duration/list |
| `tags` | Add/delete/rename/set/list (named frame ranges) |
| `conform_image` | **Any image → true, grid-correct, palette-locked pixel art.** Grid detection, two-stage downscale, OKLab quantize, auto-repair |
| `detect_grid` | Whether an image sits on a real pixel grid, and its cell size |
| `cleanup` | Deterministic repair: AA fringes, orphan pixels, jaggies, near-duplicate colors |
| `import_reference` | Photo → cropped, downscaled, OKLab-quantized starting point |
| `export` | PNG (single frame), GIF (animation), or spritesheet + JSON |
| `run_lua` | Arbitrary Lua for anything the above doesn't cover — sandboxed, logged |
| `undo` / `redo` | File-snapshot based; safe across the whole session |

Every mutating tool returns a rendered preview alongside its text result — the model sees the
consequence of each action without a separate "show me" step.

## Prompts

Each is a concrete, checkpointed tool-call sequence rather than open-ended instructions. Invoke as a
slash command or prompt template in your MCP client.

| Prompt | When |
|---|---|
| `style-setup` | **First.** Establishes the palette, ramps and canvas defaults everything else inherits |
| `sprite-character` | Draw a character silhouette-first on the project's style |
| `sprite-from-reference` | Convert a reference image via `conform_image`, then refine by hand |
| `sprite-tileset` | A seamlessly-tiling terrain or material tile |
| `animate-walkcycle` | A looping walk cycle, transformed from one key pose |
| `palette-explore` | Compare palette candidates, then commit the winner as a project |

## Resources

`aseprite://guide/pixel-art` (the craft rules — silhouettes, hue-shifted ramps, avoiding pillow
shading), `aseprite://guide/lua-api` (condensed Lua reference for `run_lua`, including the gotchas
this project found the hard way), `aseprite://palettes` (bundled preset colors).

## Troubleshooting

**"Could not find Aseprite"** — set `ASEPRITE_PATH` explicitly to the executable (not the `.app`
bundle on macOS — the binary inside it, e.g.
`/Applications/Aseprite.app/Contents/MacOS/aseprite`). Run `aseprite-mcp --doctor` to see exactly
which paths were tried.

**Anything failed and you need the detail afterwards** — `~/.aseprite-mcp/logs/aseprite-mcp.log`,
one JSON object per line, rotated at 2 MB. Bridge failures record the Aseprite traceback and the Lua
that caused it; timeouts record the script that hung; every `run_lua` script is logged. Nothing is
ever written to stdout — that carries the MCP protocol.

**A tool call times out** — the default per-command timeout is 10s (30s for exports), generous for
normal use (measured average is under 100ms). A timeout usually means Aseprite itself hung on
something unrelated to the command — check for a stray Aseprite process and kill it, then retry.

**A tool call fails naming a Lua field/method that "should" exist** — likely version drift; the Lua
scripting API isn't perfectly stable across Aseprite releases. Check your Aseprite version against
the supported range above, and see the pitfall table in
[aseprite-mcp-build-flow.md](aseprite-mcp-build-flow.md#15-pitfalls-reference) for known
version-sensitive spots.

## Development

```bash
uv sync                          # installs deps + dev tools (mypy, pytest)
uv run mypy src                  # required clean before every commit
ASEPRITE_PATH=... uv run pytest tests/   # integration tests need a real binary;
                                          # skip cleanly without one
```

[`CLAUDE.md`](CLAUDE.md) has the full rule set this project is built against.
[`aseprite-mcp-build-flow.md`](aseprite-mcp-build-flow.md) is the build log — architecture,
milestone-by-milestone findings, and every Aseprite/SDK gotcha discovered while building this,
verified against the real binary rather than assumed from docs.

## License

MIT for this project's own code. Aseprite itself is not included — see the licensing note at the top
of this file.
