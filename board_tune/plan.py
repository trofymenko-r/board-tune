"""Staged changes to a target."""

from __future__ import annotations

import difflib
from dataclasses import dataclass

from board_tune.target import Target


@dataclass
class _File:
    data: bytes
    mode: int | None
    owner: tuple[int, int] | None


@dataclass
class _Dir:
    mode: int
    owner: tuple[int, int] | None


@dataclass
class _Link:
    dest: str


class _Absent:
    pass


_ABSENT = _Absent()


@dataclass
class Change:
    """One effective change: what apply() will do to a path."""

    action: str  # create, modify, attrs, mkdir, symlink or remove
    path: str
    detail: str = ""


def _diff(path: str, old: bytes, new: bytes) -> str:
    try:
        a = old.decode().splitlines(keepends=True)
        b = new.decode().splitlines(keepends=True)
    except UnicodeDecodeError:
        return "binary content differs\n"
    lines = difflib.unified_diff(a, b, f"a{path}", f"b{path}", n=2)
    return "".join(line if line.endswith("\n") else line + "\n" for line in lines)


class Plan:
    """Staged view of the target filesystem.

    Hooks read and modify files through the plan, and nothing touches
    the target until apply(). Later hooks see the edits of earlier
    ones, so several hooks can safely share files such as the kernel
    command line. Staging the current state of a file is not a change,
    which makes hooks idempotent without extra effort.
    """

    def __init__(self, target: Target):
        self.target = target
        self.notes: list[str] = []
        self._staged: dict[str, object] = {}
        self._originals: dict[str, bytes | None] = {}
        self._commands: list[list[str]] = []

    # Reading

    def _original(self, path: str) -> bytes | None:
        if path not in self._originals:
            self._originals[path] = self.target.read(path)
        return self._originals[path]

    def read(self, path: str) -> bytes | None:
        staged = self._staged.get(path)
        if staged is None:
            return self._original(path)
        return staged.data if isinstance(staged, _File) else None

    def read_text(self, path: str) -> str | None:
        data = self.read(path)
        return None if data is None else data.decode()

    def exists(self, path: str) -> bool:
        staged = self._staged.get(path)
        if staged is None:
            return self.target.exists(path)
        return staged is not _ABSENT

    def readlink(self, path: str) -> str | None:
        staged = self._staged.get(path)
        if staged is None:
            return self.target.readlink(path)
        return staged.dest if isinstance(staged, _Link) else None

    # Staging

    def write(self, path: str, data: str | bytes, mode: int | None = None,
              owner: tuple[int, int] | None = None) -> None:
        """Stage file contents; see Target.write() for mode semantics."""
        if isinstance(data, str):
            data = data.encode()
        self._staged[path] = _File(data, mode, owner)

    def mkdir(self, path: str, mode: int = 0o755,
              owner: tuple[int, int] | None = (0, 0)) -> None:
        self._staged[path] = _Dir(mode, owner)

    def symlink(self, path: str, dest: str) -> None:
        self._staged[path] = _Link(dest)

    def remove(self, path: str) -> None:
        self._staged[path] = _ABSENT

    def command(self, argv: list[str]) -> None:
        """Run argv after applying the changes, on live targets only."""
        if self.target.live and argv not in self._commands:
            self._commands.append(argv)

    def note(self, text: str) -> None:
        """Tell the user something once the plan is shown."""
        if text not in self.notes:
            self.notes.append(text)

    @property
    def commands(self) -> list[list[str]]:
        return list(self._commands)

    # Comparing with the target

    def _attrs_differ(self, path: str, mode: int,
                      owner: tuple[int, int] | None) -> bool:
        st = self.target.stat(path)
        if st is None or st.mode != mode:
            return True
        return bool(owner and self.target.chown and (st.uid, st.gid) != owner)

    def _change(self, path: str, staged: object) -> Change | None:
        if isinstance(staged, _File):
            old = self._original(path)
            if old is None:
                return Change("create", path, _diff(path, b"", staged.data))
            if old != staged.data:
                return Change("modify", path, _diff(path, old, staged.data))
            if staged.mode is not None and self._attrs_differ(path, staged.mode, staged.owner):
                return Change("attrs", path, f"mode {staged.mode:04o}")
            return None
        if isinstance(staged, _Dir):
            if self.target.stat(path) is None:
                return Change("mkdir", path)
            if self._attrs_differ(path, staged.mode, staged.owner):
                return Change("attrs", path, f"mode {staged.mode:04o}")
            return None
        if isinstance(staged, _Link):
            if self.target.readlink(path) == staged.dest:
                return None
            return Change("symlink", path, staged.dest)
        if self.target.stat(path) is not None:
            return Change("remove", path)
        return None

    def changed(self, *paths: str) -> bool:
        """Whether any of the staged paths differs from the target."""
        return any(self._change(p, self._staged[p]) is not None
                   for p in paths if p in self._staged)

    def snapshot(self) -> dict[str, object]:
        """Remember the staged state, for changed_since()."""
        return dict(self._staged)

    def changed_since(self, snapshot: dict[str, object]) -> bool:
        """Whether paths staged after snapshot() differ from the target."""
        return any(self._change(p, s) is not None
                   for p, s in self._staged.items() if snapshot.get(p) is not s)

    def changes(self) -> list[Change]:
        changes = (self._change(p, s) for p, s in self._staged.items())
        return [c for c in changes if c is not None]

    def apply(self, changes: list[Change]) -> None:
        for change in changes:
            staged = self._staged[change.path]
            if isinstance(staged, _File):
                self.target.write(change.path, staged.data, staged.mode, staged.owner)
            elif isinstance(staged, _Dir):
                self.target.mkdir(change.path, staged.mode, staged.owner)
            elif isinstance(staged, _Link):
                self.target.symlink(change.path, staged.dest)
            else:
                self.target.remove(change.path)
        for argv in self._commands:
            self.target.run(argv)
