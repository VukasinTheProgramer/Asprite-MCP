import pytest


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("ASEPRITE_MCP_WORKSPACE", str(tmp_path))
    return tmp_path


def test_doctor_succeeds_with_a_real_binary(workspace, aseprite_exe, monkeypatch):
    monkeypatch.setenv("ASEPRITE_PATH", str(aseprite_exe))
    from aseprite_mcp import doctor

    assert doctor.run() == 0
    assert (workspace / "_doctor_preview.png").exists()
    assert not (workspace / "_doctor_test.aseprite").exists()  # cleaned up after


def test_doctor_reports_missing_binary_cleanly(workspace, monkeypatch):
    monkeypatch.setattr(
        "aseprite_mcp.doctor.find_aseprite",
        lambda: (_ for _ in ()).throw(RuntimeError("not found, tried: nowhere")),
    )
    from aseprite_mcp import doctor

    assert doctor.run() == 1
