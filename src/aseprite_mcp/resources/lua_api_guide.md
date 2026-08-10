# Aseprite Lua API cheat sheet

Condensed reference for `run_lua`, covering what this server has verified working (and a few things
verified *not* working) against the Aseprite build it was developed against. The full API is much
larger — see `aseprite.org/docs/scripting/` for the complete reference. This is the subset that
actually matters for pixel art scripting, plus the gotchas that cost real debugging time.

## Getting the sprite

```lua
local spr = J.sprite(path)   -- opens if not already open; errors "sprite_not_found" if missing
```

Every script runs in **batch mode**: nothing is open when your script starts. Always resolve the
sprite through `J.sprite(path)`, never assume `app.sprite` is set.

## Reading and writing pixels

```lua
local cel = layer:cel(frameNumber)
if not cel then cel = spr:newCel(layer, frameNumber) end
local img = cel.image:clone()      -- ALWAYS clone before mutating — in-place edits on a shared
img:drawPixel(x, y, paletteIndex)  -- image bleed into linked cels
cel.image = img
local value = img:getPixel(x, y)   -- returns the raw palette index (indexed mode) or packed RGBA
```

Index 0 is the sprite's `transparentColor` by default — Aseprite composites it as alpha 0 whether
or not it was explicitly painted. There is no way to distinguish "painted index 0" from "never
touched" by reading pixels back.

## Colors

```lua
Color{r=255, g=0, b=0, a=255}   -- WORKS
Color(255, 0, 0)                -- WORKS
Color("#ff0000")                -- DOES NOT WORK — silently returns black, no error
```

Always build colors from parsed integer components. Parse hex strings yourself before calling
`Color`.

## Palette

```lua
local pal = spr.palettes[1]
pal:setColor(0, Color{r=0,g=0,b=0,a=255})
spr:setPalette(pal)              -- full replace
pal:resize(n)                    -- grow/shrink in place
local c = pal:getColor(i)
c.red, c.green, c.blue, c.alpha  -- tostring(c) is USELESS — prints a raw pointer, not hex
```

## Layers

```lua
local l = spr:newLayer()          -- or spr:newGroup() for a group
l.name = "details"
l.isVisible = false
l.opacity = 128                    -- 0-255
l.blendMode = BlendMode.MULTIPLY
l.isEditable = false               -- "locked" in the UI — isLocked is READ-ONLY, this is the setter
l.stackIndex = 1                   -- reorder; 1 = bottom
spr:deleteLayer(l)
```

Duplicate and merge-down have no direct Lua method — go through `app.command`:

```lua
app.activeSprite = spr
app.activeLayer = l
app.command.DuplicateLayer()
app.command.MergeDownLayer()   -- silently no-ops if there's nothing below l to merge into —
                                -- check the layer count changed, don't trust a clean return
```

## Frames

```lua
local f = spr:newEmptyFrame()      -- blank frame appended
local f2 = spr:newFrame(n)         -- duplicate of frame n
spr:deleteFrame(n)
f.duration = 0.2                   -- seconds
```

`frame.frameNumber` is **read-only** — there is no scripted way to reorder frames on this build.

## Tags

```lua
local t = spr:newTag(fromFrame, toFrame)
t.name = "walk"
t.aniDir = AniDir.FORWARD  -- or REVERSE, PING_PONG
t.fromFrame.frameNumber, t.toFrame.frameNumber
spr:deleteTag(t)
```

## Shapes and fill

```lua
app.useTool{
  tool = "line",  -- "rectangle" / "filled_rectangle" / "ellipse" / "filled_ellipse" / "paint_bucket"
  color = Color{r=..., g=..., b=..., a=...},
  points = { Point(x1,y1), Point(x2,y2) },  -- more points = polyline for "line"
  layer = l, frame = n,
}
```

There's no `filled=true` flag — filled shapes are separate tool names (`filled_rectangle`, not
`rectangle` with a flag).

**`app.useTool` can silently shrink a cel** to the drawn stroke's bounding box if the cel had no
prior non-transparent content, instead of keeping it full-canvas. Restore the invariant after any
`useTool` call:

```lua
if not (cel.position.x == 0 and cel.position.y == 0
        and cel.image.width == spr.width and cel.image.height == spr.height) then
  local canvas = Image(spr.width, spr.height, spr.colorMode)
  canvas:drawImage(cel.image, cel.position)
  cel.image = canvas
  cel.position = Point(0, 0)
end
```

## Saving

Batch mode has no persistent state between commands. Every mutating script must end with:

```lua
spr:saveAs(spr.filename)
```

## Returning data

Return a plain Lua value (usually a table) — it's encoded as JSON automatically. Strings containing
newlines (a caught error's stack traceback, for instance) are safe to return; don't build JSON by
hand.
