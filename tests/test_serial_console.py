import textwrap

import pytest

from board_tune.cli import main

DROPIN = "etc/systemd/system/serial-getty@.service.d/autologin.conf"


@pytest.fixture
def apply(rootfs, tmp_path, capsys):
    def apply(config):
        path = tmp_path / "profile.cfg"
        path.write_text(textwrap.dedent(config))
        rc = main([str(rootfs), "--load", str(path), "-y"])
        out, err = capsys.readouterr()
        assert rc == 0, err
        return out
    return apply


def cmdline(rootfs):
    return (rootfs / "boot/firmware/cmdline.txt").read_text()


def config_txt(rootfs):
    return (rootfs / "boot/firmware/config.txt").read_text()


def test_defaults_rpi5(rootfs, apply):
    out = apply("""\
        CONFIG_BOARD_RPI5=y
        CONFIG_SERIAL_CONSOLE=y
    """)
    assert cmdline(rootfs) == (
        "console=tty1 console=serial0,115200 root=PARTUUID=4e639091-02 "
        "rootfstype=ext4 fsck.repair=yes rootwait "
        "plymouth.ignore-serial-consoles loglevel=7\n")
    assert "[pi5]\nenable_uart=1\n[all]\n" in config_txt(rootfs)
    assert not (rootfs / DROPIN).exists()
    assert "take effect after a reboot" in out

    assert "Nothing to change." in apply("""\
        CONFIG_BOARD_RPI5=y
        CONFIG_SERIAL_CONSOLE=y
    """)


def test_secondary_console_keeps_quiet_boot(rootfs, apply):
    apply("""\
        CONFIG_BOARD_RPI5=y
        CONFIG_SERIAL_CONSOLE=y
        CONFIG_SERIAL_CONSOLE_BAUD=921600
        # CONFIG_SERIAL_CONSOLE_PRIMARY is not set
        # CONFIG_SERIAL_CONSOLE_VERBOSE is not set
    """)
    assert cmdline(rootfs).startswith("console=serial0,921600 console=tty1 root=")
    assert " quiet splash " in cmdline(rootfs)


def test_rpi4_pl011_disables_bluetooth(rootfs, apply):
    wants = rootfs / "etc/systemd/system/multi-user.target.wants/hciuart.service"
    wants.symlink_to("/lib/systemd/system/hciuart.service")
    apply("""\
        CONFIG_BOARD_RPI4=y
        CONFIG_SERIAL_CONSOLE=y
        CONFIG_RPI4_SERIAL_PL011=y
        CONFIG_RPI_SERIAL_FIRMWARE_LOG=y
    """)
    assert ("[pi4]\nenable_uart=1\ndtoverlay=disable-bt\nuart_2ndstage=1\n[all]\n"
            in config_txt(rootfs))
    assert not wants.is_symlink()


def test_rpi5_gpio_uart(rootfs, apply):
    apply("""\
        CONFIG_BOARD_RPI5=y
        CONFIG_SERIAL_CONSOLE=y
        CONFIG_RPI5_SERIAL_GPIO=y
    """)
    assert ("[pi5]\nenable_uart=1\ndtparam=uart0=on\ndtparam=uart0_console\n[all]\n"
            in config_txt(rootfs))


def test_switching_uart_rewrites_block(rootfs, apply):
    base = "CONFIG_BOARD_RPI4=y\nCONFIG_SERIAL_CONSOLE=y\n"
    apply(base + "CONFIG_RPI4_SERIAL_PL011=y\n")
    apply(base)
    text = config_txt(rootfs)
    assert "disable-bt" not in text
    assert text.count("board-tune: serial_console >>>") == 1


def test_autologin(rootfs, apply):
    apply("""\
        CONFIG_BOARD_RPI5=y
        CONFIG_SERIAL_CONSOLE=y
        CONFIG_SERIAL_CONSOLE_AUTOLOGIN=y
        CONFIG_SERIAL_CONSOLE_AUTOLOGIN_USER="pi"
    """)
    assert ("ExecStart=-/sbin/agetty --autologin pi "
            "--keep-baud 115200,57600,38400,9600 - $TERM\n") in (rootfs / DROPIN).read_text()

    apply("""\
        CONFIG_BOARD_RPI5=y
        CONFIG_SERIAL_CONSOLE=y
    """)
    assert not (rootfs / DROPIN).exists()


def test_autologin_for_missing_user_warns(rootfs, apply):
    out = apply("""\
        CONFIG_BOARD_RPI5=y
        CONFIG_SERIAL_CONSOLE=y
        CONFIG_SERIAL_CONSOLE_AUTOLOGIN=y
        CONFIG_SERIAL_CONSOLE_AUTOLOGIN_USER="alice"
    """)
    assert "user alice does not exist yet" in out


def test_board_specific_option_needs_its_board(rootfs, tmp_path, capsys):
    path = tmp_path / "bad.cfg"
    path.write_text("CONFIG_BOARD_RPI5=y\nCONFIG_SERIAL_CONSOLE=y\nCONFIG_RPI4_SERIAL_PL011=y\n")
    assert main([str(rootfs), "--load", str(path), "-y"]) == 1
    assert "RPI4_SERIAL_PL011=y is not possible" in capsys.readouterr().err
