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
