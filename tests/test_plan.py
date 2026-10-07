from board_tune.plan import Plan


def test_nothing_touches_target_until_apply(target, rootfs):
    plan = Plan(target)
    plan.write("/etc/hostname", "board\n", mode=0o644, owner=(0, 0))
    plan.remove("/etc/passwd")
    assert not (rootfs / "etc/hostname").exists()
    assert (rootfs / "etc/passwd").exists()

    plan.apply(plan.changes())
    assert (rootfs / "etc/hostname").read_text() == "board\n"
    assert not (rootfs / "etc/passwd").exists()


def test_staged_view(target):
    plan = Plan(target)
    plan.write("/etc/a", "1")
    plan.symlink("/etc/b", "/etc/a")
    plan.remove("/etc/passwd")
    assert plan.read_text("/etc/a") == "1"
    assert plan.readlink("/etc/b") == "/etc/a"
    assert plan.read("/etc/passwd") is None
    assert not plan.exists("/etc/passwd")
    assert plan.exists("/etc/ssh/sshd_config")


def test_changes(target):
    plan = Plan(target)
    plan.write("/boot/firmware/cmdline.txt", plan.read("/boot/firmware/cmdline.txt"))
    plan.write("/etc/passwd", plan.read_text("/etc/passwd") + "x:x:1:1::/:/bin/sh\n")
    plan.write("/etc/new", "new\n", mode=0o600)
    plan.mkdir("/home/pi/.ssh", 0o700)
    plan.symlink("/etc/link", "/dev/null")
    plan.remove("/etc/missing")
    plan.remove("/etc/os-release")

    changes = {c.path: c for c in plan.changes()}
    assert "/boot/firmware/cmdline.txt" not in changes
    assert "/etc/missing" not in changes
    assert changes["/etc/passwd"].action == "modify"
    assert "+x:x:1:1::/:/bin/sh" in changes["/etc/passwd"].detail
    assert changes["/etc/new"].action == "create"
    assert changes["/home/pi/.ssh"].action == "mkdir"
    assert changes["/etc/link"].action == "symlink"
    assert changes["/etc/os-release"].action == "remove"

    plan.apply(list(changes.values()))
    assert Plan(target).changes() == []
    again = Plan(target)
    again.write("/etc/new", "new\n", mode=0o600)
    again.mkdir("/home/pi/.ssh", 0o700)
    again.symlink("/etc/link", "/dev/null")
    assert again.changes() == []


def test_mode_change_is_detected(target, rootfs):
    (rootfs / "etc/new").write_text("x")
    (rootfs / "etc/new").chmod(0o644)
    plan = Plan(target)
    plan.write("/etc/new", "x", mode=0o600)
    [change] = plan.changes()
    assert change.action == "attrs"
    assert plan.changed("/etc/new")


def test_changed_since(target):
    plan = Plan(target)
    plan.write("/etc/a", "1")
    snapshot = plan.snapshot()
    assert not plan.changed_since(snapshot)
    plan.write("/etc/passwd", plan.read("/etc/passwd"))
    assert not plan.changed_since(snapshot)
    plan.write("/etc/passwd", "")
    assert plan.changed_since(snapshot)


def test_commands_only_on_live_targets(target):
    plan = Plan(target)
    plan.command(["systemctl", "daemon-reload"])
    assert plan.commands == []
