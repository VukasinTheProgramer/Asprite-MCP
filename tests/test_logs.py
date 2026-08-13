"""Logging is file-only by necessity: stdout is the MCP transport."""

import json
import logging
import subprocess
import sys
from pathlib import Path

from aseprite_mcp.logs import LOGGER_NAME, get_logger, setup_logging


def _read(logs_dir: Path) -> list[dict]:
    path = logs_dir / "aseprite-mcp.log"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_records_are_one_json_object_per_line(tmp_path):
    setup_logging(tmp_path)
    get_logger("t").info("hello", extra={"context": {"n": 1}})

    lines = _read(tmp_path)
    assert len(lines) == 1
    assert lines[0]["msg"] == "hello"
    assert lines[0]["level"] == "INFO"
    assert lines[0]["context"] == {"n": 1}
    assert lines[0]["logger"] == f"{LOGGER_NAME}.t"


def test_exceptions_carry_their_traceback(tmp_path):
    setup_logging(tmp_path)
    try:
        raise ValueError("boom")
    except ValueError:
        get_logger("t").exception("failed")

    rec = _read(tmp_path)[0]
    assert "ValueError: boom" in rec["exc"]


def test_setup_is_idempotent(tmp_path):
    """Called once per server run and once per test; duplicate handlers would
    write every record N times and rotate at N times the rate."""
    for _ in range(5):
        setup_logging(tmp_path)
    get_logger("t").info("once")
    assert len(_read(tmp_path)) == 1


def test_nothing_is_written_before_setup(tmp_path, monkeypatch):
    """Importing a module must not create files as a side effect."""
    logging.getLogger(LOGGER_NAME).handlers.clear()
    get_logger("t").info("dropped")
    assert not (tmp_path / "aseprite-mcp.log").exists()


def test_logging_never_reaches_stdout(tmp_path):
    """The load-bearing property. stdout carries JSON-RPC framing — one stray
    handler and the client drops the connection. Run in a subprocess because
    pytest's capture would mask exactly the failure being checked, and call
    basicConfig() first to simulate a host that has already claimed root.
    """
    script = f"""
import logging, sys
logging.basicConfig(level=logging.DEBUG)          # attaches a stderr handler to root
sys.path.insert(0, {str(Path("src").resolve())!r})
from pathlib import Path
from aseprite_mcp.logs import setup_logging, get_logger
setup_logging(Path({str(tmp_path)!r}))
get_logger("t").error("must not appear on stdout")
print("SENTINEL")
"""
    proc = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "SENTINEL", f"log text leaked to stdout: {proc.stdout!r}"
    assert "must not appear" not in proc.stdout
    # and it did land in the file
    assert any("must not appear" in r["msg"] for r in _read(tmp_path))


def test_propagate_is_off_so_a_host_logger_cannot_capture_us(tmp_path):
    setup_logging(tmp_path)
    assert logging.getLogger(LOGGER_NAME).propagate is False


def test_the_log_rotates(tmp_path):
    """The hand-rolled run_lua.log this replaced appended forever."""
    from aseprite_mcp.logs import _MAX_BYTES

    setup_logging(tmp_path)
    log = get_logger("t")
    blob = "x" * 4096
    for _ in range(_MAX_BYTES // 4096 + 40):
        log.info(blob)

    assert (tmp_path / "aseprite-mcp.log.1").exists(), "never rotated"
    assert (tmp_path / "aseprite-mcp.log").stat().st_size < _MAX_BYTES * 2
