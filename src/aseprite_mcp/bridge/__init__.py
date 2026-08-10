from ..config import Config
from .base import AsepriteBridge
from .batch import BatchBridge


def make_bridge(config: Config) -> AsepriteBridge:
    # Resident (Timer+Dialog polling) is parked — broken on the dev build tested
    # in the M0 spike (see aseprite-mcp-build-flow.md §1.2, §15). Batch is the
    # only backend until that's retested against a stable Aseprite release.
    return BatchBridge(config.aseprite_exe)
