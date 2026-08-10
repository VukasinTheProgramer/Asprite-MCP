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
