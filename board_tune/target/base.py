"""Interface shared by all targets."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Stat:
    mode: int
    uid: int
    gid: int
    kind: str = "file"


class Target:
    """Root filesystem of a board that hooks read and modify.

    Paths are always absolute paths as seen from inside the target, e.g.
    "/etc/hostname", no matter where the filesystem is mounted on the
    host. Live targets are running systems that can execute commands;
    offline targets are filesystems mounted on the host.
    """

    live = False
    description = ""
    # Whether file ownership is applied and compared. False when the
    # target is a directory written without root privileges.
    chown = True

    def read(self, path: str) -> bytes | None:
        """Return the contents of a regular file, or None if it is missing."""
        raise NotImplementedError

    def listdir(self, path: str) -> list[str]:
        """Return sorted entry names, or an empty list for a missing directory."""
        raise NotImplementedError

    def stat(self, path: str) -> Stat | None:
        """Return the attributes of path without following a final symlink."""
        raise NotImplementedError

    def exists(self, path: str) -> bool:
        raise NotImplementedError

    def readlink(self, path: str) -> str | None:
        """Return the destination of a symlink, or None if path is not one."""
        raise NotImplementedError

    def write(self, path: str, data: bytes, mode: int | None = None,
              owner: tuple[int, int] | None = None) -> None:
        """Write a file, creating missing parent directories.

        With a mode the file is replaced atomically and gets the given
        mode and owner. Without one it is rewritten in place, keeping
        its attributes, which also works on filesystems such as FAT.
        """
        raise NotImplementedError

    def mkdir(self, path: str, mode: int = 0o755,
              owner: tuple[int, int] | None = None) -> None:
        raise NotImplementedError

    def symlink(self, path: str, dest: str) -> None:
        raise NotImplementedError

    def remove(self, path: str) -> None:
        raise NotImplementedError

    def realpath(self, path: str) -> str:
        """Resolve a path inside the target root."""
        raise NotImplementedError

    def set_attrs(self, path: str, mode: int, owner: tuple[int, int]) -> None:
        raise NotImplementedError

    def rmdir(self, path: str) -> None:
        """Remove an empty directory only."""
        raise NotImplementedError

    def run(self, argv: list[str], input: bytes | None = None) -> str:
        """Run a command on a live target and return its output."""
        raise NotImplementedError

    def close(self) -> None:
        pass

    def __enter__(self) -> Target:
        return self

    def __exit__(self, *exc) -> None:
        self.close()
