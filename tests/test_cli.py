import pytest

from board_tune.cli import main

PROFILE = """\
CONFIG_BOARD_DUMMY_B=y
CONFIG_GREETING=y
CONFIG_GREETING_TEXT="hi"
"""


@pytest.fixture
def profile(tmp_path, ext_path):
    path = tmp_path / "profile.cfg"
    path.write_text(PROFILE)
    return str(path)


def test_batch_apply_is_idempotent(rootfs, profile, capsys):
    assert main([str(rootfs), "--load", profile, "-y"]) == 0
    assert (rootfs / "etc/motd").read_text() == "hi\n"
    out = capsys.readouterr().out
    assert "create   /etc/motd" in out
    assert "Dummy B" in out

    assert main([str(rootfs), "--load", profile, "-y"]) == 0
    assert "Nothing to change." in capsys.readouterr().out


def test_set_overrides_loaded_values(rootfs, profile):
    assert main([str(rootfs), f"--load={profile}", "--set", "GREETING_TEXT=yo", "-y"]) == 0
    assert (rootfs / "etc/motd").read_text() == "yo\n"


def test_dry_run_changes_nothing(rootfs, profile, capsys):
    assert main([str(rootfs), "--load", profile, "--dry-run"]) == 0
    assert not (rootfs / "etc/motd").exists()
    assert "+hi" in capsys.readouterr().out


def test_confirmation_requires_terminal(rootfs, profile, capsys):
    assert main([str(rootfs), "--load", profile]) == 1
    assert "--yes" in capsys.readouterr().err
    assert not (rootfs / "etc/motd").exists()


def test_invalid_configuration_fails(rootfs, tmp_path, ext_path, capsys):
    bad = tmp_path / "bad.cfg"
    bad.write_text("CONFIG_BOARD_DUMMY_A=y\nCONFIG_GREETING=y\nCONFIG_DUMMY_B_LOUD=y\n")
    assert main([str(rootfs), "--load", str(bad), "-y"]) == 1
    assert "DUMMY_B_LOUD=y is not possible" in capsys.readouterr().err
    assert not (rootfs / "etc/motd").exists()


def test_validate_and_save_without_target(profile, tmp_path, capsys):
    assert main(["--load", profile]) == 0
    assert "Configuration is valid." in capsys.readouterr().out

    saved = tmp_path / "merged.cfg"
    assert main(["--load", profile, "--set", "GREETING_REPEAT=2", "--save", str(saved)]) == 0
    text = saved.read_text()
    assert "CONFIG_GREETING_REPEAT=2" in text
    assert "CONFIG_BOARD_DUMMY_B=y" in text


def test_menu_requires_terminal(ext_path, capsys):
    assert main([]) == 1
    assert "needs a terminal" in capsys.readouterr().err
