import os
import platform
import shutil
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
