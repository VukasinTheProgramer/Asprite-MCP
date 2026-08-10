"""File-snapshot undo/redo. Aseprite's own app.undo() has nothing to undo in
batch mode — every command is a fresh process opening the sprite from disk,
so there's no persistent in-memory undo stack (verified, M9 spike,
2026-08-10). This gets the same user-facing safety a different way: copy the
sprite's on-disk state before each mutation, restore from the copy on undo.
"""

import shutil
import uuid
from pathlib import Path

from .state import SessionState


def _dir_for(session: SessionState, path: str) -> Path:
    d = session.config.runtime / "history" / Path(path).stem
    d.mkdir(parents=True, exist_ok=True)
    return d


def push_snapshot(session: SessionState, path: str) -> None:
    """Call before every mutating tool's first bridge.execute(). A no-op if
    the sprite doesn't exist yet (nothing to snapshot before the first
    save). Clears the redo branch — a new edit invalidates old redos, same
    as any standard undo/redo model."""
    src = Path(path)
    if not src.exists():
        return
    snap = _dir_for(session, path) / f"{uuid.uuid4().hex}.aseprite"
    shutil.copy2(src, snap)
    session.undo_stack.setdefault(path, []).append(snap)
    for old in session.redo_stack.pop(path, []):
        old.unlink(missing_ok=True)


def _shift(session: SessionState, path: str, steps: int, frm: str, to: str) -> int:
    from_stack: list[Path] = getattr(session, frm).setdefault(path, [])
    to_stack: list[Path] = getattr(session, to).setdefault(path, [])
    applied = 0
    for _ in range(steps):
        if not from_stack:
            break
        carry = _dir_for(session, path) / f"{uuid.uuid4().hex}.aseprite"
        shutil.copy2(path, carry)
        to_stack.append(carry)
        snap = from_stack.pop()
        shutil.copy2(snap, path)
        snap.unlink(missing_ok=True)
        applied += 1
    return applied


def undo(session: SessionState, path: str, steps: int) -> int:
    """Returns steps actually applied — may be fewer than requested if
    history runs out; that's not an error, just nothing further to undo."""
    return _shift(session, path, steps, "undo_stack", "redo_stack")


def redo(session: SessionState, path: str, steps: int) -> int:
    return _shift(session, path, steps, "redo_stack", "undo_stack")
