import json
import subprocess
import time
import uuid
from pathlib import Path

from aseprite_mcp.discovery import find_aseprite

QUEUE = Path("~/.aseprite-mcp/runtime/queue").expanduser()
QUEUE.mkdir(parents=True, exist_ok=True)

proc = subprocess.Popen([
    str(find_aseprite()),
    "--script-param", f"queue={QUEUE}",
    "--script", str(Path(__file__).parent / "listener.lua"),
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


if __name__ == "__main__":
    time.sleep(2)  # let Aseprite boot
    print(send('local s = Sprite(32,32); return "\\"created\\""'))
