import json

import pytest

from board_tune.backup import Backup, DIRECTORY
from board_tune.cli import main
from board_tune.errors import Error
from board_tune.plan import Plan


def test_snapshot_is_private_and_restores_file_link_directory_and_absence(target, rootfs):
    (rootfs / "etc/secret").write_text("original")
    (rootfs / "etc/secret").chmod(0o640)
    (rootfs / "etc/link").symlink_to("/etc/secret")
    plan = Plan(target)
    plan.write("/etc/secret", "new", mode=0o600, sensitive=True)
    plan.remove("/etc/link")
    plan.mkdir("/home/pi/.ssh", 0o700)
    plan.write("/home/pi/.ssh/authorized_keys", "key", mode=0o600)
    assert "original" not in str(plan.changes())
    assert "sensitive content hidden" in str(plan.changes())
    plan.apply(plan.changes())
    backup_path = rootfs / DIRECTORY.lstrip("/")
    assert backup_path.stat().st_mode & 0o777 == 0o700
    file = backup_path / (plan.backup.id + ".json")
    assert file.stat().st_mode & 0o777 == 0o600
    Backup.load(target, plan.backup.id).restore()
    assert (rootfs / "etc/secret").read_text() == "original"
    assert (rootfs / "etc/secret").stat().st_mode & 0o777 == 0o640
    assert (rootfs / "etc/link").is_symlink()
    assert not (rootfs / "home/pi/.ssh").exists()


def test_in_place_symlink_destination_is_recovered(target, rootfs):
    (rootfs / "etc/destination").write_text("before")
    (rootfs / "etc/link").symlink_to("/etc/destination")
    plan = Plan(target)
    plan.write("/etc/link", "after")
    plan.apply(plan.changes())
    assert (rootfs / "etc/destination").read_text() == "after"
    plan.backup.restore()
    assert (rootfs / "etc/destination").read_text() == "before"
    assert (rootfs / "etc/link").is_symlink()


def test_mask_symlink_does_not_require_backing_up_device(target, rootfs):
    (rootfs / "etc/mask").symlink_to("/dev/null")
    plan = Plan(target)
    plan.remove("/etc/mask")
    plan.apply(plan.changes())
    plan.backup.restore()
    assert target.readlink("/etc/mask") == "/dev/null"


def test_apply_failure_restores_previous_files(target, rootfs, monkeypatch):
    before = (rootfs / "etc/passwd").read_bytes()
    write = target.write
    failed = False

    def fail_once(path, *args, **kwargs):
        nonlocal failed
        if path == "/etc/new" and not failed:
            failed = True
            raise Error("injected write failure")
        return write(path, *args, **kwargs)

    monkeypatch.setattr(target, "write", fail_once)
    plan = Plan(target)
    plan.write("/etc/passwd", "changed")
    plan.write("/etc/new", "new")
    with pytest.raises(Error, match="files restored"):
        plan.apply(plan.changes())
    assert (rootfs / "etc/passwd").read_bytes() == before
    assert not (rootfs / "etc/new").exists()
    assert plan.backup is not None


def test_cli_restore_preview_and_reversible_restore(target, rootfs, capsys):
    plan = Plan(target)
    plan.write("/etc/name", "new")
    plan.apply(plan.changes())
    identifier = plan.backup.id
    assert main([str(rootfs), "--list-backups"]) == 0
    assert identifier in capsys.readouterr().out
    assert main([str(rootfs), "--restore", identifier, "-n"]) == 0
    assert (rootfs / "etc/name").exists()
    assert main([str(rootfs), "--restore", identifier, "-y"]) == 0
    assert not (rootfs / "etc/name").exists()
    assert len(target.listdir(DIRECTORY)) == 2


def test_reject_invalid_manifest_before_restore(target, rootfs):
    backup = Backup.create(target, ["/etc/name"])
    path = rootfs / DIRECTORY.lstrip("/") / (backup.id + ".json")
    path.write_text(json.dumps({"version": 1, "entries": {"/etc/../passwd": {"kind": "absent"}}}))
    with pytest.raises(Error, match="invalid"):
        Backup.load(target, backup.id)
    with pytest.raises(Error, match="invalid"):
        Backup.load(target, "../../etc/passwd")
