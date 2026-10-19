"""Additional UART boot setup, readback, console guards and GPIO conflicts."""

import pytest

from board_tune.cli import build_plan, main
from board_tune.errors import Error
from board_tune.kconfig import Configuration
from board_tune.registry import Registry


@pytest.fixture
def conf(tmp_path, rootfs):
    overlays = rootfs / "boot/firmware/overlays"
    overlays.mkdir(parents=True)
    for name in ("uart2", "uart3", "uart4", "uart5", "uart0-pi5", "uart1-pi5",
                 "uart2-pi5", "uart3-pi5", "uart4-pi5", "w1-gpio"):
        (overlays / f"{name}.dtbo").write_bytes(b"installed test overlay")
    # Extra UARTs must also work on installations without a serial console.
    (rootfs / "boot/firmware/cmdline.txt").write_text("root=PARTUUID=test rootwait\n")
    return Configuration(Registry.load(), str(tmp_path))


def configure(conf, *values):
    for value in ("HARDWARE=y", *values):
        conf.set(value)


def apply(conf, target):
    plan = build_plan(Registry.load(), conf.config, target)
    assert not plan.commands and not plan.pre_commands
    plan.apply(plan.changes())
    return plan


def read(target, tmp_path):
    work = tmp_path / "read"
    work.mkdir(exist_ok=True)
    conf = Configuration(Registry.load(), str(work))
    notes = conf.read_target(Registry.load(), target)
    assert conf.problems() == []
    return conf, notes


@pytest.mark.parametrize("board,port", [("RPI4", p) for p in range(2, 6)]
                         + [("RPI5", p) for p in range(5)])
@pytest.mark.parametrize("flow", [False, True])
def test_each_uart_enable_flow_disable_roundtrip(conf, rootfs, target, tmp_path, board, port, flow):
    configure(conf, f"BOARD_{board}=y", f"HARDWARE_UART{port}_ENABLE=y",
              f"HARDWARE_UART{port}_CTSRTS={'y' if flow else 'n'}")
    path = rootfs / "boot/firmware/config.txt"
    original = path.read_text()
    apply(conf, target)
    name = f"uart{port}" + ("-pi5" if board == "RPI5" else "")
    assert path.read_text().startswith(original)
    directive = f"dtoverlay={name}" + (",ctsrts" if flow else "")
    assert f"[{board.replace('RPI', 'pi')}]\n{directive}\ndtoverlay=" in path.read_text()
    assert apply(conf, target).changes() == []
    imported, _ = read(target, tmp_path)
    assert imported.config.enabled(f"BOARD_{board}")
    assert imported.config.enabled(f"HARDWARE_UART{port}_ENABLE")
    assert imported.config.enabled(f"HARDWARE_UART{port}_CTSRTS") == flow
    configure(conf, f"HARDWARE_UART{port}_DISABLE=y")
    apply(conf, target)
    assert f"dtoverlay={name}," not in path.read_text()
    assert f"dtoverlay={name}\n" not in path.read_text()
    assert "dtoverlay=vc4-kms-v3d" in path.read_text()
    assert apply(conf, target).changes() == []
    imported, _ = read(target, tmp_path)
    assert imported.config.enabled(f"HARDWARE_UART{port}_DISABLE")


def test_keep_unchecked_and_disable_without_managed_overlay(conf, target):
    configure(conf)
    assert apply(conf, target).changes() == []
    configure(conf, "HARDWARE_UART3_DISABLE=y")
    assert apply(conf, target).changes() == []
    configure(conf, "HARDWARE_UART3_ENABLE=y", "HARDWARE=n")
    assert apply(conf, target).changes() == []


def test_multiple_uarts_and_independent_changes(conf, target, rootfs):
    configure(conf, "BOARD_RPI5=y", "HARDWARE_UART0_ENABLE=y", "HARDWARE_UART2_ENABLE=y",
              "HARDWARE_UART4_ENABLE=y")
    apply(conf, target)
    configure(conf, "HARDWARE_UART0_KEEP=y", "HARDWARE_UART2_CTSRTS=y", "HARDWARE_UART4_DISABLE=y")
    apply(conf, target)
    text = (rootfs / "boot/firmware/config.txt").read_text()
    assert "dtoverlay=uart0-pi5\n" in text
    assert "dtoverlay=uart2-pi5,ctsrts\n" in text
    assert "dtoverlay=uart4-pi5" not in text
    assert apply(conf, target).changes() == []


def test_external_overlay_reused_but_not_rewritten(conf, target, rootfs, tmp_path):
    path = rootfs / "boot/firmware/config.txt"
    text = "[pi5]\ndtoverlay=uart2-pi5:ctsrts=off\ndtparam=ctsrts\ndtoverlay=\n[all]\n"
    path.write_text(text)
    configure(conf, "BOARD_RPI5=y", "HARDWARE_UART2_ENABLE=y", "HARDWARE_UART2_CTSRTS=y")
    assert apply(conf, target).changes() == []
    # Explicit hint for unmanaged offline images, independent of default board.
    conf.read_target(Registry.load(), target, Registry.load().platforms[0].boards[1])
    assert conf.config.enabled("HARDWARE_UART2_ENABLE")
    assert conf.config.enabled("HARDWARE_UART2_CTSRTS")
    configure(conf, "HARDWARE_UART2_CTSRTS=n")
    with pytest.raises(Error, match="external overlay"):
        build_plan(Registry.load(), conf.config, target)
    configure(conf, "HARDWARE_UART2_DISABLE=y")
    with pytest.raises(Error, match="external overlay"):
        build_plan(Registry.load(), conf.config, target)
    assert path.read_text() == text


def test_target_mapping_allows_legacy_uart0_and_base_control_to_stay(conf, rootfs, target):
    from .test_overlaymap import make_map

    path = rootfs / "boot/firmware/config.txt"
    original = "dtoverlay=uart0\ndtparam=uart0=on\ndtoverlay=custom-camera,cam0\n"
    path.write_text(original)
    (rootfs / "boot/firmware/overlays/overlay_map.dtb").write_bytes(make_map())
    configure(conf, "BOARD_RPI5=y", "HARDWARE_UART2_ENABLE=y", "HARDWARE_UART2_CTSRTS=y")
    apply(conf, target)
    assert path.read_text().startswith(original)
    assert "dtoverlay=uart2-pi5,ctsrts\n" in path.read_text()
    assert apply(conf, target).changes() == []


def test_verified_external_alias_is_imported_and_reused(conf, rootfs, target):
    from .test_overlaymap import make_map

    path = rootfs / "boot/firmware/config.txt"
    original = "dtoverlay=uart2,ctsrts\n"
    path.write_text(original)
    (rootfs / "boot/firmware/overlays/overlay_map.dtb").write_bytes(
        make_map((("uart2", "bcm2712", b"uart2-pi5\0"),)))
    registry = Registry.load()
    conf.read_target(registry, target, registry.platforms[0].boards[1])
    assert conf.config.enabled("HARDWARE_UART2_ENABLE")
    assert conf.config.enabled("HARDWARE_UART2_CTSRTS")
    assert build_plan(registry, conf.config, target, {"ssh", "serial_console"}).changes() == []
    assert path.read_text() == original


def test_pi5_ctsrts_false_value_is_still_present_and_migrates(conf, target, rootfs):
    path = rootfs / "boot/firmware/config.txt"
    path.write_text("# >>> board-tune: hardware_uart4 >>>\n[all]\n[pi5]\n"
                    "dtoverlay=uart4-pi5,ctsrts=off\ndtoverlay=\n[all]\n"
                    "# <<< board-tune: hardware_uart4 <<<\n")
    registry = Registry.load()
    conf.read_target(registry, target)
    assert conf.config.enabled("HARDWARE_UART4_CTSRTS")
    configure(conf, "HARDWARE_UART4_CTSRTS=n")
    apply(conf, target)
    assert "dtoverlay=uart4-pi5\n" in path.read_text()
    assert "ctsrts" not in path.read_text()
    assert apply(conf, target).changes() == []


@pytest.mark.parametrize("text", [
    "include custom.txt\n",
    "[gpio4=1]\ndtoverlay=uart2-pi5\n",
    "dtoverlay=uart2-pi5,ctsrts=bad\n",
    "dtoverlay=uart2-pi5,rs485\n",
    "dtoverlay=uart2-pi5\ndtoverlay=uart2-pi5\n",
    "dtoverlay=uart2\n",  # Generic Pi 5 name needs target overlay_map evaluation.
    "dtparam=uart2=on\n",
    "dtoverlay=uart2-pi5\n[gpio4=1]\ndtoverlay=custom\n[all]\ndtparam=ctsrts\n",
])
def test_ambiguous_readback_and_changes_refused(conf, rootfs, target, text):
    path = rootfs / "boot/firmware/config.txt"
    path.write_text(text)
    registry = Registry.load()
    notes = conf.read_target(registry, target, registry.platforms[0].boards[1])
    assert conf.config.enabled("HARDWARE_UART2_KEEP")
    assert any("UART2" in note and "Keep" in note for note in notes)
    configure(conf, "HARDWARE_UART2_ENABLE=y")
    with pytest.raises(Error, match="includes|ambiguous|multiple|base-dtb"):
        build_plan(registry, conf.config, target)
    assert path.read_text() == text


@pytest.mark.parametrize("values", [
    ("BOARD_RPI5=y", "HARDWARE_I2C_ENABLE=y", "HARDWARE_UART1_ENABLE=y", "HARDWARE_UART1_CTSRTS=y"),
    ("BOARD_RPI4=y", "HARDWARE_I2C_ENABLE=y", "HARDWARE_UART2_ENABLE=y", "HARDWARE_UART2_CTSRTS=y"),
    ("BOARD_RPI5=y", "HARDWARE_SPI_ENABLE=y", "HARDWARE_UART3_ENABLE=y"),
    ("BOARD_RPI4=y", "HARDWARE_SPI_ENABLE=y", "HARDWARE_UART4_ENABLE=y"),
    ("BOARD_RPI5=y", "HARDWARE_ONEWIRE_ENABLE=y", "HARDWARE_UART2_ENABLE=y"),
    ("BOARD_RPI5=y", "HARDWARE_UART0_ENABLE=y", "HARDWARE_UART4_ENABLE=y", "HARDWARE_UART4_CTSRTS=y"),
])
def test_gpio_conflicts(conf, target, values):
    configure(conf, *values)
    with pytest.raises(Error, match="conflicts"):
        build_plan(Registry.load(), conf.config, target)


def test_kept_uart_conflicts_with_new_bus_setting(conf, target):
    configure(conf, "BOARD_RPI5=y", "HARDWARE_UART3_ENABLE=y")
    apply(conf, target)
    configure(conf, "HARDWARE_UART3_KEEP=y", "HARDWARE_SPI_ENABLE=y")
    with pytest.raises(Error, match="SPI0"):
        build_plan(Registry.load(), conf.config, target)


def test_primary_console_protected_for_enable_and_disable(conf, target, rootfs):
    configure(conf, "BOARD_RPI5=y", "HARDWARE_UART0_ENABLE=y")
    apply(conf, target)
    cmdline = rootfs / "boot/firmware/cmdline.txt"
    cmdline.write_text("console=ttyAMA0,115200 root=PARTUUID=test\n")
    assert apply(conf, target).changes() == []
    configure(conf, "HARDWARE_UART0_DISABLE=y")
    with pytest.raises(Error, match="console/getty"):
        build_plan(Registry.load(), conf.config, target)
    configure(conf, "HARDWARE_UART0_ENABLE=y", "HARDWARE_UART0_CTSRTS=y")
    with pytest.raises(Error, match="console/getty"):
        build_plan(Registry.load(), conf.config, target)


@pytest.mark.parametrize("console", ["ttyAMA3", "serial2", "ttyS1"])
def test_unresolved_console_mapping_is_not_guessed(conf, target, rootfs, console):
    (rootfs / "boot/firmware/cmdline.txt").write_text(f"console={console},115200\n")
    configure(conf, "BOARD_RPI5=y", "HARDWARE_UART2_ENABLE=y")
    with pytest.raises(Error, match="cannot resolve"):
        build_plan(Registry.load(), conf.config, target)


@pytest.mark.parametrize("directory", ["getty.target.wants", "multi-user.target.wants"])
def test_enabled_getty_protected(conf, rootfs, target, directory):
    wants = rootfs / "etc/systemd/system" / directory
    wants.mkdir(exist_ok=True)
    (wants / "serial-getty@ttyAMA0.service").symlink_to("/lib/systemd/system/serial-getty@.service")
    configure(conf, "BOARD_RPI5=y", "HARDWARE_UART0_ENABLE=y")
    with pytest.raises(Error, match="console/getty"):
        build_plan(Registry.load(), conf.config, target)


def test_serial0_getty_without_kernel_console_preserves_header_alias(conf, target, rootfs):
    with (rootfs / "boot/firmware/config.txt").open("a") as stream:
        stream.write("dtparam=uart0_console\n")
    wants = rootfs / "etc/systemd/system/getty.target.wants"
    wants.mkdir()
    (wants / "serial-getty@serial0.service").symlink_to("/lib/systemd/system/serial-getty@.service")
    configure(conf, "BOARD_RPI5=y", "HARDWARE_UART0_ENABLE=y")
    with pytest.raises(Error, match="console/getty"):
        build_plan(Registry.load(), conf.config, target)


def test_gpio_console_allows_an_unrelated_uart(conf, target, rootfs):
    (rootfs / "boot/firmware/cmdline.txt").write_text("console=serial0,115200 root=PARTUUID=test\n")
    configure(conf, "BOARD_RPI5=y", "SERIAL_CONSOLE=y", "RPI5_SERIAL_GPIO=y", "HARDWARE_UART2_ENABLE=y")
    apply(conf, target)
    assert "dtoverlay=uart2-pi5" in (rootfs / "boot/firmware/config.txt").read_text()


def test_missing_overlay_and_old_boot_path(conf, target, rootfs):
    (rootfs / "boot/firmware/overlays/uart2-pi5.dtbo").unlink()
    configure(conf, "BOARD_RPI5=y", "HARDWARE_UART2_ENABLE=y")
    with pytest.raises(Error, match="not installed"):
        build_plan(Registry.load(), conf.config, target)
    (rootfs / "boot/firmware/overlays/uart2-pi5.dtbo").write_bytes(b"installed")
    (rootfs / "boot/firmware").rename(rootfs / "old-boot")
    (rootfs / "boot").rmdir()
    (rootfs / "old-boot").rename(rootfs / "boot")
    apply(conf, target)
    assert "dtoverlay=uart2-pi5" in (rootfs / "boot/config.txt").read_text()


def test_model_dependency_and_dry_run(conf, rootfs, tmp_path, capsys):
    profile = tmp_path / "uart.cfg"
    profile.write_text("CONFIG_BOARD_RPI5=y\nCONFIG_HARDWARE=y\nCONFIG_HARDWARE_UART5_ENABLE=y\n")
    assert main([str(rootfs), "--load", str(profile), "--yes"]) == 1
    assert "HARDWARE_UART5_ENABLE" in capsys.readouterr().err
    profile.write_text("CONFIG_BOARD_RPI5=y\nCONFIG_HARDWARE=y\nCONFIG_HARDWARE_UART2_ENABLE=y\n")
    original = (rootfs / "boot/firmware/config.txt").read_bytes()
    assert main([str(rootfs), "--load", str(profile), "--dry-run"]) == 0
    assert "dtoverlay=uart2-pi5" in capsys.readouterr().out
    assert (rootfs / "boot/firmware/config.txt").read_bytes() == original
    assert not (rootfs / "var/backups/board-tune").exists()


def test_live_plan_has_no_runtime_commands(conf, rootfs, target, monkeypatch):
    model = rootfs / "proc/device-tree/model"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"Raspberry Pi 5 Model B\0")
    configure(conf, "BOARD_RPI5=y", "HARDWARE_UART2_ENABLE=y")
    target.live = True
    monkeypatch.setattr(target, "run", lambda *a, **kw: pytest.fail("runtime UART command"))
    apply(conf, target)
