"""Targets: the boards hooks operate on."""

from __future__ import annotations

import os
import stat

from board_tune.errors import Error

from .base import Stat, Target
from .block import BlockTarget
from .local import LocalTarget, acquire_sudo
from .ssh import SSHTarget

__all__ = ["BlockTarget", "LocalTarget", "SSHTarget", "Stat", "Target",
           "open_target"]


def _directory_target(path: str) -> LocalTarget:
    path = os.path.abspath(path)
    sudo = os.geteuid() != 0 and os.stat(path).st_uid != os.geteuid()
    if sudo:
        acquire_sudo()
    return LocalTarget({"/": path}, sudo=sudo, description=f"directory {path}")


def open_target(spec: str) -> Target:
    """Open a target from its command line specification.

    A path selects an offline target (a block device, a disk image or
    a root directory); anything else is an SSH destination such as
    pi@192.168.1.50.
    """
    if spec.startswith(("/", ".")) or os.path.exists(spec):
        try:
            mode = os.stat(spec).st_mode
        except OSError as e:
            raise Error(f"{spec}: {e.strerror}") from None
        if stat.S_ISDIR(mode):
            return _directory_target(spec)
        if stat.S_ISBLK(mode) or stat.S_ISREG(mode):
            return BlockTarget(spec)
        raise Error(f"{spec}: not a block device, disk image or directory")
    return SSHTarget(spec)
