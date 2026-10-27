import subprocess

import pytest

from board_tune.cli import build_plan
from board_tune.errors import Error
from board_tune.hooks.system import aliases
from board_tune.kconfig import Configuration
from board_tune.registry import Registry


@pytest.fixture
def shellfs(rootfs):
    path = rootfs / "etc/bash.bashrc"
    path.write_text('# Existing configuration\nexport SITE_SETTING=preserved')
    path.chmod(0o640)
    return path


@pytest.fixture
def configure(target, tmp_path):
    registry = Registry.load()

    def invoke(action):
        conf = Configuration(registry, str(tmp_path))
        conf.set("SYSTEM=y")
        conf.set("SYSTEM_ALIAS_LL_" + action + "=y")
        return build_plan(registry, conf.config, target)

    return invoke


def test_alias_keep_enable_readback_remove_and_backup(shellfs, configure, target, tmp_path):
    original = shellfs.read_bytes()
    assert not configure("KEEP").changes()
    plan = configure("ENABLE")
    assert [change.path for change in plan.changes()] == [aliases.PATH]
    assert not plan.commands
    plan.apply(plan.changes())
    assert shellfs.stat().st_mode & 0o777 == 0o640
    assert not configure("ENABLE").changes()
    assert not configure("KEEP").changes()
    registry = Registry.load()
    conf = Configuration(registry, str(tmp_path))
    conf.read_target(registry, target)
    assert conf.config.enabled("SYSTEM_ALIAS_LL_ENABLE")
    removal = configure("REMOVE")
    removal.apply(removal.changes())
    assert shellfs.read_bytes() == original
    assert not configure("REMOVE").changes()
    removal.backup.restore()
    assert shellfs.read_text().endswith(aliases.BLOCK)
    plan.backup.restore()
    assert shellfs.read_bytes() == original
    conf = Configuration(registry, str(tmp_path))
    conf.read_target(registry, target)
    assert not conf.config.enabled("SYSTEM_ALIAS_LL_ENABLE")
    assert aliases.read(configure("KEEP")) == {}


def test_alias_requires_existing_regular_startup_file(rootfs, configure):
    assert not configure("REMOVE").changes()
    with pytest.raises(Error, match="existing regular"):
        configure("ENABLE")
    path = rootfs / "etc/bash.bashrc"
    path.symlink_to("/etc/missing")
    with pytest.raises(Error, match="existing regular"):
        configure("ENABLE")


@pytest.mark.parametrize("broken", [
    aliases.BEGIN + "\n", aliases.END + "\n", aliases.BLOCK * 2,
    aliases.BLOCK.replace("ls -la", "ls -l"),
])
def test_modified_alias_block_is_preserved(shellfs, configure, broken):
    shellfs.write_text(broken)
    assert not configure("KEEP").changes()
    assert aliases.read(configure("KEEP")) == {}
    for action in ("ENABLE", "REMOVE"):
        with pytest.raises(Error, match="modified or is malformed"):
            configure(action)
    assert shellfs.read_text() == broken


def bash(rcfile, command, interactive=True):
    result = subprocess.run(
        ["bash", "--noprofile", "--rcfile", str(rcfile),
         "-ic" if interactive else "-c", command],
        text=True, capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_interactive_alias_passes_flags_and_arguments(shellfs, configure):
    plan = configure("ENABLE")
    plan.apply(plan.changes())
    # A stub verifies alias expansion without relying on ls output or locale.
    output = bash(shellfs, 'ls() { printf "<%s>\\n" "$@"; }\nll -- "path with spaces"')
    assert output == "<-la>\n<-->\n<path with spaces>\n"


@pytest.mark.parametrize("definition,command,expected", [
    ("alias ll='printf personal'\n", "ll", "personal"),
    ("ll() { printf function; }\n", "ll", "function"),
])
def test_existing_site_definitions_take_precedence(shellfs, configure, definition, command, expected):
    shellfs.write_text(definition)
    plan = configure("ENABLE")
    plan.apply(plan.changes())
    assert bash(shellfs, command) == expected
    removal = configure("REMOVE")
    removal.apply(removal.changes())
    assert shellfs.read_text() == definition


def test_personal_alias_wins_and_repeated_source_preserves_it(shellfs, configure, tmp_path):
    plan = configure("ENABLE")
    plan.apply(plan.changes())
    personal = tmp_path / "personal.bashrc"
    personal.write_text(f'. "{shellfs}"\nalias ll="printf personal"\n. "{shellfs}"\n')
    assert bash(personal, "ll") == "personal"


def test_noninteractive_and_posix_shells_do_not_get_alias(shellfs, configure):
    plan = configure("ENABLE")
    plan.apply(plan.changes())
    command = '. "$1"; if alias ll >/dev/null 2>&1; then exit 1; fi; printf quiet'
    for executable, flag in (("bash", "-c"), ("sh", "-ic")):
        result = subprocess.run([executable, flag, command, "sh", str(shellfs)],
                                text=True, capture_output=True)
        assert result.returncode == 0, result.stderr
        assert result.stdout == "quiet"
