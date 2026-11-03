import json
import os
import shutil
import subprocess

import pytest

from board_tune.target import BlockTarget
from board_tune.target.block import _lsblk, _match_fstab_spec

from .conftest import CONFIG_TXT

PARTS = [
    {"path": "/dev/sdb1", "fstype": "vfat", "label": "bootfs",
     "uuid": "91FE-7499", "partuuid": "4e639091-01"},
    {"path": "/dev/sdb2", "fstype": "ext4", "label": "rootfs",
     "uuid": "56f80fa2-e005-4cca-86e6-19da1069914d", "partuuid": "4e639091-02"},
]


@pytest.mark.parametrize("device,kind", [
    ("/dev/mmcblk0", "disk"), ("/dev/sdb", "disk"), ("/dev/loop0", "loop"),
])
def test_lsblk_preserves_partition_tree_without_name_column(monkeypatch, device, kind):
    suffix = "p" if device[-1].isdigit() else ""
    parts = [dict(part, path=f"{device}{suffix}{index}", type="part")
             for index, part in enumerate(PARTS, 1)]
    disk = {"path": device, "type": kind}

    def lsblk(argv, **kwargs):
        assert argv[0] == "lsblk" and argv[-1] == device
        # Model lsblk's documented JSON contract: PATH alone yields a flat list.
        tree = "--tree" in argv or "NAME" in argv[argv.index("--output") + 1].split(",")
        devices = [dict(disk, children=parts)] if tree else [disk, *parts]
        return subprocess.CompletedProcess(argv, 0, json.dumps({"blockdevices": devices}), "")

    monkeypatch.setattr(subprocess, "run", lsblk)
    result = _lsblk(device)
    assert result["path"] == device
    assert result["children"] == parts


@pytest.mark.parametrize("spec, path", [
    ("PARTUUID=4e639091-01", "/dev/sdb1"),
    ("UUID=91fe-7499", "/dev/sdb1"),
    ("LABEL=rootfs", "/dev/sdb2"),
    ("/dev/disk/by-partuuid/4e639091-02", "/dev/sdb2"),
    ("PARTUUID=deadbeef-01", None),
    ("/dev/mmcblk0p1", None),
    ("tmpfs", None),
])
def test_match_fstab_spec(spec, path):
    part = _match_fstab_spec(spec, PARTS)
    assert (part and part["path"]) == path


def _run(*argv, **kw):
    return subprocess.run(argv, check=True, capture_output=True, text=True, **kw)


@pytest.mark.skipif(os.geteuid() != 0, reason="attaching loop devices needs root")
def test_disk_image(tmp_path, rootfs):
    boot = rootfs / "boot/firmware"
    boot_files = tmp_path / "boot"
    shutil.move(boot, boot_files)
    boot.mkdir()
    (rootfs / "etc/fstab").write_text(
        "LABEL=bootfs /boot/firmware vfat defaults 0 2\n"
        "LABEL=rootfs / ext4 defaults,noatime 0 1\n")

    image = tmp_path / "disk.img"
    with open(image, "wb") as f:
        f.truncate(64 << 20)
    _run("sfdisk", "-q", str(image), input="label: dos\n,16M,c\n,,83\n")
    dev = _run("losetup", "--find", "--show", "--partscan", str(image)).stdout.strip()
    try:
        _run("mkfs.vfat", "-n", "bootfs", f"{dev}p1")
        _run("mkfs.ext4", "-q", "-L", "rootfs", "-d", str(rootfs), f"{dev}p2")
        mnt = tmp_path / "mnt"
        mnt.mkdir()
        _run("mount", f"{dev}p1", str(mnt))
        for name in os.listdir(boot_files):
            shutil.copy(boot_files / name, mnt / name)
        _run("umount", str(mnt))
    finally:
        _run("losetup", "--detach", dev)

    with BlockTarget(str(image)) as t:
        assert t.read("/boot/firmware/config.txt") == CONFIG_TXT.encode()
        t.write("/etc/hostname", b"board\n", mode=0o644, owner=(0, 0))
        t.write("/boot/firmware/cmdline.txt", b"console=tty1\n")

    with BlockTarget(str(image)) as t:
        assert t.read("/etc/hostname") == b"board\n"
        assert t.read("/boot/firmware/cmdline.txt") == b"console=tty1\n"
