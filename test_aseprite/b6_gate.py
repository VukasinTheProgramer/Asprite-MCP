"""B6 gate runner — drives the real MCP tool path, not the internals.

Per plan B6: conform each reference at a plausible native size with
auto_cleanup=True, locked to a 32-colour palette extracted from that same
image. Emits 1x and 4x PNGs beside the original into test_aseprite/.
"""

import asyncio
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path("/Users/vukasinsavkovic/Documents/Aseprite MCP")
CACHE = Path("/Users/vukasinsavkovic/.claude/image-cache/10f3cfbb-d7f2-4de7-ad36-c59c27820c00")
OUT = REPO / "test_aseprite"
EXE = "/Users/vukasinsavkovic/aseprite/build/bin/Aseprite.app/Contents/MacOS/aseprite"

# (id, source, label, target, remove_background, palette_size)
# Sizes follow the user's direction and the plan's tiers: the detailed nature
# shot gets the biggest canvas, the character gets the decision-critical 64,
# the flat circle the smallest.
CASES = [
    ("nature", "2.png", "aerial river landscape (photo, detailed)", 128, False, 32),
    ("pikachu", "3.png", "animated character (flat vector)", 64, True, 32),
    ("turtle", "4.png", "PIXEL-ART CONTROL (already clean)", 32, True, 32),
    ("orb", "5.png", "glowing circle (soft gradient)", 32, False, 32),
    ("person", "6.png", "photo of a person", 64, True, 32),
]

sys.path.insert(0, str(REPO / "src"))


async def main() -> None:
    ws = Path(tempfile.mkdtemp(prefix="b6ws-"))
    os.environ["ASEPRITE_PATH"] = EXE
    os.environ["ASEPRITE_MCP_WORKSPACE"] = str(ws)
    os.environ["ASEPRITE_MCP_STYLES"] = str(ws / "styles")

    from mcp.client import Client

    from aseprite_mcp.bridge.batch import BatchBridge
    from aseprite_mcp.server import mcp
    from aseprite_mcp.validation import lua_str

    for _id, src, _lbl, _t, _rb, _ps in CASES:
        shutil.copy(CACHE / src, ws / f"{_id}_src.png")

    OUT.mkdir(exist_ok=True)
    bridge = BatchBridge(Path(EXE))
    report = []

    async with Client(mcp) as c:
        for cid, src, label, target, rembg, psize in CASES:
            entry: dict = {"id": cid, "label": label, "target": [target, target]}

            g = await c.call_tool("detect_grid", {"image_path": f"{cid}_src.png"})
            entry["detect_grid"] = g.structured_content

            mk = await c.call_tool(
                "create_sprite",
                {"name": cid, "width": target, "height": target, "preview": False},
            )
            assert not mk.is_error, mk.content[0].text

            out = await c.call_tool(
                "conform_image",
                {
                    "image_path": f"{cid}_src.png",
                    "target_size": [target, target],
                    "palette": "auto",
                    "palette_size": psize,
                    "auto_cleanup": True,
                    "aggressiveness": 0.5,
                    "remove_background": rembg,
                    "preview": False,
                },
            )
            entry["error"] = out.is_error
            entry["summary"] = "\n".join(
                b.text for b in out.content if getattr(b, "type", None) == "text"
            )
            if out.is_error:
                report.append(entry)
                print(f"[{cid}] FAILED\n{entry['summary']}")
                continue

            spr = ws / f"{cid}.aseprite"
            png = OUT / f"{cid}_after_1x.png"
            bridge.execute(
                f"local spr = J.sprite({lua_str(str(spr))})\n"
                f"spr:saveCopyAs({lua_str(str(png))})\n"
                "return { ok = true }",
                timeout=30.0,
            )
            report.append(entry)
            print(f"[{cid}] ok -> {png.name}")

    (OUT / "b6_raw.json").write_text(json.dumps(report, indent=2))
    print("\nworkspace:", ws)


asyncio.run(main())
