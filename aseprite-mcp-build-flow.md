# Aseprite MCP — Full Build Flow

A step-by-step implementation guide. Companion to `aseprite-mcp-design.md` (which covers *what* and *why*); this document covers *how*, in build order.

**Stack assumption:** Python 3.11+ with the MCP Python SDK 2.x, Pillow for image work, a resident Aseprite process driven by a Lua listener over a filesystem command queue, with CLI batch spawns as fallback.

> **SDK version note.** This document was originally written against MCP Python SDK 1.x, where the
> high-level server class was `FastMCP`. SDK 2.0 renamed it to `MCPServer`, moved the module, and
> changed error and content semantics — with no alias and no deprecation shim. The Python snippets in
> §4, §6.2–6.3, §10.3 and §12.1 have been updated; **the architecture, Lua bridge, pixel-art craft
> rules, pitfall table and milestone order are unaffected and remain authoritative.**
> `CLAUDE.md` holds the current rules. Where a snippet here disagrees with it, `CLAUDE.md` wins.

---

## Table of contents

0. [Prerequisites](#0-prerequisites)
1. [Phase 0 — Spike: prove the bridge works](#1-phase-0--spike-prove-the-bridge-works)
2. [Repo layout](#2-repo-layout)
3. [Phase 1 — The Aseprite bridge](#3-phase-1--the-aseprite-bridge)
4. [Phase 2 — MCP server skeleton](#4-phase-2--mcp-server-skeleton)
5. [Phase 3 — Core tools](#5-phase-3--core-tools)
6. [Phase 4 — The vision loop](#6-phase-4--the-vision-loop)
7. [Phase 5 — Palette system](#7-phase-5--palette-system)
8. [Phase 6 — Reference image pipeline](#8-phase-6--reference-image-pipeline)
9. [Phase 7 — Prompts and resources](#9-phase-7--prompts-and-resources)
10. [Phase 8 — Safety and validation](#10-phase-8--safety-and-validation)
11. [Phase 9 — Testing](#11-phase-9--testing)
12. [Phase 10 — Packaging and distribution](#12-phase-10--packaging-and-distribution)
13. [Licensing and engaging the Aseprite devs](#13-licensing-and-engaging-the-aseprite-devs)
14. [Milestone checklist](#14-milestone-checklist)
15. [Pitfalls reference](#15-pitfalls-reference)
16. [End-to-end trace](#16-end-to-end-trace)

---

## 0. Prerequisites

### 0.1 Aseprite installation

You need a working Aseprite ≥ 1.3. Verify the Lua API is present:

```bash
# macOS
/Applications/Aseprite.app/Contents/MacOS/aseprite --version
# Windows
"C:\Program Files\Aseprite\Aseprite.exe" --version
# Linux (Steam)
~/.steam/steam/steamapps/common/Aseprite/aseprite --version
```

Pin a supported range in your README: **1.3.0 – 1.3.x tested, 1.4-beta best-effort.** The Lua API does drift between versions.

### 0.2 Binary discovery

Do not make the user set an env var if you can avoid it. Resolution order:

1. `ASEPRITE_PATH` env var (explicit override always wins)
2. `shutil.which("aseprite")`
3. Platform-specific known paths (list below)
4. Steam library folders parsed from `libraryfolders.vdf`
5. Fail with an actionable error naming all paths tried

```python
# src/aseprite_mcp/discovery.py
import os, shutil, platform
from pathlib import Path

CANDIDATES = {
    "Darwin": [
        "/Applications/Aseprite.app/Contents/MacOS/aseprite",
        "~/Applications/Aseprite.app/Contents/MacOS/aseprite",
        "~/Library/Application Support/Steam/steamapps/common/Aseprite/Aseprite.app/Contents/MacOS/aseprite",
    ],
    "Windows": [
        r"C:\Program Files\Aseprite\Aseprite.exe",
        r"C:\Program Files (x86)\Steam\steamapps\common\Aseprite\Aseprite.exe",
        r"~\AppData\Local\Programs\Aseprite\Aseprite.exe",
    ],
    "Linux": [
        "/usr/bin/aseprite",
        "/usr/local/bin/aseprite",
        "~/.steam/steam/steamapps/common/Aseprite/aseprite",
        "~/.local/share/Steam/steamapps/common/Aseprite/aseprite",
        "/var/lib/flatpak/exports/bin/org.aseprite.Aseprite",
    ],
}

def find_aseprite() -> Path:
    if p := os.environ.get("ASEPRITE_PATH"):
        if Path(p).expanduser().exists():
            return Path(p).expanduser()
        raise RuntimeError(f"ASEPRITE_PATH set to {p} but no file there.")
    if w := shutil.which("aseprite"):
        return Path(w)
    tried = []
    for c in CANDIDATES.get(platform.system(), []):
        pp = Path(c).expanduser()
        tried.append(str(pp))
        if pp.exists():
            return pp
    raise RuntimeError(
        "Could not find Aseprite. Set ASEPRITE_PATH to the executable.\nTried:\n  "
        + "\n  ".join(tried)
    )
```

### 0.3 Version + capability probe

At startup, run `aseprite --version` and cache the result. Feature-detect anything version-sensitive rather than branching on version strings where you can.

### 0.4 Workspace directory

All sprite files live under a single jailed workspace:

```
~/.aseprite-mcp/
  workspace/     # user sprites — the only writable art dir
  runtime/       # command queue, listener state
  previews/      # rendered PNGs (ephemeral, gitignored)
  logs/
```

Configurable via `ASEPRITE_MCP_WORKSPACE`. Every path argument from the model is resolved and checked to be inside `workspace/` — see §10.1.

---

## 1. Phase 0 — Spike: prove the bridge works

**Do not write a single MCP tool until this spike passes.** The riskiest assumption in the whole project is that you can drive a resident Aseprite reliably. Find out in an afternoon, not week three.

### 1.1 The spike

Write two files and run them by hand.

`spike/listener.lua`:

```lua
-- Resident polling listener. Run with the GUI attached for the spike.
local queue_dir = app.params["queue"] or error("need --script-param queue=...")
local running = true

local function read_file(path)
  local f = io.open(path, "rb")
  if not f then return nil end
  local data = f:read("*all")
  f:close()
  return data
end

local function write_file(path, data)
  local f = io.open(path, "wb")
  f:write(data)
  f:close()
end

-- Poll for <id>.cmd files, execute, write <id>.done
local function tick()
  local files = app.fs.listFiles(queue_dir)
  for _, name in ipairs(files) do
    if name:match("%.cmd$") then
      local id = name:gsub("%.cmd$", "")
      local path = app.fs.joinPath(queue_dir, name)
      local src = read_file(path)
      os.remove(path)

      local ok, result = pcall(function()
        local chunk = assert(load(src, "cmd:" .. id))
        return chunk()
      end)

      local payload
      if ok then
        payload = '{"ok":true,"result":' .. (result or "null") .. '}'
      else
        payload = '{"ok":false,"error":' .. string.format("%q", tostring(result)) .. '}'
      end
      write_file(app.fs.joinPath(queue_dir, id .. ".done"), payload)
    end
  end
end

-- Aseprite has no sleep/timer in batch; use a Dialog timer when UI is available.
if app.isUIAvailable then
  local dlg = Dialog("aseprite-mcp")
  dlg:label{ text = "MCP bridge running" }
  dlg:button{ text = "Stop", onclick = function() running = false; dlg:close() end }
  dlg:show{ wait = false }
  -- poll loop driven by repeated timer events
  local timer = Timer{ interval = 0.05, ontick = function() if running then tick() end end }
  timer:start()
else
  -- headless: busy loop with a bounded lifetime
  local deadline = os.time() + 3600
  while running and os.time() < deadline do tick() end
end
```

> **Note:** `Timer` availability varies by version. If it's absent, fall back to the busy-poll branch, or skip the resident model entirely and use CLI spawns (§3.4). The spike exists to find this out.

`spike/test.py`:

```python
import json, subprocess, time, uuid
from pathlib import Path
from aseprite_mcp.discovery import find_aseprite

QUEUE = Path("~/.aseprite-mcp/runtime/queue").expanduser()
QUEUE.mkdir(parents=True, exist_ok=True)

proc = subprocess.Popen([
    str(find_aseprite()),
    "--script-param", f"queue={QUEUE}",
    "--script", "spike/listener.lua",
])

def send(lua: str, timeout=5.0):
    cid = uuid.uuid4().hex
    (QUEUE / f"{cid}.cmd").write_text(lua)
    done = QUEUE / f"{cid}.done"
    deadline = time.time() + timeout
    while time.time() < deadline:
        if done.exists():
            payload = json.loads(done.read_text())
            done.unlink()
            return payload
        time.sleep(0.01)
    raise TimeoutError(f"command {cid} timed out")

time.sleep(2)  # let Aseprite boot
print(send('local s = Sprite(32,32); return "\\"created\\""'))
```

### 1.2 Spike exit criteria

You may proceed only when all five are true:

- [ ] Aseprite starts, the listener runs, and does not peg a CPU core
- [ ] A command round-trips in **< 50 ms**
- [ ] An erroring command returns a structured error instead of killing the listener
- [ ] 200 sequential commands run without the listener dying or leaking files
- [ ] The process survives being idle for 10 minutes

If the resident model fails, fall back to **CLI batch spawns** (§3.4) — slower (~0.5s/call) but dead simple. Ship that first; the resident bridge is an optimization behind the same interface.

---

## 2. Repo layout

```
aseprite-mcp/
├── pyproject.toml
├── README.md
├── src/aseprite_mcp/
│   ├── __init__.py
│   ├── server.py            # MCPServer instance, lifespan, tool registration
│   ├── discovery.py         # binary + version detection
│   ├── bridge/
│   │   ├── __init__.py
│   │   ├── base.py          # AsepriteBridge protocol
│   │   ├── resident.py      # long-lived process + queue
│   │   ├── batch.py         # CLI spawn fallback
│   │   └── lua/
│   │       ├── listener.lua
│   │       └── prelude.lua  # shared helpers injected into every command
│   ├── tools/
│   │   ├── document.py      # create/open/save/info
│   │   ├── drawing.py       # draw_grid, draw_shape, fill
│   │   ├── palette.py       # set_palette, presets, ramps
│   │   ├── structure.py     # layers, frames, tags
│   │   ├── reference.py     # import_reference
│   │   ├── export.py
│   │   └── escape.py        # run_lua
│   ├── render.py            # preview rendering, upscale, grid overlay
│   ├── palettes/            # bundled .json palette presets
│   │   ├── pico8.json
│   │   ├── db16.json
│   │   └── ...
│   ├── prompts/             # MCP prompt templates
│   ├── resources/
│   │   └── pixel_art_guide.md
│   ├── errors.py            # structured error types
│   ├── validation.py        # path jail, bounds, palette checks
│   └── state.py             # session state (open sprite handles)
└── tests/
    ├── test_bridge.py
    ├── test_tools.py
    ├── test_validation.py
    └── golden/              # reference PNGs for regression
```

---

## 3. Phase 1 — The Aseprite bridge

### 3.1 The interface

Every backend implements one protocol so the server never cares which is in use.

```python
# src/aseprite_mcp/bridge/base.py
from typing import Protocol, Any

class AsepriteBridge(Protocol):
    def execute(self, lua: str, timeout: float = 10.0) -> Any:
        """Run a Lua chunk. Returns the decoded JSON result.
        Raises AsepriteError on Lua-side failure."""
    def start(self) -> None: ...
    def stop(self) -> None: ...
    @property
    def alive(self) -> bool: ...
```

### 3.2 The Lua prelude

Prepend this to every command. It gives you JSON encoding, transaction wrapping, and consistent error shapes — write it once instead of in every tool.

```lua
-- prelude.lua (concatenated ahead of each command chunk)
local J = {}

function J.encode(v)
  local t = type(v)
  if v == nil then return "null"
  elseif t == "boolean" then return tostring(v)
  elseif t == "number" then return tostring(v)
  elseif t == "string" then return string.format("%q", v)
  elseif t == "table" then
    if #v > 0 or next(v) == nil then
      local parts = {}
      for _, item in ipairs(v) do parts[#parts+1] = J.encode(item) end
      return "[" .. table.concat(parts, ",") .. "]"
    else
      local parts = {}
      for k, val in pairs(v) do
        parts[#parts+1] = string.format("%q", tostring(k)) .. ":" .. J.encode(val)
      end
      return "{" .. table.concat(parts, ",") .. "}"
    end
  end
  return "null"
end

-- Resolve the sprite the tool should act on.
function J.sprite(id)
  if id then
    for _, s in ipairs(app.sprites) do
      if s.filename == id then return s end
    end
    error("sprite_not_found: " .. tostring(id))
  end
  if not app.sprite then error("no_active_sprite") end
  return app.sprite
end

-- Wrap mutations so a failure rolls back cleanly and yields one undo step.
function J.tx(fn)
  local result
  app.transaction(function() result = fn() end)
  return result
end

return J
```

### 3.3 Resident bridge

```python
# src/aseprite_mcp/bridge/resident.py
import json, subprocess, time, uuid, threading
from pathlib import Path
from .base import AsepriteBridge
from ..errors import AsepriteError, BridgeTimeout

PRELUDE = (Path(__file__).parent / "lua" / "prelude.lua").read_text()

class ResidentBridge:
    def __init__(self, exe: Path, runtime: Path, headless: bool = False):
        self.exe, self.runtime, self.headless = exe, runtime, headless
        self.queue = runtime / "queue"
        self.queue.mkdir(parents=True, exist_ok=True)
        self._proc = None
        self._lock = threading.Lock()

    def start(self):
        listener = Path(__file__).parent / "lua" / "listener.lua"
        cmd = [str(self.exe)]
        if self.headless:
            cmd.append("--batch")
        cmd += ["--script-param", f"queue={self.queue}", "--script", str(listener)]
        self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self._wait_ready(timeout=20)

    def _wait_ready(self, timeout):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                self.execute("return 1", timeout=1.0)
                return
            except Exception:
                time.sleep(0.25)
        raise RuntimeError("Aseprite bridge failed to become ready")

    def execute(self, lua: str, timeout: float = 10.0):
        with self._lock:                      # serialize: Aseprite is single-threaded
            cid = uuid.uuid4().hex
            chunk = f"local J = (function()\n{PRELUDE}\nend)()\n{lua}"
            tmp = self.queue / f"{cid}.tmp"
            tmp.write_text(chunk, encoding="utf-8")
            tmp.rename(self.queue / f"{cid}.cmd")   # atomic: avoid partial reads

            done = self.queue / f"{cid}.done"
            deadline = time.time() + timeout
            while time.time() < deadline:
                if done.exists():
                    payload = json.loads(done.read_text(encoding="utf-8"))
                    done.unlink(missing_ok=True)
                    if not payload.get("ok"):
                        raise AsepriteError(payload.get("error", "unknown"))
                    return payload.get("result")
                if self._proc and self._proc.poll() is not None:
                    raise AsepriteError("Aseprite process died")
                time.sleep(0.005)
            raise BridgeTimeout(f"command timed out after {timeout}s")

    @property
    def alive(self):
        return self._proc is not None and self._proc.poll() is None

    def stop(self):
        if self._proc:
            self._proc.terminate()
            try: self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired: self._proc.kill()
```

**Critical details**

- **Write to `.tmp` then rename.** Without the atomic rename the Lua side will occasionally read a half-written file.
- **Serialize with a lock.** Aseprite executes one script at a time; concurrent commands corrupt state.
- **Detect a dead process** inside the wait loop, or a crash becomes a 10-second hang.

### 3.4 Batch fallback

Simpler, slower, more robust. Same interface. Keep it — it's your CI backend and your bug-report reproduction path.

```python
# src/aseprite_mcp/bridge/batch.py
class BatchBridge:
    def execute(self, lua: str, timeout: float = 30.0):
        chunk = f"local J = (function()\n{PRELUDE}\nend)()\n" + lua
        with tempfile.NamedTemporaryFile("w", suffix=".lua", delete=False) as f:
            f.write(chunk + '\nprint("__RESULT__" .. J.encode(__out or nil))')
            script = f.name
        try:
            r = subprocess.run(
                [str(self.exe), "--batch", "--script", script],
                capture_output=True, text=True, timeout=timeout,
            )
            for line in r.stdout.splitlines():
                if line.startswith("__RESULT__"):
                    return json.loads(line[len("__RESULT__"):])
            raise AsepriteError(r.stderr.strip() or "no result marker in output")
        finally:
            os.unlink(script)
```

Batch mode caveats: `app.sprite` is usually `nil`, so each command must `app.open(path)` itself; `Dialog()` returns nil; state does not persist across calls, so **every batch command must open → mutate → save**.

### 3.5 Backend selection

```python
def make_bridge(config) -> AsepriteBridge:
    if config.backend == "batch":
        return BatchBridge(...)
    try:
        b = ResidentBridge(..., headless=config.headless)
        b.start()
        return b
    except Exception as e:
        log.warning("resident bridge failed (%s), falling back to batch", e)
        return BatchBridge(...)
```

---

## 4. Phase 2 — MCP server skeleton

```python
# src/aseprite_mcp/server.py
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
        state.bridge.stop()          # never orphan Aseprite


mcp = MCPServer("aseprite", lifespan=lifespan)

from .tools import document, drawing, palette, structure, reference, export, escape
for module in (document, drawing, palette, structure, reference, export, escape):
    module.register(mcp)

from . import prompts, resources
prompts.register(mcp)
resources.register(mcp)

def main():
    mcp.run(transport="stdio")
```

Tools reach the bridge through **resolved dependencies**, not globals and not a `get_bridge`
closure threaded through `register()`. A resolved parameter is invisible to the model — it never
appears in the input schema — and an invalid dependency graph fails at server startup rather than
on the first call:

```python
# src/aseprite_mcp/deps.py
from typing import Annotated
from mcp.server.mcpserver import Context, Resolve

from .bridge import make_bridge
from .bridge.base import AsepriteBridge
from .state import SessionState


async def _session(ctx: Context[SessionState]) -> SessionState:
    return ctx.request_context.lifespan_context

async def _bridge(ctx: Context[SessionState]) -> AsepriteBridge:
    st = ctx.request_context.lifespan_context
    if not st.bridge.alive:                       # restart once, report it
        st.bridge = make_bridge(st.config)
        st.bridge.start()
    return st.bridge

# Import these two aliases in every tool module.
Session = Annotated[SessionState, Resolve(_session)]
Bridge = Annotated[AsepriteBridge, Resolve(_bridge)]
```

A tool then reads:

```python
@mcp.tool()
async def draw_grid(grid: str, bridge: Bridge, session: Session, x: int = 0, y: int = 0) -> ...:
```

`grid`, `x` and `y` are in the schema; `bridge` and `session` are not.

### 4.1 Session state

```python
# src/aseprite_mcp/state.py
from dataclasses import dataclass, field

@dataclass
class SpriteHandle:
    path: str            # absolute path inside the workspace
    width: int
    height: int
    color_mode: str
    palette: list[str]   # hex strings, index-ordered
    palette_names: dict[int, str] = field(default_factory=dict)  # 3 -> "skin_shadow"

@dataclass
class SessionState:
    """Yielded by the lifespan; reached via ctx.request_context.lifespan_context (§4)."""
    config: Config
    bridge: AsepriteBridge | None = None
    sprites: dict[str, SpriteHandle] = field(default_factory=dict)
    active: str | None = None
```

Keeping the palette **server-side** is what lets `draw_grid` accept indices and lets you validate before touching Aseprite. It's the small piece of state that buys you the most.

---

## 5. Phase 3 — Core tools

Build in this order. Each is shippable.

### 5.0 Return-shape convention

Under SDK 2.x the **return annotation is the output schema**, and an `MCPImage` result forces
`structured_content = None`. That splits every tool into exactly two kinds. Pick one per tool; never
try to be both.

| Kind | Tools | Returns | Schema |
|---|---|---|---|
| **Mutating** — returns a preview | `create_sprite`, `draw_grid`, `draw_shape`, `fill`, `layers`, `frames`, `tags`, `set_palette`, `import_reference`, `export`, `run_lua`, `undo`, `redo` | `list[str \| MCPImage]` via `emit()` | none (`structured_output=False`) |
| **Read-only** — returns data | `get_sprite_info`, `get_region_as_grid`, `get_ramp` | a `TypedDict` / dataclass / pydantic model | derived from the annotation |

Never annotate a tool `-> dict`. A bare `dict` publishes a shapeless object schema and gives the
model no idea what comes back. The signatures below omit the `bridge: Bridge, session: Session`
resolved parameters (§4) for readability — every tool takes them, and the model never sees them.

Where a tool genuinely wants both an image and a table (`set_palette`'s swatch strip, `get_ramp`'s
ramp), the table goes in the text block as rendered markdown, not as structured content. The model
reads `content`; it is the only part it sees.

### 5.1 `create_sprite`

```python
@mcp.tool()
def create_sprite(
    name: str,
    width: int,
    height: int,
    color_mode: Literal["indexed", "rgb", "grayscale"] = "indexed",
    palette: str | list[str] = "db16",
) -> list[str | MCPImage]:
    """Create a new sprite and make it active.

    For character sprites, 16x16 / 32x32 / 48x48 are typical.
    Prefer indexed color mode with a constrained palette — it produces
    much better pixel art than free RGB.
    """
```

Validate: `1 <= width, height <= 1024` (guard against a model asking for 4096×4096 and blowing up preview cost). Warn above 128×128 that pixel art gets hard to control.

Lua:

```lua
local spr = Sprite(%(w)d, %(h)d, ColorMode.%(mode)s)
spr.filename = %(path)q
%(palette_lua)s
spr:saveAs(%(path)q)
return J.encode{ path = spr.filename, width = spr.width, height = spr.height }
```

Return shape — content blocks, not a dict (§5.0). Once Phase 4 lands the preview is the second block:

```python
[
  "Created knight.aseprite — 32x32 indexed, palette db16 (16 colors).\n"
  "| idx | hex | name |\n|---|---|---|\n"
  "| 0 | #00000000 | transparent |\n| 1 | #140c1c | black |\n...",
  MCPImage(data=png, format="png"),
]
```

The palette table is **markdown inside the text block**, not structured content — an `MCPImage` in
the result nulls `structured_content`, and text is the only channel the model reads anyway.

### 5.2 `get_sprite_info`

Cheap, read-only, and the model's way to re-orient after context loss. Return dimensions, color mode, palette with indices and names, layers (name/index/visible/opacity/blend), frames with durations, tags, and the active layer/frame.

### 5.3 `draw_grid` — the workhorse

```python
@mcp.tool()
def draw_grid(
    grid: str,
    x: int = 0,
    y: int = 0,
    layer: str | None = None,
    frame: int = 1,
    legend: dict[str, int] | None = None,
) -> list[str | MCPImage]:
    """Draw a rectangular block of pixels from a text grid.

    Each line is a row; each character is one pixel. Characters map to
    palette indices via `legend`, defaulting to: '.' = transparent,
    '0'-'9' = palette index 0-9, 'a'-'z' = index 10-35.

    Example (a 7x5 face using palette indices 1 and 2):
        ..111..
        .11211.
        1122211
        .11211.
        ..111..

    All rows must be the same length. This is the preferred way to draw —
    much more reliable than individual pixel calls.
    """
```

Validation, in this order, all *before* touching Aseprite:

1. Strip blank leading/trailing lines; reject empty grids
2. All rows equal length → else `grid_rows_uneven` with the offending row numbers and their lengths
3. Every character is in the legend → else `unknown_grid_char` listing the bad chars and the valid legend
4. Every mapped index `< len(palette)` → else `palette_index_out_of_range` with the palette size
5. `x, y` plus grid dimensions within canvas bounds → else `out_of_bounds` with the canvas size and the overhang

Generate Lua as a single batched pixel write inside one transaction:

```lua
local spr = J.sprite(%(sprite)q)
local cel = ... -- resolve or create cel for layer/frame
J.tx(function()
  local img = cel.image:clone()
  local px = { %(pixels)s }   -- flat list: x1,y1,i1, x2,y2,i2, ...
  for k = 1, #px, 3 do
    img:drawPixel(px[k], px[k+1], px[k+2])
  end
  cel.image = img
end)
```

> Clone → mutate → assign, rather than mutating `cel.image` in place. In-place edits on a shared image can bleed into linked cels.

### 5.4 `draw_shape`

One tool, `shape` enum — this is the consolidation that keeps the tool count down.

```python
@mcp.tool()
def draw_shape(
    shape: Literal["line", "rect", "ellipse", "polyline", "point"],
    points: list[list[int]],          # [[x,y], ...] — 2 for line/rect/ellipse
    color: int | str,                  # palette index or name
    filled: bool = False,
    thickness: int = 1,
    layer: str | None = None,
    frame: int = 1,
    mirror: Literal["none","horizontal","vertical","both"] = "none",
) -> list[str | MCPImage]:
    """Draw a geometric primitive. Use `mirror` for symmetric characters —
    drawing half a face and mirroring is far more reliable than drawing both halves."""
```

The `mirror` parameter deserves emphasis: symmetry is where hand-plotted LLM sprites fall apart, and it's free to enforce here.

Implement with `app.useTool{}` so strokes match what the built-in tools produce:

```lua
app.useTool{
  tool = "line", color = Color{ index = %(idx)d },
  points = { Point(%(x1)d,%(y1)d), Point(%(x2)d,%(y2)d) },
  brush = Brush(%(thickness)d),
  layer = lyr, frame = frm,
}
```

### 5.5 `fill`

Flood fill via `app.useTool{ tool = "paint_bucket", ... }`. Expose `tolerance` and `contiguous`.

### 5.6 `layers` / `frames` / `tags`

Three tools, each with an `action` enum. This collapses ~20 CRUD tools into 3.

```python
@mcp.tool()
def layers(
    action: Literal["add","delete","rename","reorder","set","list","duplicate","merge_down"],
    name: str | None = None,
    new_name: str | None = None,
    index: int | None = None,
    visible: bool | None = None,
    opacity: int | None = None,       # 0-255
    blend_mode: str | None = None,
    is_group: bool = False,
) -> list[str | MCPImage]:
    """Manage layers. Use action='add' with is_group=True for layer groups."""
```

Dispatch to private handlers server-side; keep the model-facing surface flat. Validate that the required params for each action are present and return `missing_parameter` naming exactly which one.

### 5.7 `export`

```python
@mcp.tool()
def export(
    format: Literal["png","gif","spritesheet"],
    path: str | None = None,
    scale: int = 1,
    sheet_type: Literal["horizontal","vertical","rows","columns","packed"] = "horizontal",
    include_json: bool = True,
    trim: bool = False,
    padding: int = 0,
) -> list[str | MCPImage]:
```

Spritesheets go through `--sheet`/`--data` on a batch invocation rather than the resident bridge — the CLI export path is better tested than the scripted equivalent.

### 5.8 `run_lua` — the escape hatch

```python
@mcp.tool()
def run_lua(script: str, preview: bool = True, timeout: float = 15.0) -> list[str | MCPImage]:
    """Execute arbitrary Lua against the active sprite. Use when no other
    tool covers what you need. Runs inside a transaction (rolled back on error).
    The full Aseprite Lua API is available; `J.sprite()` returns the active sprite."""
```

This one tool means a missing wrapper never blocks a user. Sandbox it per §10.2.

---

## 6. Phase 4 — The vision loop

**This is the phase that differentiates the project.** Do not defer it.

### 6.1 Preview rendering

```python
# src/aseprite_mcp/render.py
import base64, io
from PIL import Image

def render_preview(bridge, sprite_path, frame=1, scale="auto",
                   max_dim=512, grid_overlay=False) -> tuple[bytes, dict]:
    tmp = previews_dir / f"{uuid4().hex}.png"
    bridge.execute(f'''
        local spr = J.sprite({sprite_path!r})
        local img = Image(spr.width, spr.height, spr.colorMode)
        img:drawSprite(spr, {frame})
        img:saveAs({str(tmp)!r})
        return J.encode{{ w = spr.width, h = spr.height }}
    ''')
    im = Image.open(tmp).convert("RGBA")
    if scale == "auto":
        scale = max(1, min(16, max_dim // max(im.width, im.height)))
    im = im.resize((im.width*scale, im.height*scale), Image.NEAREST)  # NEAREST is mandatory
    if grid_overlay:
        im = draw_grid_overlay(im, scale)
    buf = io.BytesIO(); im.save(buf, "PNG")
    return buf.getvalue(), {"scale": scale, "native": (im.width//scale, im.height//scale)}
```

**Non-negotiables:**

- `Image.NEAREST` — bilinear resampling turns pixel art to mush and the model will "see" anti-aliasing that isn't there.
- Upscale to roughly 256–512px. A raw 32×32 PNG is close to unreadable to a vision encoder.
- Cap total preview size; a 1024×1024 preview at scale 4 is a very expensive image token bill.

### 6.2 Returning it as MCP image content

```python
from mcp.server.mcpserver import Image as MCPImage   # NOT `Image` — PIL owns that name here

def preview_image(png: bytes) -> MCPImage:
    return MCPImage(data=png, format="png")   # or MCPImage(path=...) to read from disk
```

> **Alias the SDK's `Image`.** `render.py` and `reference.py` use PIL's `Image` heavily
> (`Image.open`, `Image.NEAREST`, `Image.Image` annotations). Importing the SDK's `Image` unaliased
> into those modules shadows PIL and breaks them in confusing ways. PIL keeps the bare name.

### 6.3 Auto-preview on every mutation

The decisive design choice — but note the constraint that shapes it:

> **A tool result is a list of content blocks, and an `Image` result has `structured_content = None`.**
> An image is content for the model to look at, not data for the application to parse, so it cannot
> be a field inside a returned dict. Mutating tools therefore return `list[str | Image]` and carry no
> output schema. Read-only data tools (`get_sprite_info`) keep their schema and return no image.

Do this with a plain helper, **not a decorator**. `functools.wraps` sets `__wrapped__`, and
`inspect.signature` follows it — so a wrapper that injects a `preview` kwarg is invisible to the
SDK's schema generation, and the model never learns the parameter exists.

```python
def emit(bridge, session, summary: str, preview: bool = True) -> list[str | MCPImage]:
    blocks: list[str | MCPImage] = [summary]
    if preview and session.active:
        png, meta = render_preview(bridge, session.active, scale="auto")
        blocks.append(f"preview: {meta['native'][0]}x{meta['native'][1]} at {meta['scale']}x")
        blocks.append(preview_image(png))
    return blocks
```

Each mutating tool declares `preview: bool = True` in its own signature — so it lands in the schema —
and ends with `return emit(bridge, session, "wrote 412 pixels", preview)`.

The model then sees the consequence of every action without having to remember to look. That single behavior is the difference between output that looks like pixel art and output that looks like noise.

Add a `preview=False` opt-out so a long scripted sequence doesn't return 40 images.

### 6.4 The grid readback

```python
@mcp.tool()
def get_region_as_grid(
    bridge: Bridge, session: Session,
    x: int = 0, y: int = 0,
    width: int | None = None, height: int | None = None, frame: int = 1,
) -> RegionGrid:            # TypedDict: {grid: str, x: int, y: int, width: int, height: int}
    """Read a canvas region back as a palette-index text grid — the same
    format `draw_grid` accepts. Use this to inspect and edit existing pixels."""
```

Returning **both** the rendered image and the text grid measurably improves the model's spatial judgement. The image conveys gestalt; the grid conveys exact coordinates. Neither alone is sufficient.

### 6.5 Preview ergonomics

- Include a **coordinate ruler** option on the overlay (ticks every 8px) so the model can convert what it sees into coordinates.
- For animations, offer `render_preview(all_frames=True)` producing a contact sheet with frame numbers.
- Include an alpha checkerboard so transparent ≠ black in the model's eyes.

---

## 7. Phase 5 — Palette system

### 7.1 Bundled presets

Ship as JSON in `palettes/`: `pico8` (16), `db16`, `db32`, `aap64`, `endesga32`, `nes` (54), `gameboy` (4), `cga` (16), `sweetie16`, `resurrect64`.

```json
{"name": "PICO-8", "colors": ["#000000","#1D2B53","#7E2553","#008751", "..."],
 "notes": "Classic 16-color fantasy console palette. Good for chunky, high-contrast sprites."}
```

The `notes` field goes back to the model — it helps it pick well.

### 7.2 `set_palette`

```python
@mcp.tool()
def set_palette(
    palette: str | list[str],
    names: dict[int, str] | None = None,
    preserve_indices: bool = False,
) -> list[str | MCPImage]:
    """Set the sprite palette from a preset name or explicit hex list.

    `names` lets you label indices semantically ({3: "skin_shadow"}) so later
    drawing calls can reference colors by name instead of number.
    Index 0 should normally be transparent."""
```

Always return the resulting palette **as a rendered swatch strip image** plus the index/hex/name table. The model choosing colors it can see is materially better than it choosing from hex strings.

### 7.3 `get_ramp`

```python
@mcp.tool()
def get_ramp(base_color: str, steps: int = 5, hue_shift: float = 15.0) -> Ramp:
    """Generate a shading ramp from a base color. Applies hue-shifting
    (shadows toward blue/purple, highlights toward yellow) — the standard
    pixel-art technique that makes shading look intentional rather than
    like a brightness slider."""
```

Implement in HSL/OKLab: darken → shift hue cool, lighten → shift hue warm, and compress saturation at the extremes. Encoding this craft knowledge in a tool is worth more than any amount of prompt instruction.

---

## 8. Phase 6 — Reference image pipeline

Promoted into v1. This is how people actually work, and it sidesteps the from-scratch spatial reasoning problem.

### 8.1 `import_reference`

```python
@mcp.tool()
def import_reference(
    image_path: str,
    target_width: int | None = None,
    target_height: int | None = None,
    mode: Literal["trace","quantize","both"] = "both",
    remove_background: bool = True,
    layer_name: str = "reference",
    locked: bool = True,
    opacity: int = 128,
) -> list[str | MCPImage]:
    """Import a reference image, downscale it to the sprite grid and quantize
    it to the current palette. Creates a locked, semi-transparent reference
    layer to draw over, and optionally a quantized starting point."""
```

### 8.2 The pipeline

1. **Load and orient** — Pillow, honour EXIF rotation, convert to RGBA.
2. **Background removal** (optional) — flood-fill from the corners with a tolerance; or if the image has alpha already, trust it.
3. **Crop to content** — bounding box of non-transparent pixels; this is what makes the subject fill the sprite instead of floating in a sea of margin.
4. **Downscale** — `Image.LANCZOS` down to roughly 2× the target, then `Image.NEAREST` for the last step. Straight-to-NEAREST loses detail; straight-to-LANCZOS produces mud.
5. **Quantize to palette** — map each pixel to the nearest palette color in **OKLab or CIELAB**, not RGB Euclidean. RGB distance produces visibly wrong hue choices.
6. **Optional dithering** — Floyd–Steinberg is usually *wrong* for sprite work (it produces noise at 32×32). Default off; offer `dither="none"|"bayer2x2"|"bayer4x4"`.
7. **Edge cleanup** — remove orphan single pixels, since downscaling generates them liberally.
8. **Write two layers** — `reference` (the LANCZOS downscale at low opacity, locked) and optionally `reference_quantized` (the palette-mapped version) as a starting point the model refines.

```python
def quantize_to_palette(im: Image.Image, palette_hex: list[str]) -> np.ndarray:
    lab_pal = np.array([rgb_to_oklab(hex_to_rgb(h)) for h in palette_hex])
    arr = np.array(im.convert("RGB"), dtype=float) / 255.0
    lab = rgb_to_oklab_array(arr)                       # (H, W, 3)
    d = ((lab[:, :, None, :] - lab_pal[None, None]) ** 2).sum(-1)
    return d.argmin(-1)                                 # (H, W) palette indices
```

### 8.3 Returning it usefully

Return the reference **as a grid** alongside the preview, so the model can read exact indices off it and selectively copy regions into the working layer. That turns "draw a character" into "refine this rough" — a far easier task.

---

## 9. Phase 7 — Prompts and resources

Most MCP servers ship tools only and leave a lot on the table.

### 9.1 Prompts (workflow templates)

`sprite-character`:

```
Create a {size}x{size} character sprite: {description}

Follow this order — do not skip steps:
1. create_sprite({size}, {size}, indexed, palette={palette})
2. get_ramp for each material (skin, cloth, metal) and name the indices
3. Block the SILHOUETTE only, in one flat mid-tone, using draw_grid.
   Check the preview. A good sprite is readable as a black silhouette.
4. Add base colors within the silhouette.
5. Shade: pick one light direction and state it. Use ramps, never
   arbitrary colors. Avoid pillow shading (uniform edge darkening).
6. Add a selective outline — darker version of the adjacent color,
   not pure black everywhere.
7. Add highlights sparingly — 2-3 pixels of the lightest tone.
8. render_preview and critique your own work against the description.
   Fix the single worst problem. Repeat twice.
```

Also worth shipping: `sprite-tileset`, `animate-walkcycle`, `from-reference`, `palette-explore`.

The silhouette-first ordering matters more than any other single instruction — it's the standard professional workflow and it front-loads the decision the model is worst at.

### 9.2 Resources

- `aseprite://palettes` — all bundled presets with swatch previews
- `aseprite://sprite/current` — live preview image of the working sprite
- `aseprite://guide/pixel-art` — the craft rules (below)
- `aseprite://guide/lua-api` — a condensed API cheat sheet for `run_lua`

`pixel_art_guide.md` should cover, concretely: readable silhouettes; one consistent light source; hue-shifted ramps instead of brightness ramps; selective outlining vs full black outlines; avoiding pillow shading; avoiding noise/orphan pixels; using few colors deliberately; anti-aliasing only at low-contrast joins; consistent pixel density (no mixed "resolutions" in one sprite); readable at 1×, not just zoomed.

The model's default pixel art is bad in *specific, nameable ways*. Naming them in a resource it can read fixes a surprising amount.

---

## 10. Phase 8 — Safety and validation

### 10.1 Path jail

```python
def safe_path(user_path: str, workspace: Path) -> Path:
    p = (workspace / user_path).resolve()
    if not p.is_relative_to(workspace.resolve()):
        raise ValidationError("path_outside_workspace",
            f"Paths must be inside the workspace. Got: {user_path}")
    return p
```

Apply to **every** path parameter, including `import_reference` (with a documented, explicit opt-in for reading references from outside the workspace, since that's a legitimate need).

### 10.2 Lua injection

Never f-string user values into Lua. Two defenses:

1. Type-validate and coerce every scalar (ints stay ints, colors validated against `^#[0-9a-fA-F]{6,8}$`).
2. Pass strings through a proper Lua long-bracket or `%q` escape, never bare quotes.

```python
def lua_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'
```

For `run_lua`, accept that the model is executing arbitrary code — that's the point — but bound it: strip/shadow `os.execute`, `os.remove`, `io.popen`; enforce a timeout; run inside a transaction; log every script executed.

### 10.3 Structured errors

```python
# src/aseprite_mcp/errors.py
@dataclass
class ToolError(Exception):
    """Model-visible failure. RAISE this — never return it.

    Raising an ordinary exception yields a result with is_error=True and __str__ in
    `content`, which the model reads and corrects. RETURNING an error dict yields
    is_error=False — it reads as success to the client and the loop never closes.
    """
    code: str
    message: str
    hint: str | None = None
    context: dict = field(default_factory=dict)

    def __str__(self) -> str:
        out = [f"{self.code}: {self.message}"]
        if self.hint:
            out.append(f"Hint: {self.hint}")
        if self.context:
            out.append(json.dumps(self.context))
        return " ".join(out)
```

`ToolError` must **not** subclass `MCPError`. `MCPError` is a protocol-level failure: it bypasses
the model entirely and fails the JSON-RPC request, so the model never sees it and cannot retry.
The test is *"could a smarter model have avoided this?"*

- **Yes → `ToolError`**: uneven grid, out-of-range palette index, out-of-bounds coordinates, unknown layer name.
- **No → `MCPError`**: Aseprite binary not found, bridge dead beyond recovery, workspace unwritable.

Since `__str__` is all the model gets, every error must answer "what do I do differently next time".
These are the payloads that message renders from:

```json
{"error": "palette_index_out_of_range",
 "message": "Grid references palette index 9 but the palette has 8 colors (0-7).",
 "hint": "Either use an index 0-7, or call set_palette with more colors first.",
 "context": {"bad_indices": [9, 11], "palette_size": 8}}
```

```json
{"error": "grid_rows_uneven",
 "message": "All grid rows must be the same length.",
 "hint": "Row 3 has 7 characters but rows 1-2 have 8. Pad with '.' for transparent.",
 "context": {"row_lengths": {"1": 8, "2": 8, "3": 7, "4": 8}}}
```

### 10.4 Undo safety

Every mutating tool wraps in `J.tx()`. Expose:

```python
@mcp.tool()
def undo(steps: int = 1) -> list[str | MCPImage]: ...
@mcp.tool()
def redo(steps: int = 1) -> list[str | MCPImage]: ...
```

`app.undo()` per step. This changes the risk calculus for the user enough to be worth the two tools.

### 10.5 Resource limits

- Canvas: reject `> 1024` in either dimension; warn `> 128`
- `draw_grid`: cap total pixels per call (~65k)
- Preview: cap output at 1024×1024 after upscale
- Command timeout: 10s default, 30s for export
- Bridge restart: if Aseprite dies, restart once automatically and report it

---

## 11. Phase 9 — Testing

### 11.1 Unit — no Aseprite required

Grid parsing, legend mapping, validation errors, path jail, Lua escaping, palette quantization, ramp generation. Fast, run on every commit.

### 11.2 Integration — real Aseprite

Drive the server through the SDK's in-memory `Client` rather than calling tool functions directly.
It runs the real lifespan, resolves the real dependencies, and validates results against the
published schemas — so a broken annotation fails here instead of in a user's client. (The v1 helper
`create_connected_server_and_client_session` was removed in SDK 2.0.)

```python
import pytest
from mcp.client import Client
from aseprite_mcp.server import mcp

@pytest.fixture
async def client():
    async with Client(mcp) as c:      # lifespan starts/stops Aseprite around the block
        yield c

async def test_draw_grid_writes_expected_pixels(client, tmp_sprite):
    await client.call_tool("draw_grid",
                           {"grid": "..1..\n.111.\n..1..", "x": 0, "y": 0, "preview": False})
    out = await client.call_tool("get_region_as_grid",
                                 {"x": 0, "y": 0, "width": 5, "height": 3})
    assert out.structured_content["grid"] == "..1..\n.111.\n..1.."

async def test_bad_grid_is_model_visible(client, tmp_sprite):
    out = await client.call_tool("draw_grid", {"grid": "..1..\n.11\n..1.."})
    assert out.is_error                       # NOT a raised MCPError
    assert "grid_rows_uneven" in str(out.content[0])
```

The `draw_grid` → `get_region_as_grid` round-trip is your single most valuable test: it exercises the bridge, transactions, palette mapping, and coordinates in one assertion. The second test is the one that catches an error accidentally raised as `MCPError` or returned as a dict — both of which break the model's correction loop silently.

### 11.3 Golden-image regression

Render known sprites to PNG and compare against committed goldens with a small pixel-difference tolerance. Catches Aseprite version drift, which will otherwise bite you silently.

### 11.4 Bridge stress

500 sequential commands; kill Aseprite mid-command and assert clean recovery; concurrent tool calls assert serialization; 30-minute idle then a command.

### 11.5 LLM eval harness

The one people skip, and the one that tells you if the product works. Fixed set of prompts ("32×32 knight facing right, DB16 palette", "8-frame coin spin", "16×16 tree, PICO-8"), run end to end, then score the output — via a vision model judge shown both the render and the ASCII grid, on: silhouette readability, palette adherence, shading coherence, absence of orphan pixels, and match to the prompt.

Run it against every meaningful change. It's the only way to know whether e.g. the auto-preview or the silhouette-first prompt actually helped, rather than assuming.

### 11.6 CI

GitHub Actions matrix: Linux (Aseprite built from source, `xvfb-run`), macOS, Windows. Unit tests everywhere; integration only where a binary is available. Building Aseprite from source in CI takes ~15 min — cache aggressively.

---

## 12. Phase 10 — Packaging and distribution

### 12.1 `pyproject.toml`

```toml
[project]
name = "aseprite-mcp"
requires-python = ">=3.11"
dependencies = ["mcp>=2.0", "pillow>=10", "numpy>=1.26", "pydantic>=2.12"]
# Note: the SDK depends on httpx2, not httpx. Do not add httpx.

[project.scripts]
aseprite-mcp = "aseprite_mcp.server:main"
```

### 12.2 Client config

```json
{
  "mcpServers": {
    "aseprite": {
      "command": "uvx",
      "args": ["aseprite-mcp"],
      "env": {
        "ASEPRITE_PATH": "/Applications/Aseprite.app/Contents/MacOS/aseprite",
        "ASEPRITE_MCP_WORKSPACE": "~/pixel-art"
      }
    }
  }
}
```

`uvx` means zero-install for the user — worth optimizing for.

### 12.3 README essentials

Supported Aseprite versions; the 60-second quickstart; a **screenshot or GIF of the live GUI drawing itself** (this is the demo that sells it); the tool table; troubleshooting for the three failure modes you'll actually get (binary not found, bridge timeout, version mismatch).

### 12.4 Diagnostics

```bash
aseprite-mcp --doctor
```

Prints: binary path and version, workspace path and writability, bridge backend selected, a round-trip latency measurement, and a rendered test sprite. Turns 90% of bug reports into self-service.

---

## 13. Licensing and engaging the Aseprite devs

*Not legal advice — general guidance based on the publicly posted terms as of August 2026. Verify against the current text of each document before relying on this.*

### 13.1 Two separate licenses are in play

This project touches two different licensing questions that are easy to conflate and should be kept separate in the README and in any conversation with Aseprite's maintainers:

1. **The license on the code you write** (the MCP server, the Lua bridge scripts, the palette presets) — entirely your choice.
2. **The terms governing Aseprite itself** — you don't choose these, you comply with them.

### 13.2 Licensing your own code

Recommendation: **MIT**, or **Apache 2.0** if you want the explicit patent grant (more relevant once there are multiple contributors touching the reference-quantization or bridge internals).

- Ownership: under Anthropic's Commercial Terms of Service (anthropic.com/legal/commercial-terms), you own the output Claude helps you write, with no restriction on how you license or redistribute it. Anthropic assigns its rights in the output to you, conditional on your own compliance with the usage policy. Anthropic's indemnification does not extend to infringement introduced by you or by third-party content you combine with the output — the standard due diligence on any dependency you pull in (Pillow, numpy, the MCP SDK) is still yours to do.
- No obligation to credit Anthropic/Claude in the license text; a line in the README acknowledging it was built with Claude's help is common courtesy, not a requirement.
- Add a `LICENSE` file at repo root (plain MIT text, your name, current year) and an `SPDX-License-Identifier: MIT` header convention if you want tooling to pick it up automatically.

### 13.3 What the Aseprite EULA actually restricts

Per `github.com/aseprite/aseprite/blob/main/EULA.txt`:

- You **may not redistribute copies of the Aseprite software product itself**. Do not vendor the Aseprite binary in your repo, releases, or Docker image.
- Aseprite's own docs (`aseprite.org/cli/`, `aseprite.org/docs/scripting/`) describe the CLI and Lua API as the intended interfaces for automation and batch workflows — nothing in the cited EULA text prohibits building third-party tooling that drives Aseprite through those documented interfaces.
- Practical consequence for this project: ship the MCP server on its own, require the end user to supply their own licensed Aseprite install via `ASEPRITE_PATH` (already the design in §0.2), and say so explicitly in the README ("Requires your own licensed copy of Aseprite ≥1.3 — not included or bundled").
- If you ever want to ship a convenience Docker image, the same rule applies: the image builds/installs the user's own Aseprite at container-build time (or expects it mounted in), it does not bake in a redistributed copy.

### 13.4 If you want to approach the Aseprite devs

Reasonable, and there's precedent for community tools getting linked from the docs/forum. A few things worth doing before reaching out, in order:

1. **Get to M4** (§ Milestone checklist) before contacting them — a working vision-loop demo is a much stronger pitch than a design doc, and it's the piece that differentiates this from the existing servers they may already be aware of.
2. **Respect the EULA boundary explicitly in the pitch** — lead with "this doesn't bundle or redistribute Aseprite, users bring their own license" so licensing isn't the first question they have to ask.
3. **Where to actually reach them**: the Aseprite community forum (`community.aseprite.org`) is the normal channel for tool announcements and gets read by the maintainers; the GitHub repo (`github.com/aseprite/aseprite`) issue tracker is better for API gaps/bugs you hit while building than for announcing a finished tool. Igara Studio (the company behind Aseprite) doesn't have a public "submit your integration" program as far as the current docs show, so a forum post plus a well-written README is the realistic path to visibility, not a formal submission process.
4. **What "acceptance" could look like in practice**: a link from the community showcase/forum, not necessarily an official endorsement or bundling into the app. Set that expectation for yourself going in.

---

## 14. Milestone checklist

| Milestone | Deliverable | Done when |
|---|---|---|
| **M0** Spike | Working bridge prototype | 200 commands round-trip < 50ms, errors don't kill it |
| **M1** Skeleton | MCPServer + lifespan + discovery + 1 tool | `create_sprite` works from Claude |
| **M2** Draw | `draw_grid`, `draw_shape`, `fill` | Can hand-write a recognizable sprite via tool calls |
| **M3** Vision | `render_preview` + auto-preview | Model sees its own output and self-corrects |
| **M4** Color | Palette presets, `set_palette`, `get_ramp` | Swatch images returned; indexed drawing works |
| **M5** Structure | `layers`, `frames`, `tags` | Can build a 4-frame tagged animation |
| **M6** Reference | `import_reference` | Photo → usable 32×32 quantized starting point |
| **M7** Export | `export` all three formats | Spritesheet + JSON loads in a game engine |
| **M8** Guidance | Prompts + resources | `/sprite-character` produces good output unprompted |
| **M9** Harden | Errors, validation, undo, limits | Fuzzing produces no crashes, only structured errors |
| **M10** Ship | Packaging, README, `--doctor`, CI | `uvx aseprite-mcp` works on a clean machine |

**Suggested order of attack if time is short:** M0 → M1 → M2 → M3 → M4 → stop and evaluate. That's a genuinely usable product and it's where the quality question gets answered. Everything after M4 is breadth; M3 is depth, and depth is what's missing from the existing servers.

---

## 15. Pitfalls reference

| Pitfall | Symptom | Fix |
|---|---|---|
| Non-atomic queue writes | Intermittent Lua parse errors | Write `.tmp`, then `rename` |
| Concurrent commands | Corrupted sprite state, random failures | Serialize with a lock |
| Bilinear preview upscale | Model "sees" anti-aliasing, draws mush | `Image.NEAREST`, always |
| Tiny previews | Model can't resolve pixels, output is noise | Upscale to 256–512px |
| `app.sprite` nil in batch | `no_active_sprite` errors | Always `app.open()` explicitly |
| In-place `cel.image` mutation | Edits bleed across linked cels | Clone → mutate → assign |
| RGB-space quantization | Reference imports have wrong hues | Quantize in OKLab/CIELAB |
| Floyd–Steinberg by default | Noisy, non-pixel-art references | Dithering off by default |
| Undocumented `app.command` params | Silent no-ops | Test each wrapped command; read `gui.xml` |
| Unbounded canvas size | Preview token costs explode | Cap at 1024, warn at 128 |
| f-stringing values into Lua | Injection, syntax errors on quotes/newlines | Type-validate + `%q` escape |
| Aseprite version drift | Tools break after user updates | Pin range, feature-detect, golden tests |
| Cold-spawn per call | ~1s latency, state loss | Resident bridge, batch as fallback |
| One tool per Lua call | Tool bloat, poor selection | `action` enums + `run_lua` escape hatch |
| No preview on mutations | Model draws blind | Auto-preview helper on every mutating tool |
| Returning an error dict | `is_error=False` — reads as success, model never retries | Raise `ToolError`; never return it |
| `ToolError` subclassing `MCPError` | Model never sees the error, request just fails | Subclass `Exception`; `MCPError` only for unfixable state |
| `Image` inside a returned dict | `structured_content` is `None`; schema validation fails | Return `list[str \| Image]` from mutating tools |
| Decorator injecting a tool kwarg | `inspect.signature` follows `__wrapped__`; param missing from schema | Declare `preview: bool = True` on the tool itself |

---

## 16. End-to-end trace

What "create a small main character, 32×32, these colors, this reference" looks like in practice.

```
User: "Make me a 32x32 main character — a small knight with a red cape,
       facing right. Use the DB16 palette. Here's a reference: knight.png"

→ create_sprite(name="knight", width=32, height=32,
                color_mode="indexed", palette="db16")
  ← {path, palette: [16 colors w/ names], preview: <empty canvas>}

→ import_reference(image_path="knight.png", mode="both",
                   remove_background=True, opacity=100)
  ← {preview: <ref layer visible>, grid: "<32x32 quantized index grid>",
     layers: ["reference" (locked), "reference_quantized"]}

→ layers(action="add", name="silhouette")
→ draw_grid(grid="<32 rows blocking the knight's outline>", layer="silhouette")
  ← {preview: <flat silhouette>, pixels_written: 412}

   [model looks at the preview: "the helmet reads as a blob, the cape
    doesn't separate from the body"]

→ draw_shape(shape="line", points=[[18,12],[24,20]], color=0,
             layer="silhouette")     # carve the cape edge
  ← {preview: <silhouette with separated cape>}

→ get_ramp(base_color="#8f3a3a", steps=4)     # cape red
  ← {ramp: [indices], swatch: <image>}

→ layers(action="add", name="color")
→ draw_grid(grid="<base colors within the silhouette>", layer="color")
  ← {preview: <flat-colored knight>}

→ draw_shape(shape="polyline", points=[...], color="cape_shadow",
             layer="color", mirror="none")     # shade, light from upper-left
  ← {preview: <shaded knight>}

→ draw_shape(... highlights ...)
→ export(format="png", scale=1)
  ← {path: "~/pixel-art/knight.png", preview: <final>}
```

Nine tool calls, three of which are the model looking at its own work and fixing it. That feedback loop — not the tool count — is the product.
