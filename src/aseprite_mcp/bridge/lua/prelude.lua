-- prelude.lua (concatenated ahead of each command chunk)
local J = {}

-- Lua's %q produces re-loadable LUA source: a literal newline is escaped as
-- a backslash followed by an actual newline character, not the two-character
-- \n JSON expects. json.loads() on the Python side then chokes on the raw
-- control character. Confirmed empirically (M5 spike, 2026-08-10): a pcall'd
-- error() whose message contains a Lua stack traceback (always multi-line)
-- broke every result that tried to return it as encoded data. Escape for
-- JSON explicitly instead of delegating to %q.
local ESCAPES = { ['\\'] = '\\\\', ['"'] = '\\"', ['\n'] = '\\n', ['\r'] = '\\r', ['\t'] = '\\t' }
function J.encode_string(s)
  local out = s:gsub('[%c\\"]', function(c)
    return ESCAPES[c] or string.format('\\u%04x', c:byte())
  end)
  return '"' .. out .. '"'
end

function J.encode(v)
  local t = type(v)
  if v == nil then return "null"
  elseif t == "boolean" then return tostring(v)
  elseif t == "number" then return tostring(v)
  elseif t == "string" then return J.encode_string(v)
  elseif t == "table" then
    if #v > 0 or next(v) == nil then
      local parts = {}
      for _, item in ipairs(v) do parts[#parts+1] = J.encode(item) end
      return "[" .. table.concat(parts, ",") .. "]"
    else
      local parts = {}
      for k, val in pairs(v) do
        parts[#parts+1] = J.encode_string(tostring(k)) .. ":" .. J.encode(val)
      end
      return "{" .. table.concat(parts, ",") .. "}"
    end
  end
  return "null"
end

-- Resolve the sprite at `path`. Checks already-open sprites first (matters
-- once a resident backend exists), falls back to app.open (batch mode: the
-- process starts with nothing open — every command opens its own target).
function J.sprite(path)
  for _, s in ipairs(app.sprites) do
    if s.filename == path then return s end
  end
  local spr = app.open(path)
  if not spr then error("sprite_not_found: " .. tostring(path)) end
  return spr
end

-- Wrap mutations so a failure rolls back cleanly and yields one undo step.
function J.tx(fn)
  local result
  app.transaction(function() result = fn() end)
  return result
end

-- Batch mode has no persistent state between commands — every mutating
-- command must save before the process exits, or the edit is lost.
function J.save(spr)
  spr:saveAs(spr.filename)
end

-- app.useTool (and possibly other tool-driven ops) auto-shrinks a cel's image
-- to its content's bounding box when the cel had no prior non-transparent
-- content — confirmed empirically (M2 spike, 2026-08-10): a fresh cel drawn
-- on via useTool comes back at the stroke's bbox size, not the canvas size.
-- Our tools assume every cel is always full-canvas at position (0,0) — same
-- assumption draw_grid relies on — so call this after any op that isn't a
-- plain drawPixel to restore that invariant. No-op if already normalized.
function J.normalize_cel(spr, cel)
  if cel.position.x == 0 and cel.position.y == 0
     and cel.image.width == spr.width and cel.image.height == spr.height then
    return
  end
  local canvas = Image(spr.width, spr.height, spr.colorMode)
  canvas:drawImage(cel.image, cel.position)
  cel.image = canvas
  cel.position = Point(0, 0)
end

return J
