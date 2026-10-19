"""Current target settings seed interactive configuration, without writes."""

import shutil

import pytest

from board_tune import cli
from board_tune.cli import main
from board_tune.errors import Error
from board_tune.kconfig import Configuration
from board_tune.registry import Registry
from board_tune.target import LocalTarget


@pytest.fixture
def conf(tmp_path, monkeypatch):
    monkeypatch.delenv("BOARD_TUNE_PATH", raising=False)
    work = tmp_path / "config"
    work.mkdir()
    return Configuration(Registry.load(), str(work))


def read(conf, target):
    return conf.read_target(Registry.load(), target)


def test_unmanaged_boot_and_disabled_ssh(conf, target):
    notes = read(conf, target)
    assert any("board model" in note for note in notes)
    assert conf.config.enabled("SERIAL_CONSOLE")
    assert not conf.config.enabled("SERIAL_CONSOLE_PRIMARY")
    assert not conf.config.enabled("SERIAL_CONSOLE_VERBOSE")
    assert conf.config.int("SERIAL_CONSOLE_BAUD") == 115200
    assert conf.config.enabled("SSH")
    assert not conf.config.enabled("SSH_ENABLE")
    assert not conf.config.enabled("NETWORK")
    assert conf.problems() == []


def test_managed_settings_roundtrip(conf, rootfs, target, tmp_path):
    profile = tmp_path / "all.cfg"
    profile.write_text('''CONFIG_BOARD_RPI5=y
CONFIG_SERIAL_CONSOLE=y
CONFIG_SERIAL_CONSOLE_BAUD=921600
CONFIG_SERIAL_CONSOLE_LOGLEVEL=8
CONFIG_SERIAL_CONSOLE_AUTOLOGIN=y
CONFIG_SERIAL_CONSOLE_AUTOLOGIN_USER="pi"
CONFIG_RPI5_SERIAL_GPIO=y
CONFIG_RPI_SERIAL_FIRMWARE_LOG=y
CONFIG_NETWORK=y
CONFIG_NETWORK_STATIC=y
CONFIG_NETWORK_ADDRESS="192.168.1.90/24"
CONFIG_NETWORK_GATEWAY="192.168.1.1"
CONFIG_NETWORK_DNS="1.1.1.1 8.8.8.8"
CONFIG_SSH=y
CONFIG_SSH_PORT=2222
CONFIG_SSH_ROOT_LOGIN_KEY=y
CONFIG_SSH_PASSWORD_AUTH_NO=y
''')
    assert main([str(rootfs), "--load", str(profile), "-y"]) == 0
    read(conf, target)
    cfg = conf.config
    for symbol in ("BOARD_RPI5", "RPI5_SERIAL_GPIO", "RPI_SERIAL_FIRMWARE_LOG",
                   "SERIAL_CONSOLE_PRIMARY", "SERIAL_CONSOLE_VERBOSE",
                   "SERIAL_CONSOLE_AUTOLOGIN", "NETWORK_STATIC", "NETWORK_BACKEND_NM",
                   "SSH_ENABLE", "SSH_ROOT_LOGIN_KEY", "SSH_PASSWORD_AUTH_NO"):
        assert cfg.enabled(symbol), symbol
    assert cfg.int("SERIAL_CONSOLE_BAUD") == 921600
    assert cfg.int("SERIAL_CONSOLE_LOGLEVEL") == 8
    assert cfg.str("SERIAL_CONSOLE_AUTOLOGIN_USER") == "pi"
    assert cfg.str("NETWORK_ADDRESS") == "192.168.1.90/24"
    assert cfg.str("NETWORK_GATEWAY") == "192.168.1.1"
    assert cfg.str("NETWORK_DNS") == "1.1.1.1 8.8.8.8"
    assert cfg.int("SSH_PORT") == 2222
    assert conf.problems() == []


def test_live_model_selects_board(conf, rootfs):
    model = rootfs / "proc/device-tree/model"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"Raspberry Pi 5 Model B Rev 1.0\0")
    target = LocalTarget({"/": str(rootfs)})
    target.live = True
    read(conf, target)
    assert conf.config.enabled("BOARD_RPI5")
    assert conf.config.enabled("RPI5_SERIAL_DEBUG")
    assert conf.problems() == []


def _getty_file(rootfs, path, command):
    file = rootfs / path
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(f"[Service]\nExecStart=\nExecStart={command}\n")
    return file


@pytest.mark.parametrize("option", ["--autologin pi", "--autologin=pi", "-a pi"])
def test_serial_autologin_instance_through_device_alias(conf, rootfs, target, option):
    (rootfs / "dev").mkdir()
    # An absolute link must resolve inside the target, not on the host.
    (rootfs / "dev/serial0").symlink_to("/dev/ttyAMA10")
    _getty_file(rootfs, "usr/lib/systemd/system/serial-getty@.service",
                "-/sbin/agetty --keep-baud 115200 - $TERM")
    _getty_file(rootfs, "etc/systemd/system/serial-getty@ttyAMA10.service.d/autologin.conf",
                f'-/sbin/agetty -o "-p -- \\\\u" {option} --keep-baud 115200 - $TERM')
    before = _contents(rootfs)
    read(conf, target)
    assert conf.config.enabled("SERIAL_CONSOLE_AUTOLOGIN")
    assert conf.config.str("SERIAL_CONSOLE_AUTOLOGIN_USER") == "pi"
    assert _contents(rootfs) == before


@pytest.mark.parametrize("files, expected", [
    # A different console's override must not leak into the selected one.
    ([("etc", "ttyAMA1", "autologin", "--autologin pi")], False),
    # Same filename: instance beats template within the same load path.
    ([("etc", "", "autologin", "--autologin pi"),
      ("etc", "ttyAMA10", "autologin", "")], False),
    ([("etc", "", "autologin", ""),
      ("etc", "ttyAMA10", "autologin", "--autologin pi")], True),
    # Filename order applies across both instance/template and load paths.
    ([("etc", "ttyAMA10", "10-login", "--autologin pi"),
      ("usr/lib", "", "90-login", "")], False),
    ([("etc", "ttyAMA10", "90-login", "--autologin pi"),
      ("usr/lib", "", "10-login", "")], True),
    # Same filename: /etc > /run > vendor, even across instance/template.
    ([("etc", "", "login", ""),
      ("usr/lib", "ttyAMA10", "login", "--autologin pi")], False),
    ([("run", "ttyAMA10", "login", "--autologin pi"),
      ("usr/lib", "ttyAMA10", "login", "")], True),
])
def test_serial_getty_dropin_precedence(conf, rootfs, target, files, expected):
    (rootfs / "boot/firmware/cmdline.txt").write_text("console=ttyAMA10,115200\n")
    _getty_file(rootfs, "usr/lib/systemd/system/serial-getty@.service", "-/sbin/agetty - $TERM")
    for directory, instance, name, options in files:
        _getty_file(rootfs,
                    f"{directory}/systemd/system/serial-getty@{instance}.service.d/{name}.conf",
                    f"-/sbin/agetty {options} - $TERM")
    read(conf, target)
    assert conf.config.enabled("SERIAL_CONSOLE_AUTOLOGIN") is expected
    if expected:
        assert conf.config.str("SERIAL_CONSOLE_AUTOLOGIN_USER") == "pi"


def test_serial_getty_instance_fragment(conf, rootfs, target):
    (rootfs / "boot/firmware/cmdline.txt").write_text("console=ttyS0,115200\n")
    _getty_file(rootfs, "usr/lib/systemd/system/serial-getty@.service", "-/sbin/agetty - $TERM")
    _getty_file(rootfs, "etc/systemd/system/serial-getty@ttyS0.service",
                "-/sbin/agetty --autologin pi - $TERM")
    read(conf, target)
    assert conf.config.enabled("SERIAL_CONSOLE_AUTOLOGIN")
    assert conf.config.str("SERIAL_CONSOLE_AUTOLOGIN_USER") == "pi"


def test_serial_getty_masked_dropin(conf, rootfs, target):
    (rootfs / "boot/firmware/cmdline.txt").write_text("console=ttyAMA10,115200\n")
    _getty_file(rootfs, "usr/lib/systemd/system/serial-getty@ttyAMA10.service.d/login.conf",
                "-/sbin/agetty --autologin pi - $TERM")
    mask = rootfs / "etc/systemd/system/serial-getty@ttyAMA10.service.d/login.conf"
    mask.parent.mkdir(parents=True)
    mask.symlink_to("/dev/null")
    read(conf, target)
    assert not conf.config.enabled("SERIAL_CONSOLE_AUTOLOGIN")


def test_offline_serial_alias_does_not_guess_a_getty_instance(conf, rootfs, target):
    _getty_file(rootfs, "etc/systemd/system/serial-getty@ttyAMA10.service.d/autologin.conf",
                "-/sbin/agetty --autologin pi - $TERM")
    notes = read(conf, target)
    assert not conf.config.enabled("SERIAL_CONSOLE_AUTOLOGIN")
    assert any("instance-specific serial getty settings could not be imported" in n for n in notes)


def test_unchanged_menu_preserves_external_serial_autologin(rootfs, monkeypatch, capsys):
    (rootfs / "boot/firmware/cmdline.txt").write_text("console=ttyAMA10,115200\n")
    _getty_file(rootfs, "etc/systemd/system/serial-getty@ttyAMA10.service.d/autologin.conf",
                "-/sbin/agetty --autologin pi - $TERM")
    before = _contents(rootfs)

    def menu(conf):
        assert conf.config.enabled("SERIAL_CONSOLE_AUTOLOGIN")
        assert conf.config.str("SERIAL_CONSOLE_AUTOLOGIN_USER") == "pi"
        return True

    monkeypatch.setattr(cli, "_require_tty", lambda what: None)
    monkeypatch.setattr(Configuration, "menu", menu)
    assert main([str(rootfs), "-y"]) == 0
    assert "Nothing to change." in capsys.readouterr().out
    assert _contents(rootfs) == before


def test_native_networkmanager_profile(conf, rootfs, target):
    (rootfs / "etc/NetworkManager/system-connections/Wired.nmconnection").write_text('''[connection]
id=Wired connection 1
type=802-3-ethernet
interface-name=enp1s0
[ipv4]
method=manual
address1=10.2.0.8/16,10.2.0.1
dns=9.9.9.9;1.1.1.1;
''')
    read(conf, target)
    assert conf.config.str("NETWORK_IFACE") == "enp1s0"
    assert conf.config.str("NETWORK_ADDRESS") == "10.2.0.8/16"
    assert conf.config.str("NETWORK_GATEWAY") == "10.2.0.1"
    assert conf.config.str("NETWORK_DNS") == "9.9.9.9 1.1.1.1"
    assert conf.problems() == []


def test_native_networkd_profile(conf, rootfs, target):
    shutil.rmtree(rootfs / "etc/NetworkManager")
    directory = rootfs / "etc/systemd/network"
    directory.mkdir()
    (directory / "20-wired.network").write_text('''[Match]
Name=eth0
[Network]
Address=10.0.0.20/24
Gateway=10.0.0.1
DNS=1.1.1.1
DNS=8.8.8.8
''')
    read(conf, target)
    assert conf.config.enabled("NETWORK_BACKEND_NETWORKD")
    assert conf.config.enabled("NETWORK_STATIC")
    assert conf.config.str("NETWORK_ADDRESS") == "10.0.0.20/24"
    assert conf.config.str("NETWORK_DNS") == "1.1.1.1 8.8.8.8"
    assert conf.problems() == []


def test_ssh_include_order_and_match_scope(conf, rootfs, target):
    (rootfs / "etc/ssh/sshd_config").write_text('''Port 2200
Include sshd_config.d/*.conf
PasswordAuthentication yes
Match User guest
  PermitRootLogin yes
''')
    (rootfs / "etc/ssh/sshd_config.d/01-site.conf").write_text('''Port 2222
PermitRootLogin without-password
PasswordAuthentication no
''')
    read(conf, target)
    assert conf.config.int("SSH_PORT") == 2200
    assert conf.config.enabled("SSH_ROOT_LOGIN_KEY")
    assert conf.config.enabled("SSH_PASSWORD_AUTH_NO")
    assert conf.problems() == []


def _contents(rootfs):
    return {str(path.relative_to(rootfs)): path.read_bytes()
            for path in rootfs.rglob("*") if path.is_file() and not path.is_symlink()}


@pytest.mark.parametrize("spec, live", [("/dev/sdb", False), ("pi@192.168.1.50", True)])
def test_unchanged_menu_preserves_files_and_opens_once(rootfs, target, monkeypatch, capsys, spec, live):
    target.live = live
    if live:
        model = rootfs / "proc/device-tree/model"
        model.parent.mkdir(parents=True)
        model.write_bytes(b"Raspberry Pi 5 Model B Rev 1.0\0")
    before = _contents(rootfs)
    opened, closed = [], []

    def open_target(spec):
        opened.append(spec)
        return target

    def menu(conf):
        assert conf.config.enabled("SERIAL_CONSOLE")
        assert not conf.config.enabled("SERIAL_CONSOLE_VERBOSE")
        assert not conf.config.enabled("SSH_ENABLE")
        return True

    monkeypatch.setattr(cli, "_require_tty", lambda what: None)
    monkeypatch.setattr(cli, "open_target", open_target)
    monkeypatch.setattr(target, "close", lambda: closed.append(True))
    monkeypatch.setattr(Configuration, "menu", menu)
    assert main([spec, "-y"]) == 0
    assert "Nothing to change." in capsys.readouterr().out
    assert _contents(rootfs) == before
    assert opened == [spec]
    assert closed == [True]


def test_menu_edits_only_changed_hook(rootfs, monkeypatch):
    boot_before = (rootfs / "boot/firmware/cmdline.txt").read_bytes()

    def menu(conf):
        conf.set("SSH_PORT=2222")
        return True

    monkeypatch.setattr(cli, "_require_tty", lambda what: None)
    monkeypatch.setattr(Configuration, "menu", menu)
    assert main([str(rootfs), "-y"]) == 0
    assert "Port 2222" in (rootfs / "etc/ssh/sshd_config.d/10-board-tune.conf").read_text()
    assert (rootfs / "boot/firmware/cmdline.txt").read_bytes() == boot_before
    assert not (rootfs / "etc/systemd/system/multi-user.target.wants/ssh.service").exists()


def test_profile_then_set_override_current_menu(rootfs, tmp_path, monkeypatch):
    (rootfs / "etc/ssh/sshd_config.d/10-board-tune.conf").write_text("Port 2222\n")
    profile = tmp_path / "override.cfg"
    profile.write_text("CONFIG_SSH_PORT=2200\n")

    def menu(conf):
        assert conf.config.enabled("SERIAL_CONSOLE")
        assert conf.config.int("SSH_PORT") == 2300
        return True

    monkeypatch.setattr(cli, "_require_tty", lambda what: None)
    monkeypatch.setattr(Configuration, "menu", menu)
    assert main([str(rootfs), "--load", str(profile), "--set", "SSH_PORT=2300", "--menu", "-y"]) == 0
    assert "Port 2300" in (rootfs / "etc/ssh/sshd_config.d/10-board-tune.conf").read_text()


def test_discard_closes_target_without_writes(rootfs, target, monkeypatch):
    before = _contents(rootfs)
    closed = []
    monkeypatch.setattr(cli, "_require_tty", lambda what: None)
    monkeypatch.setattr(cli, "open_target", lambda spec: target)
    monkeypatch.setattr(target, "close", lambda: closed.append(True))
    monkeypatch.setattr(Configuration, "menu", lambda conf: False)
    assert main([str(rootfs)]) == 1
    assert closed == [True]
    assert _contents(rootfs) == before


def test_noninteractive_menu_does_not_open_target(rootfs, monkeypatch):
    def unexpected(spec):
        pytest.fail("target opened before terminal check")

    monkeypatch.setattr(cli, "open_target", unexpected)
    assert main([str(rootfs)]) == 1


def test_explicit_offline_board_selects_its_uart_settings(rootfs, monkeypatch):
    with (rootfs / "boot/firmware/config.txt").open("a") as f:
        f.write("\n[pi5]\ndtparam=uart0=on\ndtparam=uart0_console\n[all]\n")

    def menu(conf):
        assert conf.config.enabled("BOARD_RPI5")
        assert conf.config.enabled("RPI5_SERIAL_GPIO")
        assert conf.problems() == []
        return True

    monkeypatch.setattr(cli, "_require_tty", lambda what: None)
    monkeypatch.setattr(Configuration, "menu", menu)
    assert main([str(rootfs), "--set", "BOARD_RPI5=y", "-y"]) == 0


def test_read_failure_closes_target(rootfs, target, monkeypatch):
    closed = []

    def fail(*args):
        raise Error("read failed")

    monkeypatch.setattr(cli, "_require_tty", lambda what: None)
    monkeypatch.setattr(cli, "open_target", lambda spec: target)
    monkeypatch.setattr(target, "close", lambda: closed.append(True))
    monkeypatch.setattr(Configuration, "read_target", fail)
    assert main([str(rootfs)]) == 1
    assert closed == [True]


def test_unbound_dhcp_profile_uses_default_interface(conf, rootfs, target):
    (rootfs / "etc/NetworkManager/system-connections/Wired.nmconnection").write_text(
        "[connection]\nid=Wired connection 1\ntype=ethernet\n[ipv4]\nmethod=auto\n")
    notes = read(conf, target)
    assert conf.config.enabled("NETWORK_DHCP")
    assert conf.config.str("NETWORK_IFACE") == "eth0"
    assert any("no interface binding" in note for note in notes)
    assert conf.problems() == []


def test_networkd_dhcp_readback(conf, rootfs, target):
    shutil.rmtree(rootfs / "etc/NetworkManager")
    directory = rootfs / "etc/systemd/network"
    directory.mkdir()
    (directory / "20-wired.network").write_text("[Match]\nName=eth0\n[Network]\nDHCP=yes\n")
    read(conf, target)
    assert conf.config.enabled("NETWORK_DHCP")
    assert conf.config.enabled("NETWORK_BACKEND_NETWORKD")
    assert conf.problems() == []
