"""Offline targets on block devices and disk images."""

from __future__ import annotations

import functools
import json
import os
import re
import stat
import subprocess
import sys
import tempfile

from board_tune.errors import Error

from .local import LocalTarget, acquire_sudo

ROOT_FSTYPES = {"ext2", "ext3", "ext4", "btrfs", "xfs", "f2fs"}
BOOT_FSTYPES = {"vfat", "exfat"}
# Partitions of a removable disk mounted by the desktop are reused; any
# other mount point means the disk is in use by this system.
HOST_MOUNT_DIRS = ("/media/", "/run/media/", "/mnt/")

_LSBLK_COLUMNS = "PATH,TYPE,FSTYPE,LABEL,UUID,PARTUUID,RM,HOTPLUG,SIZE,MODEL,MOUNTPOINT"


def _flag(value) -> bool:
    # lsblk reports booleans as true/false or "1"/"0" depending on version.
    return str(value).lower() in ("1", "true")


def _size(nbytes) -> str:
    size = float(nbytes or 0)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TiB"


def _lsblk(dev: str) -> dict:
    # JSON children are emitted only with NAME in the columns or --tree.
    # We select PATH instead, so explicitly request the disk/partition tree.
    proc = subprocess.run(["lsblk", "--json", "--tree", "--bytes", "--output",
                           _LSBLK_COLUMNS, dev], capture_output=True, text=True)
    if proc.returncode != 0:
        raise Error(f"{dev}: {proc.stderr.strip()}")
    return json.loads(proc.stdout)["blockdevices"][0]


def _match_fstab_spec(spec: str, parts: list[dict]) -> dict | None:
    m = re.fullmatch(r"/dev/disk/by-(partuuid|uuid|label)/(.+)", spec)
    if m:
        field, value = m.group(1), m.group(2)
    else:
        key, _, value = spec.partition("=")
        field = {"PARTUUID": "partuuid", "UUID": "uuid", "LABEL": "label"}.get(key)
        if field is None:
            return None
    for part in parts:
        if (part.get(field) or "").lower() == value.lower():
            return part
    return None


class BlockTarget(LocalTarget):
    """Boot media of a board: a removable disk or a disk image.

    The root filesystem is the partition containing /etc/os-release;
    other partitions are mounted where the image's /etc/fstab puts them,
    e.g. the Raspberry Pi boot partition at /boot/firmware.
    """

    def __init__(self, path: str):
        self._cleanup: list = []
        self._priv = [] if os.geteuid() == 0 else ["sudo", "--"]
        if self._priv:
            acquire_sudo()
        try:
            mounts, description = self._setup(path)
        except BaseException:
            self._teardown()
            raise
        super().__init__(mounts, sudo=bool(self._priv), description=description)

    def _cmd(self, argv: list[str]) -> str:
        proc = subprocess.run(self._priv + argv, capture_output=True, text=True)
        if proc.returncode != 0:
            raise Error(f"{' '.join(argv)}: {proc.stderr.strip()}")
        return proc.stdout

    def _setup(self, path: str) -> tuple[dict[str, str], str]:
        if stat.S_ISREG(os.stat(path).st_mode):
            dev = self._cmd(["losetup", "--find", "--show", "--partscan",
                             path]).strip()
            self._cleanup.append(functools.partial(
                self._cmd, ["losetup", "--detach", dev]))
            subprocess.run(["udevadm", "settle"], check=False)
            what = f"image {path}"
        else:
            dev = os.path.realpath(path)
            what = dev

        disk = _lsblk(dev)
        if disk["type"] not in ("disk", "loop"):
            raise Error(f"{path}: is a {disk['type']}, expected a whole disk "
                        "(e.g. /dev/sdb, not /dev/sdb1)")
        model = (disk.get("model") or "").strip()
        removable = (_flag(disk.get("rm")) or _flag(disk.get("hotplug"))
                     or disk["type"] == "loop"
                     or os.path.basename(dev).startswith("mmcblk"))
        if not removable:
            raise Error(f"{dev} ({model or 'unknown model'}) is not a removable "
                        "disk; refusing to modify it")

        parts = disk.get("children") or []
        if not parts:
            raise Error(f"{dev}: no partitions found")
        for part in parts:
            mp = part.get("mountpoint")
            if mp and not mp.startswith(HOST_MOUNT_DIRS):
                raise Error(f"{part['path']} is mounted at {mp}; refusing to "
                            "touch a disk in use by this system")

        self._tmp = tempfile.mkdtemp(prefix="board-tune-mnt-")
        self._cleanup.append(functools.partial(os.rmdir, self._tmp))

        root = None
        for part in parts:
            if part.get("fstype") in ROOT_FSTYPES:
                d = self._mount(part)
                if os.path.lexists(os.path.join(d, "etc/os-release")):
                    root = d
                    break
        if root is None:
            raise Error(f"{path}: no Linux root filesystem found")

        mounts = {"/": root}
        for mountpoint, part in self._fstab(root, parts):
            mounts[mountpoint] = self._mount(part)
        if len(mounts) == 1:
            # No usable fstab entries: guess where the boot partition goes.
            boot = next((p for p in parts if p.get("fstype") in BOOT_FSTYPES), None)
            if boot is not None:
                where = ("/boot/firmware"
                         if os.path.isdir(os.path.join(root, "boot/firmware"))
                         else "/boot")
                mounts[where] = self._mount(boot)

        details = ", ".join(x for x in (model, _size(disk.get("size"))) if x)
        return mounts, f"{what} ({details})"

    def _mount(self, part: dict) -> str:
        if part.get("mountpoint"):
            return part["mountpoint"]
        d = os.path.join(self._tmp, os.path.basename(part["path"]))
        if not os.path.isdir(d):
            os.mkdir(d)
            self._cleanup.append(functools.partial(os.rmdir, d))
            self._cmd(["mount", part["path"], d])
            self._cleanup.append(functools.partial(self._cmd, ["umount", d]))
            part["mountpoint"] = d
        return d

    @staticmethod
    def _fstab(root: str, parts: list[dict]):
        try:
            with open(os.path.join(root, "etc/fstab")) as f:
                lines = f.read().splitlines()
        except OSError:
            return
        for line in lines:
            fields = line.split()
            if len(fields) < 3 or fields[0].startswith("#"):
                continue
            spec, mountpoint = fields[0], fields[1]
            if mountpoint == "/" or not mountpoint.startswith("/"):
                continue
            part = _match_fstab_spec(spec, parts)
            if part is not None:
                yield mountpoint.rstrip("/"), part

    def _teardown(self) -> None:
        while self._cleanup:
            action = self._cleanup.pop()
            try:
                action()
            except (Error, OSError) as e:
                print(f"board-tune: warning: cleanup failed: {e}", file=sys.stderr)

    def close(self):
        self._teardown()
