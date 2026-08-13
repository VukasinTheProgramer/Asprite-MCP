"""File-snapshot undo/redo. Aseprite's own app.undo() has nothing to undo in
batch mode — every command is a fresh process opening the sprite from disk,
so there's no persistent in-memory undo stack (verified, M9 spike,
2026-08-10). This gets the same user-facing safety a different way: copy the
sprite's on-disk state before each mutation, restore from the copy on undo.
"""

import os
import shutil
import sys
import uuid
from pathlib import Path

from .config import Config
from .state import SessionState

# How many undo steps to keep per sprite. Past this the oldest is dropped, the
# same way any editor bounds its history. Unbounded, this wrote 2,763 files and
# 11 MB during development alone — on a 512x512 sprite (~100 KB) a working
# session of 300 draw calls is ~30 MB, kept forever.
MAX_SNAPSHOTS = 50


def _run_dir(config: Config) -> Path:
    """History for THIS server process.

    Keyed by pid so `sweep_stale_history` can tell a dead run's leftovers from a
    concurrent server's live snapshots. Without the split, two clients sharing a
    machine would delete each other's undo history on startup.
    """
    return config.runtime / "history" / str(os.getpid())


def _dir_for(session: SessionState, path: str) -> Path:
    d = _run_dir(session.config) / Path(path).stem
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
    with session.lock:
        stack = session.undo_stack.setdefault(path, [])
        stack.append(snap)
        while len(stack) > MAX_SNAPSHOTS:
            stack.pop(0).unlink(missing_ok=True)
        for old in session.redo_stack.pop(path, []):
            old.unlink(missing_ok=True)


def _alive(pid: int) -> bool:
    """Does a process with this pid exist?

    Errs toward True on anything ambiguous: a false "alive" keeps stale files a
    while longer, a false "dead" deletes a running server's undo history.
    """
    if sys.platform == "win32":
        return _alive_windows(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else
    except OSError:
        return True  # unknown -- err toward keeping the files
    return True


def _alive_windows(pid: int) -> bool:
    """os.kill is NOT a liveness probe on Windows.

    CPython maps os.kill(pid, sig) to TerminateProcess(handle, sig) for any
    signal that is not a console control event — so `os.kill(pid, 0)`, the POSIX
    idiom for "does this exist", would terminate a live process with exit code 0.
    For a pid that does not exist it raises a plain OSError rather than
    ProcessLookupError, which the POSIX branch reads as "alive" and never sweeps.
    That is how CI caught this: the dead-pid case returned 0 removals.

    OpenProcess with a query-only right is the actual probe.
    """
    import ctypes
    from ctypes import wintypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    ERROR_INVALID_PARAMETER = 87
    STILL_ACTIVE = 259

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        # Only "no such pid" proves death. Access-denied means it exists and
        # belongs to someone else, which is still alive.
        return ctypes.get_last_error() != ERROR_INVALID_PARAMETER  # type: ignore[attr-defined]

    # A handle that opens is NOT proof of life. Windows keeps an exited
    # process's pid resolvable for as long as any handle to it remains open --
    # including the one a parent holds after the child died. CI hit exactly
    # that: the "definitely dead" subprocess still opened, so nothing swept.
    # GetExitCodeProcess is the actual liveness question.
    try:
        code = wintypes.DWORD()
        ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
    finally:
        kernel32.CloseHandle(handle)
    if not ok:
        return True  # could not tell -- err toward keeping the files
    # STILL_ACTIVE is ambiguous by design: a process that genuinely exits with
    # 259 reads as running. That errs toward "alive", the safe direction here.
    return code.value == STILL_ACTIVE


def sweep_stale_history(config: Config) -> int:
    """Delete history left by server runs that are no longer alive.

    Undo stacks live in memory, so once a run exits its snapshot files are
    unreachable by definition — not merely old, but provably dead. Nothing
    reclaimed them before this, which is the whole of why the directory grew
    without bound. Returns the number of run directories removed.
    """
    root = config.runtime / "history"
    if not root.exists():
        return 0
    removed = 0
    for d in root.iterdir():
        if not d.is_dir():
            continue
        if not d.name.isdigit():
            # Pre-cap layout: history/<sprite-stem>/ with no run directory. All
            # live history now lives under a pid, so anything else is left over
            # from before this fix and is unreachable. Without this the 2,763
            # files that motivated the change would never be reclaimed.
            shutil.rmtree(d, ignore_errors=True)
            removed += 1
            continue
        if int(d.name) == os.getpid() or _alive(int(d.name)):
            continue
        shutil.rmtree(d, ignore_errors=True)
        removed += 1
    return removed


def _shift(session: SessionState, path: str, steps: int, frm: str, to: str) -> int:
    with session.lock:
        return _shift_locked(session, path, steps, frm, to)


def _shift_locked(session: SessionState, path: str, steps: int, frm: str, to: str) -> int:
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
