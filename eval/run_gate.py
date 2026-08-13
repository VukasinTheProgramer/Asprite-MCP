"""B6 gate harness — score the conform pipeline against reference images.

Reads a manifest (see manifest.example.json), runs each entry through the real
MCP tool path, and writes 1x / 8x / side-by-side PNGs plus raw.json.

    python eval/run_gate.py eval/manifest.json

Reference images are NOT committed: the ones this project was scored against are
third-party (a stock portrait, a watermarked sprite, a character under copyright)
and have no place in an MIT repo. Point the manifest at your own. `refs/` holds
the art this project generated itself, which is redistributable and doubles as
the clean pixel-art control.
"""

import asyncio
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))


async def run(manifest_path: Path) -> None:
    manifest = json.loads(manifest_path.read_text())
    cases = manifest["cases"]
    out_dir = (manifest_path.parent / manifest.get("out_dir", "results")).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    exe = os.environ.get("ASEPRITE_PATH")
    if not exe:
        raise SystemExit("set ASEPRITE_PATH to your Aseprite binary")

    ws = Path(tempfile.mkdtemp(prefix="b6-"))
    os.environ["ASEPRITE_MCP_WORKSPACE"] = str(ws)
    os.environ["ASEPRITE_MCP_STYLES"] = str(ws / "styles")

    from PIL import Image

    from mcp.client import Client

    from aseprite_mcp.bridge.batch import BatchBridge
    from aseprite_mcp.server import mcp
    from aseprite_mcp.validation import lua_str

    for c in cases:
        src = (manifest_path.parent / c["image"]).resolve()
        if not src.exists():
            raise SystemExit(f"missing reference: {src}")
        shutil.copy(src, ws / f"{c['id']}_src.png")

    bridge = BatchBridge(Path(exe))
    report = []

    async with Client(mcp) as c_:
        for case in cases:
            cid, target = case["id"], case["target"]
            entry: dict = {**case}

            g = await c_.call_tool("detect_grid", {"image_path": f"{cid}_src.png"})
            entry["detect_grid"] = g.structured_content

            mk = await c_.call_tool(
                "create_sprite",
                {"name": cid, "width": target, "height": target, "preview": False},
            )
            assert not mk.is_error, mk.content[0].text

            res = await c_.call_tool("conform_image", {
                "image_path": f"{cid}_src.png",
                "target_size": [target, target],
                "palette": case.get("palette", "auto"),
                "palette_size": case.get("palette_size", 32),
                "auto_cleanup": True,
                "aggressiveness": case.get("aggressiveness", 0.5),
                "remove_background": case.get("remove_background", False),
                "preview": False,
            })
            entry["error"] = res.is_error
            entry["summary"] = "\n".join(
                b.text for b in res.content if getattr(b, "type", None) == "text"
            )
            if res.is_error:
                print(f"[{cid}] FAILED\n{entry['summary']}")
                report.append(entry)
                continue

            png = out_dir / f"{cid}_after_1x.png"
            bridge.execute(
                f"local spr = J.sprite({lua_str(str(ws / (cid + '.aseprite')))})\n"
                f"spr:saveCopyAs({lua_str(str(png))})\nreturn {{ok=true}}",
                timeout=30.0,
            )

            after = Image.open(png).convert("RGBA")
            n = after.width
            big = after.resize((n * 8, n * 8), Image.Resampling.NEAREST)
            big.save(out_dir / f"{cid}_after_8x.png")

            orig = Image.open(src).convert("RGBA")
            orig.thumbnail((n * 8, n * 8), Image.Resampling.LANCZOS)
            sheet = Image.new(
                "RGBA",
                (orig.width + big.width + 24, max(orig.height, big.height)),
                (255, 255, 255, 255),
            )
            sheet.alpha_composite(orig, (0, 0))
            sheet.alpha_composite(big, (orig.width + 24, 0))
            sheet.save(out_dir / f"{cid}_compare.png")

            px = [p for p in after.get_flattened_data() if p[3] > 0]
            entry["colors_used"] = len(set(px))
            entry["opaque_px"] = len(px)
            report.append(entry)
            print(f"[{cid}] ok  colors={len(set(px))}  -> {png.name}")

    (out_dir / "raw.json").write_text(json.dumps(report, indent=2))
    print("\nwrote", out_dir)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    asyncio.run(run(Path(sys.argv[1])))
