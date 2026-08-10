# CLAUDE.md — Aseprite MCP

Coding rules for this repo. `aseprite-mcp-build-flow.md` is the build plan (what/when).
This file is the law (how). When the two disagree, this file wins.

## Stack

- Python 3.11+, MCP Python SDK **2.x**, Pillow, numpy, pydantic ≥2.12.
- No new dependency without a reason that a few lines of stdlib can't cover.
- Entrypoint: `aseprite_mcp.server:main`, stdio transport.

> **`aseprite-mcp-build-flow.md` was written against SDK 1.x and its server code is stale.**
> Its architecture, pixel-art craft rules, and pitfall table still hold. Its imports,
> error handling, and auto-preview return shape do not. See "SDK 2.x" below.

## SDK 2.x — the API actually is this

```python
from mcp.server import MCPServer                        # NOT mcp.server.fastmcp.FastMCP
from mcp.server.mcpserver import Resolve, Context
from mcp.server.mcpserver import Image as MCPImage      # PIL owns the bare name `Image`
```

- `FastMCP` is **gone**. No alias, no deprecation shim. Anything importing `mcp.server.fastmcp` is v1 code.
- HTTP client is `httpx2`, not `httpx`. Don't add `httpx`.
- All Pydantic field names are snake_case in Python (`input_schema`, `is_error`); the wire stays camelCase.
- Transport args (`host`, `port`, …) go to `run()`, not the constructor.
- MCP types silently drop unknown fields. Custom data goes in `_meta` or it vanishes.

## Layout

Follow `aseprite-mcp-build-flow.md` §2. Don't invent new top-level packages.
New tool → a module in `src/aseprite_mcp/tools/` exposing `register(mcp)`, pulling the
`Bridge` / `Session` aliases from `deps.py` (rule 28).

## Build order

M0 → M1 → M2 → M3 → M4, then reassess. Do not start a milestone before the
previous one's "done when" is actually true. The Phase 0 spike gates everything:
no MCP tool code lands before the bridge round-trips 200 commands.

---

## Hard rules — do not violate

These are the failure modes this project is known to hit. Each one cost someone a day.

### Bridge

1. **Atomic queue writes.** Write `<id>.tmp`, then `rename()` to `<id>.cmd`. Never write `.cmd` directly.
2. **Serialize every command** behind one lock. Aseprite is single-threaded; concurrent commands corrupt sprite state.
3. **Poll for process death** inside the wait loop. A crash must raise in ms, not hang for the full timeout.
4. **Both backends implement `AsepriteBridge`** (`base.py`). Server code never branches on which backend is live.
5. **Batch backend has no state.** Every batch command is open → mutate → save. `app.sprite` is nil there.

### Lua

6. **Never f-string user values into Lua.** Type-validate scalars; escape strings with `lua_str()` / `%q`. Colors must match `^#[0-9a-fA-F]{6,8}$`.
7. **Every mutation wraps in `J.tx()`** — one undo step, clean rollback on error.
8. **Clone → mutate → assign** for `cel.image`. In-place edits bleed into linked cels.
9. Shared Lua helpers live in `prelude.lua`, injected once. Don't redefine JSON encoding per tool.

### Images

10. **`Image.NEAREST` for every upscale.** No exceptions. Bilinear makes the model hallucinate anti-aliasing.
11. **Preview upscales to 256–512px**, capped at 1024×1024 output. A raw 32×32 PNG is unreadable to a vision encoder.
12. **Quantize in OKLab/CIELAB**, never RGB Euclidean distance.
13. **Downscale LANCZOS to ~2× target, then NEAREST.** Not straight to either.
14. **Dithering off by default.** Floyd–Steinberg is noise at 32×32.

### Tool surface

15. **`action` enums over tool-per-operation.** `layers(action=...)` not `add_layer`/`delete_layer`/`rename_layer`. Dispatch to private handlers server-side.
16. **Every mutating tool auto-returns a preview**, with a `preview=False` opt-out so long scripted sequences don't return 40 images. Use the plain `emit()` helper (build flow §6.3), never a decorator — `functools.wraps` sets `__wrapped__`, `inspect.signature` follows it, and an injected kwarg vanishes from the published schema.
17. **Keep `run_lua`.** A missing wrapper must never block a user.
18. Tool docstrings are model-facing prompt surface. Write them for the model: state the preferred technique (`draw_grid` over pixel calls, `mirror` over hand-drawing both halves), give one worked example.

### Validation

19. **Validate fully before touching Aseprite.** Collect all errors; don't fail on the first and leave the sprite half-written.
20. **Path jail every path parameter** through `safe_path()`. Reading references from outside the workspace requires an explicit documented opt-in.
21. **Enforce limits:** canvas ≤1024 (warn >128), ≤65k pixels per `draw_grid`, 10s command timeout (30s export).

### Errors

22. **Raise. Never return an error dict.** A returned dict is `is_error=False` — it looks like success to the client. Raising an ordinary exception gives `is_error=True` with the message in `content`, which is the whole self-correction loop.
23. **`ToolError` subclasses `Exception`, never `MCPError`.** `MCPError` bypasses the model entirely and fails the JSON-RPC request. The test is *"could a smarter model have avoided this?"* — yes → `ToolError`; no → `MCPError`.
    - `ToolError`: bad grid, out-of-range index, out-of-bounds coords, unknown layer name.
    - `MCPError`: Aseprite binary not found, bridge dead and unrecoverable, workspace unwritable.
24. **The message must say what to do differently next time**, because the message is all the model gets. "Invalid index" is a bug in the error. "Grid uses index 9 but the palette has 8 colors (0-7) — use 0-7, or call set_palette with more colors first" is the bar. Put `code`/`hint`/`context` on the exception and render them into `str(exc)`.
25. Error codes are stable, snake_case identifiers (`grid_rows_uneven`, `palette_index_out_of_range`). Tests assert on codes, not on message prose.

### SDK 2.x plumbing

26. **`Image` results carry no structured content.** Returning `Image` sets `structured_content=None`, so a preview cannot be a field inside a returned data dict. A tool result is a list of content blocks — mutating tools return `list[str | Image]` (summary text + preview) and carry no output schema; read-only data tools keep their schema and return no image. **Verify this shape in the M1 spike before building 20 tools on the assumption.**
27. **The return type annotation *is* the output schema.** Read-only tools returning data (`get_sprite_info`) use a TypedDict/dataclass/pydantic model, never a bare `dict` with ad-hoc keys. Scalars and lists get wrapped in `{"result": ...}`; objects don't. Use `structured_output=False` for text-only tools.
28. **Inject the bridge and session state with `Resolve()`**, not module globals and not the closure `register(mcp, get_bridge, state)` pattern:
    ```python
    async def get_bridge(...) -> AsepriteBridge: ...

    @mcp.tool()
    async def draw_grid(grid: str, bridge: Annotated[AsepriteBridge, Resolve(get_bridge)]) -> ...
    ```
    Resolved params are invisible to the model and absent from the input schema. Bad dependency graphs fail at server startup, not at call time.
29. **Bridge start/stop belongs in `lifespan`**, not lazy init. `MCPServer("aseprite", lifespan=...)`; read it via `ctx.request_context.lifespan_context`. Shutdown goes in `finally` so Aseprite never orphans.
30. **Sync handlers run on a worker thread now.** Real concurrent tool calls will reach the bridge. Rule 2's lock is load-bearing, not defensive. Blocking calls are fine — they no longer stall the event loop.
31. **Validate with `Annotated[int, Field(ge=1, le=1024)]`** where the SDK can do it — those errors already reach the model. Hand-rolled checks are for what `Field` can't express (grid row evenness, palette bounds, canvas overhang).
32. Use `ctx.log(data=...)` and `ctx.report_progress()`. Not `print()`, not `message=` (that's the v1 signature).
33. Mark tools with `annotations` — read-only for `get_sprite_info`/`get_region_as_grid`, destructive for anything overwriting pixels. Hints for the client, not security.

---

## Testing

- Unit tests need no Aseprite: grid parsing, legend mapping, validation, path jail, Lua escaping, quantization, ramps. These run on every commit.
- Integration tests are marked and skip cleanly when no binary is found.
- **`draw_grid` → `get_region_as_grid` round-trip is the canary test.** Keep it green; it covers bridge, transaction, palette mapping, and coordinates in one assert.
- Golden PNGs catch Aseprite version drift. Regenerate deliberately, never "to make CI pass".
- New non-trivial logic ships with one runnable check. No fixtures-on-fixtures.

## Security

- `run_lua` executes arbitrary code by design. Bound it: shadow `os.execute`, `os.remove`, `io.popen`; enforce timeout; run in a transaction; log every script.
- Never widen the path jail to "make it work". Add an explicit opt-in parameter instead.
- No secrets, no absolute user paths, no `~/.aseprite-mcp` contents in committed files.

## Licensing

- **Never vendor, bundle, or redistribute the Aseprite binary** — repo, releases, or Docker image. EULA forbids it.
- Users bring their own licensed Aseprite ≥1.3 via `ASEPRITE_PATH`. Say so in the README.
- Own code is MIT. Keep `LICENSE` at root.

## Style

- Type hints on everything public. `Literal[...]` for enums the model sees.
- Structured logging to `~/.aseprite-mcp/logs/`, never `print()` — stdout is the MCP transport.
- Comments explain *why*, not *what*. The pitfall table in the build flow doc is a good source of "why" comments.
- Format/lint before commit. Don't mix a reformat with a behavior change in one commit.

## Commits

Conventional Commits. Subject ≤50 chars. Body only when the "why" isn't obvious from the diff.
