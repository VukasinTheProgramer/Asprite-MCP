"""`aseprite-mcp --doctor` — turns most bug reports into self-service (§12.4).
Checks binary discovery, workspace writability, bridge round-trip latency,
and renders a real test sprite, printing exactly what failed if anything did.
"""

import time

from .bridge import make_bridge
from .config import load_config
from .discovery import find_aseprite
from .render import render_preview
from .validation import lua_str


def run() -> int:
    print("aseprite-mcp doctor")
    print("=" * 40)

    try:
        exe = find_aseprite()
    except RuntimeError as e:
        print(f"[FAIL] Aseprite binary: {e}")
        return 1
    print(f"[ OK ] Aseprite binary: {exe}")

    config = load_config()
    print(f"[ OK ] Workspace: {config.workspace}")

    probe = config.workspace / ".doctor_probe"
    try:
        probe.write_text("x")
        probe.unlink()
    except OSError as e:
        print(f"[FAIL] Workspace not writable: {e}")
        return 1
    print("[ OK ] Workspace writable")

    bridge = make_bridge(config)
    print(f"[ OK ] Bridge backend: {type(bridge).__name__}")

    t0 = time.time()
    try:
        bridge.execute("return { ok = true }")
    except Exception as e:
        print(f"[FAIL] Bridge round-trip: {e}")
        return 1
    print(f"[ OK ] Bridge round-trip: {(time.time() - t0) * 1000:.0f}ms")

    test_sprite = config.workspace / "_doctor_test.aseprite"
    try:
        bridge.execute(
            "local spr = Sprite(8, 8, ColorMode.INDEXED)\n"
            f"spr.filename = {lua_str(str(test_sprite))}\n"
            "spr:saveAs(spr.filename)\n"
            "return { ok = true }"
        )
        png, _ = render_preview(bridge, config.previews, str(test_sprite))
        preview_out = config.workspace / "_doctor_preview.png"
        preview_out.write_bytes(png)
        print(f"[ OK ] Rendered test sprite: {preview_out}")
    except Exception as e:
        print(f"[FAIL] Test sprite render: {e}")
        return 1
    finally:
        test_sprite.unlink(missing_ok=True)

    print("=" * 40)
    print("All checks passed.")
    return 0
