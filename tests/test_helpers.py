import pytest

from board_tune import accounts, cmdline, systemd
from board_tune.errors import Error
from board_tune.plan import Plan


def test_cmdline_helpers():
    tokens = "console=tty1 root=/dev/sda2 quiet loglevel=3 loglevel=4".split()
    assert cmdline.key("console=tty1") == "console"
    assert cmdline.value("console=tty1") == "tty1"
    assert cmdline.value("quiet") == ""
    assert cmdline.without(tokens, "quiet", "loglevel") == ["console=tty1", "root=/dev/sda2"]
    assert cmdline.set_param(tokens, "loglevel", 7) == \
        ["console=tty1", "root=/dev/sda2", "quiet", "loglevel=7"]
    assert cmdline.set_param(["a"], "rootwait") == ["a", "rootwait"]


def test_accounts(target):
    plan = Plan(target)
    pi = accounts.lookup(plan, "pi")
    assert (pi.uid, pi.gid, pi.home) == (1000, 1000, "/home/pi")
    assert accounts.lookup(plan, "nobody") is None


def test_enable_unit(target):
    plan = Plan(target)
    links = systemd.enable(plan, "ssh.service")
    assert "/etc/systemd/system/sshd.service" in links
    changes = {c.path: c.detail for c in plan.changes()}
    assert changes == {
        "/etc/systemd/system/multi-user.target.wants/ssh.service":
            "/usr/lib/systemd/system/ssh.service",
        "/etc/systemd/system/sshd.service": "/usr/lib/systemd/system/ssh.service",
    }
    plan.apply(plan.changes())

    again = Plan(target)
    systemd.enable(again, "ssh.service")
    assert again.changes() == []


def test_enable_keeps_links_through_lib(target, rootfs):
    (rootfs / "etc/systemd/system/multi-user.target.wants/ssh.service").symlink_to(
        "/lib/systemd/system/ssh.service")
    (rootfs / "etc/systemd/system/sshd.service").symlink_to("/lib/systemd/system/ssh.service")
    plan = Plan(target)
    systemd.enable(plan, "ssh.service")
    assert plan.changes() == []


def test_enable_unmasks(target, rootfs):
    (rootfs / "etc/systemd/system/ssh.service").symlink_to("/dev/null")
    plan = Plan(target)
    systemd.enable(plan, "ssh.service")
    assert ("remove", "/etc/systemd/system/ssh.service") in \
        [(c.action, c.path) for c in plan.changes()]


def test_enable_missing_unit(target):
    with pytest.raises(Error, match="not installed"):
        systemd.enable(Plan(target), "nope.service")


def test_disable_unit(target, rootfs):
    link = rootfs / "etc/systemd/system/multi-user.target.wants/hciuart.service"
    link.symlink_to("/lib/systemd/system/hciuart.service")
    plan = Plan(target)
    systemd.disable(plan, "hciuart.service")
    assert [(c.action, c.path) for c in plan.changes()] == \
        [("remove", "/etc/systemd/system/multi-user.target.wants/hciuart.service")]
    assert systemd.disable(Plan(target), "nope.service") == []
