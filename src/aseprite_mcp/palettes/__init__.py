import json
from pathlib import Path

from ..errors import ToolError

_DIR = Path(__file__).parent


def available_presets() -> list[str]:
    return sorted(p.stem for p in _DIR.glob("*.json"))


def load_preset(name: str) -> dict:
    path = _DIR / f"{name}.json"
    if not path.exists():
        raise ToolError(
            code="unknown_palette_preset",
            message=f"No bundled preset named '{name}'.",
            hint="Pass one of the bundled preset names, or an explicit list of hex colors.",
            context={"available": available_presets()},
        )
    return json.loads(path.read_text())
