"""The style bible: a persistent, machine-readable definition of a project's
visual language. See aseprite-mcp-upgrade-plan.md A1.

Phase B's palette lock is supposed to resolve against this — `conform_image`
and `cleanup` take `palette=None` to mean "the active project's palette", which
only works once a project exists to ask.
"""

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from .color import rgb_to_oklab
from .errors import ToolError
from .validation import hex_to_rgba

# Hue families for auto-ramp clustering. Pixel-art palettes are small and
# hue-clustered by construction, so fixed sectors beat k-means here: they give
# stable, nameable ramps ("red", "blue") instead of arbitrary cluster ids that
# renumber every time the palette changes by one entry.
_HUE_SECTORS: tuple[tuple[str, float, float], ...] = (
    ("red", 345.0, 15.0),
    ("orange", 15.0, 45.0),
    ("yellow", 45.0, 75.0),
    ("green", 75.0, 165.0),
    ("cyan", 165.0, 195.0),
    ("blue", 195.0, 255.0),
    ("purple", 255.0, 285.0),
    ("magenta", 285.0, 345.0),
)

# Below this OKLab chroma a color reads as neutral regardless of its hue angle,
# which is noise at that point — a near-black with a nominal hue of 200 belongs
# in the greys ramp, not the blues.
_NEUTRAL_CHROMA = 0.04

# Consecutive hues further apart than this (degrees) start a new family. Wide
# enough that a shading ramp's own hue-shift (typically 10-30 deg end to end)
# stays together, narrow enough to separate genuinely different materials.
_HUE_GAP = 40.0


def _sector_name(hue_deg: float) -> str:
    for name, lo, hi in _HUE_SECTORS:
        if (lo <= hue_deg < hi) or (lo > hi and (hue_deg >= lo or hue_deg < hi)):
            return name
    return "neutral"


def normalize_hex(colors: list[str]) -> list[str]:
    """Canonical lowercase hex. Bundled presets ship uppercase, Aseprite reads
    colors back lowercase, and `set_palette`'s off-style check compares the two
    as strings — without this it fires on every correctly-on-palette sprite,
    which trains the model to ignore the warning entirely."""
    return [c.lower() for c in colors]


def _lab_of(hex_colors: list[str]) -> np.ndarray:
    rgb = np.array([hex_to_rgba(h)[:3] for h in hex_colors], dtype=float) / 255.0
    return rgb_to_oklab(rgb)


def auto_ramps(palette_hex: list[str], skip_index_0: bool = True) -> dict[str, list[int]]:
    """Cluster a palette into hue families, each ordered dark -> light.

    Index 0 is transparency in every indexed sprite this server writes, so it is
    excluded by default — a ramp containing it would paint holes.
    """
    if not palette_hex:
        return {}
    lab = _lab_of(palette_hex)
    chroma = np.hypot(lab[:, 1], lab[:, 2])
    hue = np.degrees(np.arctan2(lab[:, 2], lab[:, 1])) % 360.0

    candidates = [
        i
        for i in range(len(palette_hex))
        if not (skip_index_0 and i == 0) and chroma[i] >= _NEUTRAL_CHROMA
    ]
    families: dict[str, list[int]] = {}
    neutral = [
        i
        for i in range(len(palette_hex))
        if not (skip_index_0 and i == 0) and chroma[i] < _NEUTRAL_CHROMA
    ]
    if neutral:
        families["neutral"] = neutral

    # Cluster by hue *proximity*, not by fixed sector membership. A ramp whose
    # colors straddle a sector edge (reds at 13 deg and 15.1 deg) would split in
    # two under fixed bucketing and then be dropped as two one-color families —
    # the boundary is an artifact of the naming scheme, not of the palette.
    if candidates:
        order = sorted(candidates, key=lambda i: float(hue[i]))
        gaps = [
            (float(hue[order[(k + 1) % len(order)]] - hue[order[k]]) % 360.0, k)
            for k in range(len(order))
        ]
        cluster: list[int] = []
        # walk starting after the widest gap so no real family is split at the seam
        start = (max(gaps)[1] + 1) % len(order)
        rotated = order[start:] + order[:start]
        for pos, i in enumerate(rotated):
            cluster.append(i)
            nxt = rotated[(pos + 1) % len(rotated)]
            gap = float(hue[nxt] - hue[i]) % 360.0
            if pos == len(rotated) - 1 or gap > _HUE_GAP:
                mean_hue = float(np.degrees(np.arctan2(
                    np.sin(np.radians(hue[cluster])).mean(),
                    np.cos(np.radians(hue[cluster])).mean(),
                )) % 360.0)
                name = _sector_name(mean_hue)
                # two distinct families can land in one sector; keep both
                while name in families:
                    name += "_2"
                families[name] = cluster
                cluster = []

    # dark -> light within each family, which is the order shading code wants
    return {
        name: sorted(idxs, key=lambda i: float(lab[i, 0]))
        for name, idxs in sorted(families.items())
        if len(idxs) >= 2  # a lone color is not a ramp
    }


def outline_candidates(palette_hex: list[str], n: int = 2) -> list[int]:
    """The darkest non-transparent entries — what `selective_darker` outlining
    draws with. Pure black is a pixel-art smell (it flattens everything it
    touches), so this returns the palette's own darks rather than adding one."""
    if len(palette_hex) < 2:
        return []
    lab = _lab_of(palette_hex)
    order = sorted(range(1, len(palette_hex)), key=lambda i: float(lab[i, 0]))
    return order[:n]


@dataclass
class StyleBible:
    project: str
    palette: list[str]
    roles: dict[int, str] = field(default_factory=dict)
    ramps: dict[str, list[int]] = field(default_factory=dict)
    light_source: str = "upper_left_45"
    outline: str = "selective_darker"
    outline_indices: list[int] = field(default_factory=list)
    shading_steps: int = 3
    dithering: str = "none"
    canvas_defaults: dict[str, list[int]] = field(
        default_factory=lambda: {
            "character": [32, 32],
            "item": [16, 16],
            "tile": [16, 16],
            "portrait": [64, 64],
            "vfx": [32, 32],
        }
    )
    negative: list[str] = field(
        default_factory=lambda: [
            "no pillow shading",
            "no pure black outlines",
            "no gradients — hard color steps only",
            "no orphan single pixels",
        ]
    )
    reference_sprites: list[str] = field(default_factory=list)

    @classmethod
    def create(cls, project: str, palette_hex: list[str], **overrides: Any) -> "StyleBible":
        """Build a bible with ramps and outline indices derived from the palette."""
        palette_hex = normalize_hex(palette_hex)
        bible = cls(
            project=project,
            palette=list(palette_hex),
            ramps=auto_ramps(palette_hex),
            outline_indices=outline_candidates(palette_hex),
        )
        bible.apply(overrides)
        return bible

    def apply(self, fields: dict[str, Any]) -> list[str]:
        """Set known fields from a dict; returns the names actually changed.
        Unknown keys raise rather than being silently dropped — a typo'd field
        that vanishes is a bug the model cannot see."""
        known = {f for f in self.__dataclass_fields__ if f != "project"}
        unknown = sorted(set(fields) - known)
        if unknown:
            raise ToolError(
                code="unknown_style_field",
                message=f"Unknown style field(s): {unknown}.",
                hint=f"Valid fields: {sorted(known)}.",
                context={"valid": sorted(known)},
            )
        if fields.get("palette") is not None:
            fields = {**fields, "palette": normalize_hex(fields["palette"])}
        changed = []
        for k, v in fields.items():
            if v is not None and getattr(self, k) != v:
                setattr(self, k, v)
                changed.append(k)
        if "palette" in changed and "ramps" not in fields:
            # a new palette invalidates index-based ramps; regenerate rather
            # than leave them pointing at entries that moved or no longer exist
            self.ramps = auto_ramps(self.palette)
            self.outline_indices = outline_candidates(self.palette)
            changed += ["ramps", "outline_indices"]
        return changed

    def validate(self) -> None:
        """Every index referenced must exist in the palette. Collect all errors
        before raising — a half-reported bible sends the model round twice."""
        n = len(self.palette)
        problems: list[str] = []
        for c in self.palette:
            hex_to_rgba(c)  # raises invalid_hex_color naming the offender
        for name, idxs in self.ramps.items():
            bad = [i for i in idxs if not 0 <= i < n]
            if bad:
                problems.append(f"ramp {name!r} references {bad}")
        bad_roles = [i for i in self.roles if not 0 <= i < n]
        if bad_roles:
            problems.append(f"roles reference {sorted(bad_roles)}")
        bad_outline = [i for i in self.outline_indices if not 0 <= i < n]
        if bad_outline:
            problems.append(f"outline_indices references {bad_outline}")
        if problems:
            raise ToolError(
                code="style_index_out_of_range",
                message=f"Palette has {n} colors (0-{n - 1}) but " + "; ".join(problems) + ".",
                hint="Fix the indices, or set a larger palette first.",
                context={"palette_size": n},
            )

    # --- persistence ---------------------------------------------------------

    @staticmethod
    def path_for(root: Path, project: str) -> Path:
        return root / project / "style.json"

    def save(self, root: Path) -> Path:
        self.validate()
        p = self.path_for(root, self.project)
        p.parent.mkdir(parents=True, exist_ok=True)
        import json

        p.write_text(json.dumps(asdict(self), indent=2))
        return p

    @classmethod
    def load(cls, root: Path, project: str) -> "StyleBible":
        import json

        p = cls.path_for(root, project)
        if not p.exists():
            raise ToolError(
                code="style_project_not_found",
                message=f"No style bible for project {project!r}.",
                hint=(
                    f"Available projects: {available_projects(root) or '(none)'}. "
                    "Create one with style(action='create', project=..., palette=...)."
                ),
                context={"available": available_projects(root)},
            )
        raw = json.loads(p.read_text())
        # JSON object keys are always strings; roles is keyed by palette index.
        # Without this the bible round-trips to {"8": "skin"} and every lookup
        # by int misses. The plan's A1 sketch (cls(**json.loads(...))) has this
        # bug — it never round-trips roles correctly.
        raw["roles"] = {int(k): v for k, v in raw.get("roles", {}).items()}
        return cls(**raw)


def available_projects(root: Path) -> list[str]:
    if not root.exists():
        return []
    return sorted(d.name for d in root.iterdir() if (d / "style.json").exists())
