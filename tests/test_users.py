"""Account changes stay in the target and compose with SSH key provisioning."""

import pytest

from board_tune.cli import build_plan, main
from board_tune.kconfig import Configuration
from board_tune.registry import Registry

HASH = "$6$testSalt$" + "a" * 86


@pytest.fixture
def userfs(rootfs):
    files = {
        "etc/group": "root:x:0:\npi:x:1000:\nsudo:x:27:pi\ndialout:x:20:\n",
        "etc/shadow": "root:*:20000:0:99999:7:::\npi:!old:20000:0:99999:7:::\n",
        "etc/gshadow": "root:*::\npi:!::\nsudo:!::pi\ndialout:!::\n",
        "etc/login.defs": "UID_MIN 1000\nUID_MAX 60000\nGID_MIN 1000\nGID_MAX 60000\n",
        "etc/sudoers": "root ALL=(ALL:ALL) ALL\n@includedir /etc/sudoers.d\n",
        "bin/bash": "test shell\n",
        "bin/sh": "test shell\n",
    }
    for name, text in files.items():
        path = rootfs / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        if name.startswith("bin/"):
            path.chmod(0o755)
        elif name.endswith(("shadow", "gshadow")):
            path.chmod(0o640)
    return rootfs


@pytest.fixture
def run(userfs, tmp_path, capsys):
    def invoke(config="", expect=0, extra=()):
        profile = tmp_path / "profile.cfg"
        profile.write_text("CONFIG_USERS=y\nCONFIG_USERS_NAME=\"alice\"\n" + config)
        result = main([str(userfs), "--load", str(profile), "-y", *extra])
        out, err = capsys.readouterr()
        assert result == expect, out + err
        return out + err
    return invoke


def record(root, database, name):
    return next(line.split(":") for line in (root / "etc" / database).read_text().splitlines()
                if line.split(":", 1)[0] == name)


def test_create_locked_user_idempotently_with_owned_home(userfs, run):
    before_root = record(userfs, "passwd", "root")
    run()
    user = record(userfs, "passwd", "alice")
    assert user == ["alice", "x", "1001", "1001", "", "/home/alice", "/bin/bash"]
    assert record(userfs, "group", "alice") == ["alice", "x", "1001", ""]
    assert record(userfs, "shadow", "alice")[1] == "!"
    assert record(userfs, "gshadow", "alice") == ["alice", "!", "", ""]
    assert (userfs / "home/alice").stat().st_mode & 0o777 == 0o750
    assert record(userfs, "passwd", "root") == before_root
    assert "Nothing to change." in run()


def test_hash_import_is_hidden_from_preview_and_profile(userfs, run, tmp_path):
    source = tmp_path / "hash"
    source.write_text(HASH + "\n")
    config = f'CONFIG_USERS_PASSWORD_HASH=y\nCONFIG_USERS_PASSWORD_HASH_FILE="{source}"\n'
    saved = tmp_path / "saved.cfg"
    output = run(config, extra=("--save", str(saved)))
    assert record(userfs, "shadow", "alice")[1] == HASH
    assert HASH not in output
    assert HASH not in saved.read_text()
    assert "sensitive content hidden" in output
    assert "Nothing to change." in run(config)


def test_dry_run_creates_no_account_or_backup(userfs, run):
    run(extra=("--dry-run",))
    assert "alice:" not in (userfs / "etc/passwd").read_text()
    assert not (userfs / "home/alice").exists()
    assert not (userfs / "var/backups/board-tune").exists()


def test_add_and_replace_groups_keep_databases_consistent(userfs, run):
    run('CONFIG_USERS_GROUPS_ADD=y\nCONFIG_USERS_GROUPS="sudo,dialout"\n')
    assert record(userfs, "group", "sudo")[3] == "pi,alice"
    assert record(userfs, "gshadow", "sudo")[3] == "pi,alice"
    run('CONFIG_USERS_GROUPS_REPLACE=y\nCONFIG_USERS_GROUPS="dialout"\n')
    assert record(userfs, "group", "sudo")[3] == "pi"
    assert record(userfs, "group", "dialout")[3] == "alice"
    run('CONFIG_USERS_GROUPS_REPLACE=y\nCONFIG_USERS_GROUPS=""\n')
    assert record(userfs, "group", "dialout")[3] == ""
    assert record(userfs, "passwd", "alice")[3] == "1001"


def test_sudo_grant_change_and_removal_only_manage_own_rule(userfs, run):
    directory = userfs / "etc/sudoers.d"
    directory.mkdir()
    other = directory / "site-policy"
    other.write_text("%sudo ALL=(ALL:ALL) ALL\n")
    run("CONFIG_USERS_SUDO_NOPASSWD=y\n")
    rule = directory / "90-board-tune-alice"
    assert "alice ALL=(ALL:ALL) NOPASSWD: ALL" in rule.read_text()
    assert rule.stat().st_mode & 0o777 == 0o440
    run("CONFIG_USERS_SUDO_PASSWORD=y\n")
    assert "NOPASSWD" not in rule.read_text()
    run("CONFIG_USERS_SUDO_REMOVE=y\n")
    assert not rule.exists()
    assert other.read_text() == "%sudo ALL=(ALL:ALL) ALL\n"


def test_existing_user_keep_and_explicit_password_lock(userfs, run):
    shadow = userfs / "etc/shadow"
    shadow.write_text(shadow.read_text().replace("pi:!old:", "pi:" + HASH + ":"))
    output = run('CONFIG_USERS_NAME="pi"\n')
    assert "Nothing to change." in output
    run('CONFIG_USERS_NAME="pi"\nCONFIG_USERS_PASSWORD_LOCK=y\n')
    assert record(userfs, "shadow", "pi")[1] == "!" + HASH
    assert "Nothing to change." in run('CONFIG_USERS_NAME="pi"\nCONFIG_USERS_PASSWORD_LOCK=y\n')


def test_create_then_authorize_ssh_key_in_one_plan(userfs, run, tmp_path):
    key = tmp_path / "key.pub"
    key.write_text("ssh-ed25519 AAAAtest operator\n")
    run(f'CONFIG_SSH=y\nCONFIG_SSH_AUTHORIZED_KEYS="{key}"\nCONFIG_SSH_AUTHORIZED_KEYS_USER="alice"\n')
    assert (userfs / "home/alice/.ssh/authorized_keys").read_text() == key.read_text()


@pytest.mark.parametrize("config,message", [
    ('CONFIG_USERS_NAME="bad:name"\n', "USERS_NAME"),
    ('CONFIG_USERS_GROUPS_ADD=y\nCONFIG_USERS_GROUPS="missing"\n', "unknown group"),
    ('CONFIG_USERS_UID=1000\n', "already in use"),
    ('CONFIG_USERS_CREATE=n\n', "does not exist"),
    ('CONFIG_USERS_SHELL="/etc/passwd"\n', "executable"),
    ('CONFIG_USERS_HOME="/etc/alice"\n', "below /home"),
    ('CONFIG_USERS_NAME="pi"\nCONFIG_USERS_UID=1005\n', "cannot be changed"),
    ('CONFIG_USERS_NAME="pi"\nCONFIG_USERS_HOME="/home/other"\n', "cannot be moved"),
])
def test_validation_precedes_writes(userfs, run, config, message):
    before = (userfs / "etc/passwd").read_text()
    assert message in run(config, expect=1)
    assert (userfs / "etc/passwd").read_text() == before
    assert not (userfs / "var/backups/board-tune").exists()


def test_invalid_hash_not_echoed(userfs, run, tmp_path):
    source = tmp_path / "plaintext"
    source.write_text("do-not-leak-this-password\n")
    output = run(f'CONFIG_USERS_PASSWORD_HASH=y\nCONFIG_USERS_PASSWORD_HASH_FILE="{source}"\n', expect=1)
    assert "do-not-leak" not in output


def test_readback_is_action_based_and_does_not_clone_identity(userfs, target, tmp_path):
    registry = Registry.load()
    conf = Configuration(registry, str(tmp_path))
    conf.read_target(registry, target)
    assert conf.config.str("USERS_NAME") == "pi"
    assert conf.config.enabled("USERS_PASSWORD_KEEP")
    assert conf.config.enabled("USERS_GROUPS_KEEP")
    assert conf.config.str("USERS_SHELL") == ""
    conf.set("USERS_NAME=alice")
    plan = build_plan(registry, conf.config, target, {"ssh", "serial_console", "network"})
    plan.apply(plan.changes())
    assert record(userfs, "passwd", "alice")[2] == "1001"
    assert record(userfs, "passwd", "pi")[2] == "1000"


def test_live_uses_native_commands_and_private_stdin(userfs, target, tmp_path, monkeypatch):
    (userfs / "usr/sbin").mkdir(parents=True)
    (userfs / "usr/sbin/visudo").write_text("test binary")
    source = tmp_path / "hash"
    source.write_text(HASH)
    registry = Registry.load()
    conf = Configuration(registry, str(tmp_path))
    for assignment in ("USERS=y", "USERS_NAME=alice", "USERS_PASSWORD_HASH=y",
                       f"USERS_PASSWORD_HASH_FILE={source}", "USERS_SUDO_NOPASSWD=y"):
        conf.set(assignment)
    target.live = True
    calls = []
    monkeypatch.setattr(target, "run", lambda argv, input=None: calls.append((argv, input)))
    plan = build_plan(registry, conf.config, target)
    assert any(argv[0] == "useradd" for argv in plan.pre_commands)
    assert not {"/etc/passwd", "/etc/group", "/etc/shadow", "/etc/gshadow"} & {c.path for c in plan.changes()}
    assert HASH not in str(plan.pre_commands + plan.commands + plan.changes())
    plan.apply(plan.changes())
    password = next(call for call in calls if call[0][0] == "chpasswd")
    assert password[1] == f"alice:{HASH}\n".encode()
    assert next(i for i, call in enumerate(calls) if call[0][0] == "useradd") < calls.index(password)


@pytest.mark.parametrize("assignment", [
    "USERS_SHELL=/bin/sh",
    "USERS_GROUPS_REPLACE=y",
    "USERS_SUDO_REMOVE=y",
])
def test_live_refuses_to_break_connected_administrator(userfs, target, tmp_path, assignment):
    from board_tune.errors import Error

    rules = userfs / "etc/sudoers.d"
    rules.mkdir()
    (rules / "90-board-tune-pi").write_text("pi ALL=(ALL:ALL) NOPASSWD: ALL\n")
    conf = Configuration(Registry.load(), str(tmp_path))
    for value in ("USERS=y", "USERS_NAME=pi", assignment):
        conf.set(value)
    target.live, target.login_uid = True, 1000
    with pytest.raises(Error, match="connect as another administrator"):
        build_plan(Registry.load(), conf.config, target)
