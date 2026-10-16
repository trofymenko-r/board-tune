"""Offline targets: a root filesystem mounted on this host."""

from __future__ import annotations

import errno
import os
import subprocess

from board_tune.errors import Error, Unsupported

from .shell import _READLINK, ShellTarget

_MAX_SYMLINKS = 40


def acquire_sudo() -> None:
    """Ask for the sudo password once, before any real work starts."""
    if subprocess.run(["sudo", "-v"]).returncode != 0:
        raise Error("sudo authentication failed")


class LocalTarget(ShellTarget):
    """Root filesystem of a board, mounted somewhere on this host.

    mounts maps target paths to host directories, e.g. {"/": rootfs,
    "/boot/firmware": bootfs}. Symlinks are resolved inside the target,
    so an absolute link such as /etc/resolv.conf -> /run/... can never
    lead to a file of the host.
    """

    live = False

    def __init__(self, mounts: dict[str, str], sudo: bool = False,
                 description: str | None = None):
        if "/" not in mounts:
            raise ValueError("the root of the target must be mounted")
        self._mounts = sorted(
            ((prefix.rstrip("/") or "/", os.path.abspath(host))
             for prefix, host in mounts.items()),
            key=lambda m: len(m[0]), reverse=True)
        self._sudo = sudo
        self.chown = sudo or os.geteuid() == 0
        self.description = description or f"directory {mounts['/']}"

    def _sh(self, script, *args, input=b""):
        argv = ["sh", "-c", script, "sh", *args]
        if self._sudo:
            argv = ["sudo", "--"] + argv
        return subprocess.run(argv, input=input, capture_output=True)

    def _map(self, path: str) -> str:
        for prefix, host in self._mounts:
            if prefix == "/":
                return host + path
            if path == prefix or path.startswith(prefix + "/"):
                return host + path[len(prefix):]
        raise AssertionError("unreachable")

    def _host_readlink(self, path: str) -> str | None:
        host = self._map(path)
        try:
            return os.readlink(host)
        except OSError as e:
            if e.errno in (errno.ENOENT, errno.ENOTDIR, errno.EINVAL):
                return None
            if e.errno != errno.EACCES:
                raise
        # A directory such as /root is not searchable without privileges.
        proc = self._sh(_READLINK, host)
        if proc.returncode == 3:
            return None
        return self._check(proc, f"cannot read link {path}").stdout.decode().rstrip("\n")

    def _resolve(self, path: str, follow: bool = True) -> str:
        """Resolve symlinks in path as if the target root were /."""
        parts = [p for p in path.split("/") if p]
        resolved: list[str] = []
        hops = 0
        while parts:
            name = parts.pop(0)
            if name == ".":
                continue
            if name == "..":
                if resolved:
                    resolved.pop()
                continue
            if not parts and not follow:
                resolved.append(name)
                break
            link = self._host_readlink("/" + "/".join(resolved + [name]))
            if link is None:
                resolved.append(name)
                continue
            hops += 1
            if hops > _MAX_SYMLINKS:
                raise Error(f"{self.description}: {path}: too many levels of symbolic links")
            if link.startswith("/"):
                resolved = []
            parts = [p for p in link.split("/") if p] + parts
        return "/" + "/".join(resolved)

    def _path(self, path, follow=True):
        if not path.startswith("/"):
            raise ValueError(f"target paths must be absolute: {path}")
        return self._map(self._resolve(path, follow))

    def realpath(self, path):
        return self._resolve(path)

    def run(self, argv, input=None):
        raise Unsupported(f"{self.description}: cannot run commands on an offline target")
