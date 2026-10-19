"""1-Wire boot configuration without a physical sensor or live pin changes."""

import pytest

from board_tune.cli import build_plan, main
from board_tune.errors import Error
from board_tune.kconfig import Configuration
from board_tune.registry import Registry


@pytest.fixture
def conf(tmp_path, rootfs):
    overlay = rootfs / "boot/firmware/overlays/w1-gpio.dtbo"
    overlay.parent.mkdir(parents=True)
    overlay.write_bytes(b"test overlay availability")
    return Configuration(Registry.load(), str(tmp_path))


def configure(conf, *values):
    for value in ("HARDWARE=y", *values):
        conf.set(value)


def apply(conf, target):
    plan = build_plan(Registry.load(), conf.config, target)
    assert not plan.commands and not plan.pre_commands
    plan.apply(plan.changes())
    return plan


def read(rootfs, target, tmp_path):
    work = tmp_path / "read"
    work.mkdir(exist_ok=True)
    registry = Registry.load()
    conf = Configuration(registry, str(work))
    notes = conf.read_target(registry, target, registry.platforms[0].boards[1])
    assert not conf.problems()
    return conf, notes


@pytest.mark.parametrize("board", ["RPI4", "RPI5"])
def test_enable_pin_change_disable_and_readback(conf, target, rootfs, tmp_path, board):
    configure(conf, f"BOARD_{board}=y", "HARDWARE_ONEWIRE_ENABLE=y")
    original = (rootfs / "boot/firmware/config.txt").read_text()
    plan = apply(conf, target)
    assert any("reboot" in note for note in plan.notes)
    path = rootfs / "boot/firmware/config.txt"
    text = path.read_text()
    assert text.startswith(original)
    assert f"[{board.replace('RPI', 'pi')}]\ndtoverlay=w1-gpio,gpiopin=4\ndtoverlay=\n[all]" in text
    assert apply(conf, target).changes() == []
    configure(conf, "HARDWARE_ONEWIRE_GPIO=17")
    apply(conf, target)
    text = path.read_text()
    assert text.count("dtoverlay=w1-gpio") == 1
    assert "gpiopin=17" in text
    imported, _ = read(rootfs, target, tmp_path)
    assert imported.config.enabled(f"BOARD_{board}")
    assert imported.config.enabled("HARDWARE_ONEWIRE_ENABLE")
    assert imported.config.int("HARDWARE_ONEWIRE_GPIO") == 17
    configure(conf, "HARDWARE_ONEWIRE_DISABLE=y")
    apply(conf, target)
    assert "dtoverlay=w1-gpio" not in path.read_text()
    assert "dtoverlay=vc4-kms-v3d" in path.read_text()
    assert apply(conf, target).changes() == []
    imported, _ = read(rootfs, target, tmp_path)
    assert imported.config.enabled("HARDWARE_ONEWIRE_DISABLE")


def test_keep_and_disable_without_managed_overlay_are_noops(conf, target):
    configure(conf)
    assert apply(conf, target).changes() == []
    configure(conf, "HARDWARE_ONEWIRE_DISABLE=y")
    assert apply(conf, target).changes() == []


def test_missing_overlay_is_reported_without_writes(conf, target, rootfs):
    (rootfs / "boot/firmware/overlays/w1-gpio.dtbo").unlink()
    configure(conf, "HARDWARE_ONEWIRE_ENABLE=y")
    with pytest.raises(Error, match="not installed"):
        build_plan(Registry.load(), conf.config, target)
    assert not (rootfs / "var/backups/board-tune").exists()


def test_directory_is_not_an_installed_overlay(conf, target, rootfs):
    overlay = rootfs / "boot/firmware/overlays/w1-gpio.dtbo"
    overlay.unlink()
    overlay.mkdir()
    configure(conf, "HARDWARE_ONEWIRE_ENABLE=y")
    with pytest.raises(Error, match="not installed"):
        build_plan(Registry.load(), conf.config, target)


def test_pi5_specific_overlay_is_not_imported_as_supported_on_pi4(conf, target, rootfs):
    (rootfs / "boot/firmware/config.txt").write_text("dtoverlay=w1-gpio-pi5\n")
    registry = Registry.load()
    notes = conf.read_target(registry, target, registry.platforms[0].boards[0])
    assert conf.config.enabled("HARDWARE_ONEWIRE_KEEP")
    assert any("1-Wire" in note and "unsupported" in note for note in notes)
    configure(conf, "HARDWARE_ONEWIRE_ENABLE=y")
    with pytest.raises(Error, match="ambiguous"):
        build_plan(registry, conf.config, target)


@pytest.mark.parametrize("text,pin", [
    ("dtoverlay=w1-gpio\n", 4),
    ("dtoverlay=w1-gpio,gpiopin=17\n", 17),
    ("dtoverlay=w1-gpio:gpiopin=0x12\n", 18),
    ("dtoverlay=w1-gpio-pi5,pullup=on\ndtparam=gpiopin=22\n", 22),
    ("[pi4]\ndtoverlay=w1-gpio,gpiopin=7\n[pi5]\ndtoverlay=w1-gpio,gpiopin=23\n", 23),
])
def test_external_overlay_readback_is_idempotent(conf, target, rootfs, tmp_path, text, pin):
    path = rootfs / "boot/firmware/config.txt"
    path.write_text(text)
    imported, notes = read(rootfs, target, tmp_path)
    assert imported.config.enabled("HARDWARE_ONEWIRE_ENABLE")
    assert imported.config.int("HARDWARE_ONEWIRE_GPIO") == pin
    assert any("external overlay" in note for note in notes)
    assert build_plan(Registry.load(), imported.config, target, {"serial_console", "ssh"}).changes() == []
    configure(conf, "BOARD_RPI5=y", "HARDWARE_ONEWIRE_ENABLE=y", f"HARDWARE_ONEWIRE_GPIO={pin}")
    assert apply(conf, target).changes() == []
    configure(conf, "HARDWARE_ONEWIRE_GPIO=24")
    with pytest.raises(Error, match="external overlay"):
        build_plan(Registry.load(), conf.config, target)
    configure(conf, "HARDWARE_ONEWIRE_DISABLE=y")
    with pytest.raises(Error, match="external overlay"):
        build_plan(Registry.load(), conf.config, target)
    assert path.read_text() == text


@pytest.mark.parametrize("text", [
    "include usercfg.txt\n",
    "[gpio4=1]\ndtoverlay=w1-gpio\n",
    "dtoverlay=w1-gpio,gpiopin=99\n",
    "dtoverlay=w1-gpio,gpiopin=bad\n",
    "dtoverlay=w1-gpio,unknown=1\n",
    "dtoverlay=w1-gpio-pullup,extpullup=5\n",
    "dtoverlay=w1-gpio\ndtoverlay=w1-gpio,gpiopin=17\n",
    "dtoverlay=w1-gpio\n[gpio7=1]\ndtparam=gpiopin=17\n",
    "dtoverlay=w1-gpio\n[gpio7=1]\ndtoverlay=other\n[all]\ndtparam=gpiopin=17\n",
])
def test_ambiguous_or_unsupported_configuration_is_not_guessed(conf, target, rootfs, tmp_path, text):
    path = rootfs / "boot/firmware/config.txt"
    path.write_text(text)
    imported, notes = read(rootfs, target, tmp_path)
    assert imported.config.enabled("HARDWARE_ONEWIRE_KEEP")
    assert any("1-Wire" in note and "ambiguous" in note for note in notes)
    configure(conf, "BOARD_RPI5=y", "HARDWARE_ONEWIRE_ENABLE=y")
    with pytest.raises(Error, match="ambiguous"):
        build_plan(Registry.load(), conf.config, target)
    configure(conf, "HARDWARE_ONEWIRE_DISABLE=y")
    with pytest.raises(Error, match="ambiguous"):
        build_plan(Registry.load(), conf.config, target)
    assert path.read_text() == text


@pytest.mark.parametrize("bus,pin", [("I2C", 2), ("I2C", 3), ("SPI", 7), ("SPI", 11)])
def test_conflicting_primary_bus_pins_are_rejected(conf, target, bus, pin):
    configure(conf, f"HARDWARE_{bus}_ENABLE=y", "HARDWARE_ONEWIRE_ENABLE=y",
              f"HARDWARE_ONEWIRE_GPIO={pin}")
    with pytest.raises(Error, match="conflicts"):
        build_plan(Registry.load(), conf.config, target)


def test_existing_bus_conflict_and_explicit_disable(conf, target, rootfs):
    path = rootfs / "boot/firmware/config.txt"
    path.write_text("dtparam=i2c_arm=on\n")
    configure(conf, "HARDWARE_ONEWIRE_ENABLE=y", "HARDWARE_ONEWIRE_GPIO=2")
    with pytest.raises(Error, match="conflicts"):
        build_plan(Registry.load(), conf.config, target)
    configure(conf, "HARDWARE_I2C_DISABLE=y")
    apply(conf, target)
    assert "dtparam=i2c_arm=off" in path.read_text()
    assert "gpiopin=2" in path.read_text()


def test_profile_serial_console_pin_conflict(conf, target):
    configure(conf, "BOARD_RPI5=y", "SERIAL_CONSOLE=y", "RPI5_SERIAL_GPIO=y",
              "HARDWARE_ONEWIRE_ENABLE=y", "HARDWARE_ONEWIRE_GPIO=14")
    with pytest.raises(Error, match="conflicts"):
        build_plan(Registry.load(), conf.config, target)


@pytest.mark.parametrize("pin", [1, 28])
def test_invalid_header_pin_is_reported_before_writes(conf, rootfs, tmp_path, capsys, pin):
    profile = tmp_path / "bad-pin.cfg"
    profile.write_text(f"CONFIG_HARDWARE=y\nCONFIG_HARDWARE_ONEWIRE_ENABLE=y\n"
                       f"CONFIG_HARDWARE_ONEWIRE_GPIO={pin}\n")
    assert main([str(rootfs), "--load", str(profile), "--yes"]) == 1
    assert "HARDWARE_ONEWIRE_GPIO" in capsys.readouterr().err
    assert not (rootfs / "var/backups/board-tune").exists()


@pytest.mark.parametrize("prefix", ["[pi4]\ndtoverlay=custom\n", "[all]\ndtoverlay=custom\n"])
def test_disable_preserves_following_parameter_scope(conf, target, rootfs, prefix):
    path = rootfs / "boot/firmware/config.txt"
    path.write_text(prefix)
    configure(conf, "BOARD_RPI5=y", "HARDWARE_ONEWIRE_ENABLE=y")
    apply(conf, target)
    with path.open("a") as stream:
        stream.write("dtparam=audio=on\n")
    configure(conf, "HARDWARE_ONEWIRE_DISABLE=y")
    apply(conf, target)
    text = path.read_text()
    assert "dtoverlay=w1-gpio" not in text
    assert "dtoverlay=\n[all]\ndtparam=audio=on" in text
    assert "dtparam=audio=on" in text
    assert apply(conf, target).changes() == []


def test_unchecked_dry_run_and_live_commands(conf, rootfs, target, tmp_path, monkeypatch, capsys):
    configure(conf, "HARDWARE_ONEWIRE_ENABLE=y", "HARDWARE=n")
    assert apply(conf, target).changes() == []
    profile = tmp_path / "onewire.cfg"
    profile.write_text("CONFIG_BOARD_RPI5=y\nCONFIG_HARDWARE=y\nCONFIG_HARDWARE_ONEWIRE_ENABLE=y\n")
    original = (rootfs / "boot/firmware/config.txt").read_bytes()
    assert main([str(rootfs), "--load", str(profile), "--dry-run"]) == 0
    assert "dtoverlay=w1-gpio,gpiopin=4" in capsys.readouterr().out
    assert (rootfs / "boot/firmware/config.txt").read_bytes() == original
    model = rootfs / "proc/device-tree/model"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"Raspberry Pi 5 Model B\0")
    configure(conf, "BOARD_RPI5=y", "HARDWARE=y")
    target.live = True
    monkeypatch.setattr(target, "run", lambda *a, **kw: pytest.fail("runtime command"))
    apply(conf, target)
