"""Primary buses: preservation, explicit disable, readback and reboot-only plans."""

import pytest

from board_tune.cli import build_plan, main
from board_tune.kconfig import Configuration
from board_tune.platforms.rpi.hardware import I2C_MODULES
from board_tune.registry import Registry


@pytest.fixture
def conf(tmp_path):
    return Configuration(Registry.load(), str(tmp_path))


def configure(conf, *values):
    for value in ("BOARD_RPI5=y", "HARDWARE=y", *values):
        conf.set(value)


def test_keep_and_unchecked_leave_everything_untouched(conf, target, rootfs):
    module = rootfs / I2C_MODULES.lstrip("/")
    module.parent.mkdir(parents=True)
    module.write_text("site module settings\n")
    configure(conf)
    assert build_plan(Registry.load(), conf.config, target).changes() == []
    configure(conf, "HARDWARE_I2C_ENABLE=y", "HARDWARE=n")
    assert build_plan(Registry.load(), conf.config, target).changes() == []


@pytest.mark.parametrize("board", ["RPI4", "RPI5"])
def test_enable_both_is_idempotent_and_board_specific(conf, target, rootfs, board):
    configure(conf, f"BOARD_{board}=y", "HARDWARE_I2C_ENABLE=y", "HARDWARE_SPI_ENABLE=y")
    path = rootfs / "boot/firmware/config.txt"
    original = path.read_text()
    plan = build_plan(Registry.load(), conf.config, target)
    assert not plan.commands and not plan.pre_commands
    assert any("reboot" in note for note in plan.notes)
    plan.apply(plan.changes())
    text = path.read_text()
    assert text.startswith(original)
    assert f"[all]\n[{board.lower().replace('rpi', 'pi')}]\ndtoverlay=\ndtparam=i2c_arm=on" in text
    assert "dtparam=spi=on" in text
    module = rootfs / I2C_MODULES.lstrip("/")
    assert module.read_text().endswith("i2c-dev\n")
    assert module.stat().st_mode & 0o777 == 0o644
    again = build_plan(Registry.load(), conf.config, target)
    assert again.changes() == []
    assert again.notes == []


def test_disable_preserves_external_modules_and_other_buses(conf, target, rootfs):
    configure(conf, "HARDWARE_I2C_ENABLE=y", "HARDWARE_SPI_ENABLE=y")
    plan = build_plan(Registry.load(), conf.config, target)
    plan.apply(plan.changes())
    external = rootfs / "etc/modules"
    external.write_text("i2c-dev\nspi-dev\n")
    boot = rootfs / "boot/firmware/config.txt"
    with boot.open("a") as stream:
        stream.write("\ndtparam=i2c_vc=on\ndtoverlay=spi1-3cs\n")
    configure(conf, "HARDWARE_I2C_DISABLE=y", "HARDWARE_SPI_KEEP=y")
    plan = build_plan(Registry.load(), conf.config, target)
    plan.apply(plan.changes())
    text = boot.read_text()
    assert "dtparam=i2c_arm=off" in text
    assert "dtparam=spi=on" in text
    assert "dtparam=i2c_vc=on\ndtoverlay=spi1-3cs\n" in text
    assert (rootfs / I2C_MODULES.lstrip("/")).read_text().endswith("i2c-dev\n")
    assert external.read_text() == "i2c-dev\nspi-dev\n"
    assert build_plan(Registry.load(), conf.config, target).changes() == []


def test_site_override_is_kept_but_precedes_new_managed_choice(conf, target, rootfs):
    configure(conf, "HARDWARE_SPI_ENABLE=y")
    plan = build_plan(Registry.load(), conf.config, target)
    plan.apply(plan.changes())
    path = rootfs / "boot/firmware/config.txt"
    with path.open("a") as stream:
        stream.write("\n[all]\ndtparam=spi=off\n[gpio4=1]\n")
    plan = build_plan(Registry.load(), conf.config, target)
    plan.apply(plan.changes())
    text = path.read_text()
    assert text.index("dtparam=spi=off") < text.index("dtparam=spi=on")
    assert text.count("# >>> board-tune: hardware_spi >>>") == 1
    assert "[gpio4=1]\n\n# >>> board-tune: hardware_spi >>>\n[all]" in text
    assert build_plan(Registry.load(), conf.config, target).changes() == []


def test_moving_block_preserves_scope_of_later_site_lines(conf, target, rootfs):
    path = rootfs / "boot/firmware/config.txt"
    path.write_text("[pi4]\ndtparam=audio=on\n")
    configure(conf, "HARDWARE_SPI_ENABLE=y")
    plan = build_plan(Registry.load(), conf.config, target)
    plan.apply(plan.changes())
    with path.open("a") as stream:
        stream.write("dtparam=spi=off\n")
    plan = build_plan(Registry.load(), conf.config, target)
    plan.apply(plan.changes())
    text = path.read_text()
    # The site's override was global after the old managed block ended.
    assert "[all]\ndtparam=spi=off\n" in text
    assert text.index("dtparam=spi=off") < text.index("dtparam=spi=on")
    assert build_plan(Registry.load(), conf.config, target).changes() == []


@pytest.mark.parametrize("text,i2c,spi", [
    ("dtparam=i2c_arm=on,spi=off\n", "ENABLE", "DISABLE"),
    ("device_tree_param=i2c,spi=1\n", "ENABLE", "ENABLE"),
    ("dtparam=i2c=on\ndtparam=i2c_arm=false\n", "DISABLE", "KEEP"),
    ("[pi4]\ndtparam=i2c=on\n[pi5]\ndtparam=spi=on\n", "KEEP", "ENABLE"),
    ("[none]\ndtparam=i2c=on\n[all]\ndtparam=spi=0\n", "KEEP", "DISABLE"),
    ("dtparam=i2c_arm=bad\n#dtparam=spi=on\n", "KEEP", "KEEP"),
    ("[gpio4=1]\ndtparam=i2c_arm=on\n[pi5]\ndtparam=spi=on\n", "KEEP", "KEEP"),
    ("dtparam=i2c=on\ninclude site.txt\n", "KEEP", "KEEP"),
    ("dtoverlay=custom\ndtparam=i2c_arm=on\ndtoverlay=\ndtparam=spi=on\n", "KEEP", "ENABLE"),
])
def test_reads_explicit_base_parameters_without_guessing(conf, target, rootfs, text, i2c, spi):
    path = rootfs / "boot/firmware/config.txt"
    path.write_text(text)
    registry = Registry.load()
    conf.read_target(registry, target, registry.platforms[0].boards[1])
    assert conf.config.enabled(f"HARDWARE_I2C_{i2c}")
    assert conf.config.enabled(f"HARDWARE_SPI_{spi}")
    assert conf.problems() == []
    assert path.read_text() == text


def test_managed_roundtrip_detects_board_without_serial_settings(conf, target, rootfs):
    configure(conf, "HARDWARE_I2C_ENABLE=y", "HARDWARE_SPI_DISABLE=y")
    plan = build_plan(Registry.load(), conf.config, target)
    plan.apply(plan.changes())
    conf.set("BOARD_RPI4=y")
    conf.read_target(Registry.load(), target)
    assert conf.config.enabled("BOARD_RPI5")
    assert conf.config.enabled("HARDWARE_I2C_ENABLE")
    assert conf.config.enabled("HARDWARE_SPI_DISABLE")


def test_live_plan_does_not_touch_running_pins_or_load_modules(conf, target, rootfs, monkeypatch):
    model = rootfs / "proc/device-tree/model"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"Raspberry Pi 5 Model B\0")
    target.live = True
    monkeypatch.setattr(target, "run", lambda *a, **kw: pytest.fail("runtime hardware command"))
    configure(conf, "HARDWARE_I2C_ENABLE=y", "HARDWARE_SPI_ENABLE=y")
    plan = build_plan(Registry.load(), conf.config, target)
    assert not plan.pre_commands and not plan.commands
    plan.apply(plan.changes())


def test_old_boot_location_and_dry_run(conf, rootfs, tmp_path, capsys):
    boot = rootfs / "boot/firmware"
    boot.rename(rootfs / "old-boot")
    (rootfs / "boot").rmdir()
    (rootfs / "old-boot").rename(rootfs / "boot")
    path = rootfs / "boot/config.txt"
    original = path.read_bytes()
    profile = tmp_path / "hardware.cfg"
    profile.write_text("CONFIG_HARDWARE=y\nCONFIG_HARDWARE_I2C_ENABLE=y\n")
    assert main([str(rootfs), "--load", str(profile), "--dry-run"]) == 0
    assert "dtparam=i2c_arm=on" in capsys.readouterr().out
    assert path.read_bytes() == original
    assert not (rootfs / "var/backups/board-tune").exists()


def test_unterminated_block_refuses_changes(conf, rootfs, target):
    with (rootfs / "boot/firmware/config.txt").open("a") as stream:
        stream.write("# >>> board-tune: hardware_spi >>>\ndtparam=spi=on\n")
    configure(conf, "HARDWARE_SPI_DISABLE=y")
    from board_tune.errors import Error
    with pytest.raises(Error, match="not terminated"):
        build_plan(Registry.load(), conf.config, target)
