"""Exercise real terminal input and profile persistence through the menu."""

import errno
import fcntl
import os
import pty
import select
import struct
import subprocess
import sys
import termios
import time
from contextlib import contextmanager

import menuconfig
import pytest

from board_tune.menu import kernel_style
from board_tune.registry import Registry


class Terminal:
    def __init__(self, process, fd):
        self.process, self.fd = process, fd
        self.output = b""

    def send(self, text):
        os.write(self.fd, text.encode())

    def enter_hook(self, name):
        index = [hook.name for hook in Registry.load().hooks].index(name) + 1
        self.send("g" + "j" * index + "\n")

    def resize(self, rows, columns):
        fcntl.ioctl(self.fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, columns, 0, 0))
        # The test child does not own the parent process's controlling TTY.
        import signal
        self.process.send_signal(signal.SIGWINCH)

    def wait(self, text=None):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if text is not None and text.encode() in self.output:
                return
            if select.select([self.fd], [], [], 0.1)[0]:
                try:
                    data = os.read(self.fd, 65536)
                except OSError as error:
                    if error.errno != errno.EIO:
                        raise
                    data = b""
                self.output += data
                if not data:
                    break
            if self.process.poll() is not None:
                break
        if text is None:
            assert self.process.wait(timeout=2) in (0, 1), self.output.decode(errors="replace")
            assert b"Traceback" not in self.output
        else:
            assert text.encode() in self.output, self.output.decode(errors="replace")


@contextmanager
def terminal(tmp_path, profile="", target=None):
    source = tmp_path / "input.cfg"
    source.write_text(profile)
    destination = tmp_path / "output.cfg"
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 100, 0, 0))
    env = dict(os.environ, TERM="xterm", BOARD_TUNE_PATH="", MENUCONFIG_STYLE="")
    process = subprocess.Popen(
        [sys.executable, "-m", "board_tune", *([str(target)] if target else []),
         "--load", str(source), "--menu", "--save", str(destination)],
        stdin=slave, stdout=slave, stderr=slave, env=env)
    os.close(slave)
    session = Terminal(process, master)
    try:
        session.wait("< Load >")
        yield session, destination
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        os.close(master)


def test_hardware_choices_can_be_edited_and_saved(tmp_path):
    with terminal(tmp_path, "CONFIG_HARDWARE=y\n") as (session, destination):
        session.enter_hook("hardware")
        session.wait("I2C (GPIO 2/3)")
        session.send("j\n")
        session.wait("Enable")
        session.send("j\n")
        session.send("q")
        session.wait("Do you wish to save your new configuration?")
        session.send("y")
        session.wait()
        assert session.process.returncode == 0
        assert "CONFIG_HARDWARE_I2C_ENABLE=y" in destination.read_text()


def test_wifi_browser_keeps_offline_profiles_unchanged(tmp_path, rootfs):
    file = rootfs / "etc/NetworkManager/system-connections/Lab.nmconnection"
    original = "[connection]\nid=Lab\nuuid=abc\ntype=wifi\n[wifi]\nssid=Lab\n[ipv4]\nmethod=auto\n"
    file.write_text(original)
    with terminal(tmp_path, target=rootfs) as (session, destination):
        session.enter_hook("wifi")
        session.wait("Offline target")
        session.send("\n")
        session.wait("Saved Wi-Fi networks")
        session.wait("Lab")
        session.send("\n")
        session.wait("UUID: abc")
        session.send("\x1b")
        session.send("\x1b")
        session.send("\x1b")
        session.send("q")
        session.wait()
    assert file.read_text() == original


def test_onewire_gpio_can_be_edited_and_saved(tmp_path):
    with terminal(tmp_path, "CONFIG_HARDWARE=y\n") as (session, destination):
        session.enter_hook("hardware")
        session.wait("1-Wire")
        session.send("jjj\n")
        session.wait("Disable managed overlay")
        session.send("j\n")
        session.wait("1-Wire GPIO (BCM)")
        session.send("j\n")
        session.wait("1-Wire GPIO (BCM) (int)")
        session.send("\x1517\n")
        session.send("q")
        session.wait("Do you wish to save your new configuration?")
        session.send("y")
        session.wait()
        assert session.process.returncode == 0
        text = destination.read_text()
        assert "CONFIG_HARDWARE_ONEWIRE_ENABLE=y" in text
        assert "CONFIG_HARDWARE_ONEWIRE_GPIO=17" in text


def test_additional_uart_and_flow_control_can_be_saved(tmp_path):
    with terminal(tmp_path, "CONFIG_BOARD_RPI5=y\nCONFIG_HARDWARE=y\n") as (session, destination):
        session.enter_hook("hardware")
        session.wait("Additional UARTs")
        session.send("jjjj\n")
        session.wait("UART0 (GPIO 14/15)")
        session.send("jj\n")
        session.wait("Disable managed overlay")
        session.send("j\n")
        session.wait("UART2 CTS/RTS")
        session.send("j q")
        session.wait("Do you wish to save your new configuration?")
        session.send("y")
        session.wait()
        text = destination.read_text()
        assert "CONFIG_HARDWARE_UART2_ENABLE=y" in text
        assert "CONFIG_HARDWARE_UART2_CTSRTS=y" in text


def test_terminal_edit_save_and_resize(tmp_path):
    with terminal(tmp_path, "CONFIG_SSH=y\nCONFIG_SSH_PORT=2200\n") as (session, destination):
        session.enter_hook("ssh")
        session.wait("Start at boot")
        session.send("jj\n")
        session.wait("Port (int)")
        session.send("\x152222\n")
        session.resize(24, 80)
        session.send("q")
        session.wait("Do you wish to save your new configuration?")
        session.send("y")
        session.wait()
        assert session.process.returncode == 0
        assert "CONFIG_SSH_PORT=2222" in destination.read_text()


def test_terminal_discard_does_not_export(tmp_path):
    with terminal(tmp_path) as (session, destination):
        session.enter_hook("network")
        session.wait("Configure Ethernet")
        session.send(" q")
        session.wait("Do you wish to save your new configuration?")
        session.send("n")
        session.wait()
        assert session.process.returncode == 1
        assert not destination.exists()


@pytest.mark.parametrize('key', ['\t', '\x1bOC', '\x1bOD', '\x1b[Z'])
def test_exit_dialog_buttons_and_resize(tmp_path, key):
    with terminal(tmp_path) as (session, destination):
        session.enter_hook('network')
        session.send(' q')
        session.wait('Do you wish to save your new configuration?')
        session.wait('< Yes >')
        session.wait('< No >')
        session.resize(8, 30)
        session.send('?')  # Wake a read if SIGWINCH arrived before get_wch().
        session.wait('Resize terminal')
        session.output = b''
        session.resize(24, 80)
        session.send('?')
        session.wait('< Yes >')
        session.send(key + '\n')  # No, selected with arrows/Tab/Shift-Tab
        session.wait()
        assert session.process.returncode == 1
        assert not destination.exists()


@pytest.mark.parametrize('cancel', ['\x1b', 'c'])
def test_exit_cancel_returns_to_edits_and_enter_defaults_to_yes(tmp_path, cancel):
    with terminal(tmp_path) as (session, destination):
        session.enter_hook('network')
        session.send(' q')
        session.wait('Do you wish to save your new configuration?')
        session.output = b''
        session.send('\t' + cancel)  # Leave No selected, then cancel the exit.
        session.wait('Configure Ethernet')
        session.send('q\n')  # A new dialog defaults to Yes.
        session.wait()
        assert session.process.returncode == 0
        assert 'CONFIG_NETWORK=y' in destination.read_text()


def test_exit_no_after_earlier_save_does_not_export(tmp_path):
    with terminal(tmp_path, 'CONFIG_SSH=y\nCONFIG_SSH_PORT=2200\n') as (session, destination):
        session.enter_hook('ssh')
        session.send('jj\n\x152222\ns')
        session.wait('Settings saved for this session')
        session.send('\n\n\x153333\nq')
        session.wait('Do you wish to save your new configuration?')
        session.send('n')
        session.wait()
        assert session.process.returncode == 1
        assert not destination.exists()


def test_terminal_load_keeps_input_profile_and_saves_session(tmp_path):
    imported = tmp_path / "import.cfg"
    profile = "CONFIG_BOARD_RPI5=y\nCONFIG_SSH=y\nCONFIG_SSH_PORT=2222\n"
    imported.write_text(profile)
    with terminal(tmp_path) as (session, destination):
        session.send("o")
        session.wait("Load profile")
        session.send("\x15" + str(imported) + "\n")
        session.send("s")
        session.wait("Settings saved for this session")
        session.send("\nq")
        session.wait()
        assert session.process.returncode == 0
        assert "CONFIG_BOARD_RPI5=y" in destination.read_text()
        assert "CONFIG_SSH_PORT=2222" in destination.read_text()
        assert imported.read_text() == profile


def test_adapter_restores_hooks_and_style_on_failure(monkeypatch):
    draw = menuconfig._draw_main
    monkeypatch.setenv("MENUCONFIG_STYLE", "monochrome")
    with pytest.raises(RuntimeError), kernel_style(menuconfig):
        assert menuconfig._draw_main is not draw
        raise RuntimeError("test interruption")
    assert menuconfig._draw_main is draw
    assert os.environ["MENUCONFIG_STYLE"] == "monochrome"


def test_terminal_input_cancel_and_small_window(tmp_path):
    with terminal(tmp_path, "CONFIG_SSH=y\nCONFIG_SSH_PORT=2200\n") as (session, destination):
        session.enter_hook("ssh")
        session.send("jj\n")
        session.wait("Port (int)")
        # Tab selects OK, then Cancel. Enter must leave the original value.
        session.send("\x152222\t\t\n")
        session.resize(8, 30)
        session.wait("Resize terminal")
        session.resize(24, 80)
        session.send("q")
        session.wait()
        assert session.process.returncode == 0
        assert "CONFIG_SSH_PORT=2200" in destination.read_text()


def test_forget_wifi_draft_can_be_discarded(tmp_path, rootfs):
    file = rootfs / 'etc/NetworkManager/system-connections/Lab.nmconnection'
    original = '[connection]\nid=Lab\nuuid=lab\ntype=wifi\n[wifi]\nssid=Lab\n[ipv4]\nmethod=auto\n'
    file.write_text(original)
    with terminal(tmp_path, target=rootfs) as (session, destination):
        session.enter_hook('wifi')
        session.wait('Offline target')
        session.send('\n\njjj\n')
        session.wait('Remove saved profile Lab?')
        session.send('j\n\x1b\x1bq')
        session.wait('Do you wish to save your new configuration?')
        session.send('n')
        session.wait()
        assert session.process.returncode == 1
    assert file.read_text() == original


def test_wifi_password_form_masks_input_and_cancel_creates_nothing(tmp_path, rootfs):
    secret = 'NeverPrintThisPassword'
    with terminal(tmp_path, target=rootfs) as (session, destination):
        session.enter_hook('wifi')
        session.wait('Offline target')
        session.send('jj\n')
        session.wait('Edit Wi-Fi profile')
        session.send('\nLabSSID\njjjj\n')
        session.wait('Ctrl-U: clear')
        session.send(secret + '\n')
        session.send('q\x1bq')
        session.wait()
        assert secret.encode() not in session.output
    assert not list((rootfs / 'etc/NetworkManager/system-connections').glob('*.nmconnection'))


def test_forget_wifi_draft_applies_after_review_confirmation(tmp_path, rootfs):
    file = rootfs / 'etc/NetworkManager/system-connections/Lab.nmconnection'
    file.write_text('[connection]\nid=Lab\nuuid=lab\ntype=wifi\n[wifi]\nssid=Lab\n[ipv4]\nmethod=auto\n')
    with terminal(tmp_path, target=rootfs) as (session, destination):
        session.enter_hook('wifi')
        session.wait('Offline target')
        session.send('\n\njjj\n')
        session.wait('Remove saved profile Lab?')
        session.send('j\n\x1b\x1bq')
        session.wait('Do you wish to save your new configuration?')
        session.send('y')
        session.wait('Apply 1 change(s)?')
        assert file.exists()
        session.send('y\n')
        session.wait()
        assert session.process.returncode == 0
    assert not file.exists()
    assert list((rootfs / 'var/backups/board-tune').glob('*.json'))


def test_wifi_power_saving_edit_uses_normal_review_apply(tmp_path, rootfs):
    file = rootfs / 'etc/NetworkManager/system-connections/Lab.nmconnection'
    original = '[connection]\nid=Lab\nuuid=lab\ntype=wifi\n[wifi]\nssid=Lab\n[ipv4]\nmethod=auto\n'
    file.write_text(original)
    with terminal(tmp_path, target=rootfs) as (session, destination):
        session.enter_hook('wifi')
        session.wait('Offline target')
        session.send('\n\njj\n')
        session.wait('Edit Wi-Fi profile')
        session.send('j' * 12 + '\n')
        session.wait('Leave driver state unchanged')
        session.send('j\nj\n')  # Disabled, then Save only
        session.send('\x1b\x1bq')
        session.wait('Do you wish to save your new configuration?')
        session.send('y')
        session.wait('Apply 1 change(s)?')
        assert file.read_text() == original
        session.send('y\n')
        session.wait()
        assert session.process.returncode == 0
    assert 'powersave=2\n' in file.read_text()
    assert 'uuid=lab\n' in file.read_text()
