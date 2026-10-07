import os

import pytest

from board_tune.errors import Error, Unsupported
from board_tune.target import LocalTarget, open_target


def test_read(target):
    assert target.read("/etc/passwd").startswith(b"root:")
    assert target.read("/etc/missing") is None
    with pytest.raises(Error):
        target.read("/etc")


def test_read_follows_relative_symlinks(target):
    assert target.read("/etc/os-release").startswith(b"PRETTY_NAME")
    assert target.exists("/lib/systemd/system/ssh.service")


def test_write_in_place_keeps_mode(target, rootfs):
    os.chmod(rootfs / "boot/firmware/cmdline.txt", 0o600)
    target.write("/boot/firmware/cmdline.txt", b"quiet\n")
    assert (rootfs / "boot/firmware/cmdline.txt").read_text() == "quiet\n"
    assert target.stat("/boot/firmware/cmdline.txt").mode == 0o600


def test_write_atomic_sets_mode_and_creates_parents(target, rootfs):
    target.write("/etc/new/dir/file", b"data", mode=0o640, owner=(0, 0))
    assert (rootfs / "etc/new/dir/file").read_bytes() == b"data"
    assert target.stat("/etc/new/dir/file").mode == 0o640
    assert not [p for p in os.listdir(rootfs / "etc/new/dir") if p != "file"]


def test_mkdir_symlink_remove(target, rootfs):
    target.mkdir("/home/pi/.ssh", 0o700)
    assert target.stat("/home/pi/.ssh").mode == 0o700

    target.symlink("/etc/link", "/lib/systemd/system/ssh.service")
    assert target.readlink("/etc/link") == "/lib/systemd/system/ssh.service"
    assert target.readlink("/etc/passwd") is None
    assert target.stat("/etc/link") is not None

    target.remove("/etc/link")
    assert target.readlink("/etc/link") is None
    target.remove("/etc/link")


def test_absolute_symlinks_stay_inside_target(target, rootfs, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (rootfs / "etc/escape").symlink_to(outside)
    (rootfs / "etc/resolv.conf").symlink_to("/run/resolv.conf")

    target.write("/etc/escape/file", b"x")
    assert not (outside / "file").exists()
    assert (rootfs / str(outside).lstrip("/") / "file").read_bytes() == b"x"

    target.write("/etc/resolv.conf", b"nameserver 1.1.1.1\n")
    assert (rootfs / "run/resolv.conf").exists()


def test_dotdot_cannot_leave_root(target, rootfs):
    assert target.read("/../../etc/passwd") == (rootfs / "etc/passwd").read_bytes()


def test_symlink_loop(target, rootfs):
    (rootfs / "etc/loop").symlink_to("loop")
    with pytest.raises(Error, match="too many levels"):
        target.read("/etc/loop")


def test_extra_mounts(rootfs, tmp_path):
    boot = tmp_path / "boot"
    boot.mkdir()
    (boot / "config.txt").write_text("arm_64bit=1\n")
    t = LocalTarget({"/": str(rootfs), "/boot/firmware": str(boot)})
    assert t.read("/boot/firmware/config.txt") == b"arm_64bit=1\n"
    assert t.read("/boot/firmware/cmdline.txt") is None


def test_offline_target_cannot_run(target):
    with pytest.raises(Unsupported):
        target.run(["true"])


def test_open_directory_target(rootfs):
    with open_target(str(rootfs)) as t:
        assert not t.live
        assert t.read("/etc/passwd")


def test_open_missing_path(tmp_path):
    with pytest.raises(Error):
        open_target(str(tmp_path / "missing"))
