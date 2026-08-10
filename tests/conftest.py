import pytest

from aseprite_mcp.discovery import find_aseprite


@pytest.fixture(scope="session")
def aseprite_exe():
    """Skip integration tests cleanly when no binary is found (§11.6)."""
    try:
        return find_aseprite()
    except RuntimeError as e:
        pytest.skip(f"no Aseprite binary available: {e}")
