from pathlib import Path



# --- bounded growth -----------------------------------------------------------
# push_snapshot copied the whole sprite before every mutating call and nothing
# ever reclaimed it: 2,763 files and 11 MB accumulated during development alone.
# The stack is in memory, so files from an exited run are unreachable, not merely
# old -- which is what makes sweeping them safe.

def _session(tmp_path):
    import os

    from aseprite_mcp.config import Config
    from aseprite_mcp.state import SessionState

    cfg = Config(
        aseprite_exe=Path("/nonexistent"),
        workspace=tmp_path / "ws",
        runtime=tmp_path / "rt",
        previews=tmp_path / "pv",
        logs=tmp_path / "lg",
        styles=tmp_path / "st",
    )
    for d in (cfg.workspace, cfg.runtime, cfg.previews, cfg.logs, cfg.styles):
        d.mkdir(parents=True, exist_ok=True)
    return SessionState(config=cfg)


def test_snapshot_history_is_capped_per_sprite(tmp_path):
    from aseprite_mcp.history import MAX_SNAPSHOTS, push_snapshot

    session = _session(tmp_path)
    sprite = session.config.workspace / "s.aseprite"
    sprite.write_bytes(b"x" * 64)

    for i in range(MAX_SNAPSHOTS + 20):
        sprite.write_bytes(bytes([i % 256]) * 64)
        push_snapshot(session, str(sprite))

    stack = session.undo_stack[str(sprite)]
    assert len(stack) == MAX_SNAPSHOTS, "stack grew past the cap"
    on_disk = list((session.config.runtime / "history").rglob("*.aseprite"))
    assert len(on_disk) == MAX_SNAPSHOTS, f"{len(on_disk)} files for {MAX_SNAPSHOTS} steps"
    assert all(p.exists() for p in stack), "a still-referenced snapshot was deleted"


def test_undo_still_works_after_the_cap_drops_the_oldest(tmp_path):
    """Dropping the oldest must cost the deepest undo, not break the recent ones."""
    from aseprite_mcp.history import MAX_SNAPSHOTS, push_snapshot, undo

    session = _session(tmp_path)
    sprite = session.config.workspace / "s.aseprite"
    for i in range(MAX_SNAPSHOTS + 5):
        sprite.write_bytes(bytes([i]) * 8)
        push_snapshot(session, str(sprite))
    sprite.write_bytes(b"\xff" * 8)

    assert undo(session, str(sprite), 1) == 1
    assert sprite.read_bytes() == bytes([MAX_SNAPSHOTS + 4]) * 8
    # and the history cannot be walked back further than the cap allows
    assert undo(session, str(sprite), MAX_SNAPSHOTS + 10) == MAX_SNAPSHOTS - 1


def test_sweep_removes_a_dead_runs_history_but_not_a_live_one(tmp_path):
    """Uses real processes rather than assumed pids. `1` is init on POSIX but
    means nothing on Windows, and a hardcoded "surely dead" pid is a guess --
    which is how the first version of this passed on POSIX while the liveness
    probe was outright broken on Windows."""
    import os
    import subprocess
    import sys

    from aseprite_mcp.history import sweep_stale_history

    session = _session(tmp_path)
    root = session.config.runtime / "history"

    mine = root / str(os.getpid()) / "s"
    mine.mkdir(parents=True)
    (mine / "a.aseprite").write_bytes(b"keep")

    # a real process, alive and not ours, for the whole assertion
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    # a real process that has definitely exited, so its pid is definitely dead
    finished = subprocess.Popen([sys.executable, "-c", ""])
    finished.wait()

    try:
        live = root / str(other.pid) / "s"
        live.mkdir(parents=True)
        (live / "b.aseprite").write_bytes(b"keep")

        dead = root / str(finished.pid) / "s"
        dead.mkdir(parents=True)
        (dead / "c.aseprite").write_bytes(b"drop")

        removed = sweep_stale_history(session.config)

        assert removed == 1
        assert (mine / "a.aseprite").exists(), "swept our own live history"
        assert (live / "b.aseprite").exists(), "swept a concurrent server's history"
        assert not dead.exists(), "left a dead run's history behind"
    finally:
        other.kill()
        other.wait()


def test_sweep_reclaims_the_pre_cap_layout(tmp_path):
    """History used to live at history/<sprite-stem>/ with no run directory.
    Sweeping only numeric pid dirs would have left every one of the 2,763 files
    that motivated this change sitting on disk forever."""
    from aseprite_mcp.history import sweep_stale_history

    session = _session(tmp_path)
    old = session.config.runtime / "history" / "knight"
    old.mkdir(parents=True)
    (old / "a.aseprite").write_bytes(b"orphan")

    assert sweep_stale_history(session.config) == 1
    assert not old.exists()


def test_sweep_is_a_noop_when_there_is_no_history_yet(tmp_path):
    from aseprite_mcp.history import sweep_stale_history

    assert sweep_stale_history(_session(tmp_path).config) == 0
