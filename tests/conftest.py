"""Shared fixtures: a minimal Raspberry Pi OS root filesystem."""

from __future__ import annotations

from pathlib import Path

import pytest

from board_tune.target import LocalTarget

CMDLINE = ("console=serial0,115200 console=tty1 root=PARTUUID=4e639091-02 "
           "rootfstype=ext4 fsck.repair=yes rootwait quiet splash "
           "plymouth.ignore-serial-consoles\n")

CONFIG_TXT = """\
# For more options and information see
# http://rptl.io/configtxt
dtparam=audio=on
camera_auto_detect=1
display_auto_detect=1
auto_initramfs=1
dtoverlay=vc4-kms-v3d
max_framebuffers=2
disable_fw_kms_setup=1
arm_64bit=1
disable_overscan=1
arm_boost=1

[cm4]
otg_mode=1

[all]
"""

SSH_SERVICE = """\
[Unit]
Description=OpenBSD Secure Shell server

[Service]
ExecStart=/usr/sbin/sshd -D $SSHD_OPTS

[Install]
WantedBy=multi-user.target
Alias=sshd.service
"""

HCIUART_SERVICE = """\
[Unit]
Description=Configure Bluetooth Modems connected by UART

[Service]
ExecStart=/usr/bin/btuart

[Install]
WantedBy=multi-user.target
"""

NETWORKD_SERVICE = """\
[Unit]
Description=Network Configuration

[Service]
ExecStart=/lib/systemd/systemd-networkd

[Install]
WantedBy=multi-user.target
Alias=dbus-org.freedesktop.network1.service
"""

FILES = {
    "boot/firmware/config.txt": CONFIG_TXT,
    "boot/firmware/cmdline.txt": CMDLINE,
    "usr/lib/os-release": 'PRETTY_NAME="Debian GNU/Linux 12 (bookworm)"\nID=debian\n',
    "etc/passwd": ("root:x:0:0:root:/root:/bin/bash\n"
                   "pi:x:1000:1000:,,,:/home/pi:/bin/bash\n"),
    "etc/ssh/sshd_config": ("Include /etc/ssh/sshd_config.d/*.conf\n\n"
                            "KbdInteractiveAuthentication no\nUsePAM yes\n"),
    "etc/NetworkManager/NetworkManager.conf": "[main]\nplugins=ifupdown,keyfile\n",
    "usr/lib/systemd/system/ssh.service": SSH_SERVICE,
    "usr/lib/systemd/system/hciuart.service": HCIUART_SERVICE,
    "usr/lib/systemd/system/systemd-networkd.service": NETWORKD_SERVICE,
}

DIRS = [
    "etc/ssh/sshd_config.d",
    "etc/NetworkManager/system-connections",
    "etc/systemd/system/multi-user.target.wants",
    "home/pi",
    "root",
]


@pytest.fixture
def rootfs(tmp_path: Path) -> Path:
    root = tmp_path / "rootfs"
    for name, text in FILES.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    for name in DIRS:
        (root / name).mkdir(parents=True, exist_ok=True)
    (root / "lib").symlink_to("usr/lib")
    (root / "etc/os-release").symlink_to("../usr/lib/os-release")
    return root


@pytest.fixture
def target(rootfs: Path) -> LocalTarget:
    return LocalTarget({"/": str(rootfs)})


DUMMY_PLATFORM = """\
from board_tune.registry import Board, Platform

class Dummy(Platform):
    title = "Dummy"
    boards = (Board("DUMMY_A", "Dummy A"), Board("DUMMY_B", "Dummy B"))

PLATFORM = Dummy()
"""

DUMMY_HOOK = """\
from board_tune.registry import Hook

class Greeting(Hook):
    title = "Greeting"
    symbol = "GREETING"

    def plan(self, ctx):
        ctx.plan.write("/etc/motd", self.message(ctx) + "\\n")

    def message(self, ctx):
        return ctx.config.str("GREETING_TEXT")

HOOK = Greeting()
"""

DUMMY_HOOK_KCONFIG = """\
menuconfig GREETING
	bool "Greeting"

if GREETING

config GREETING_TEXT
	string "Text"
	default "hello"

config GREETING_REPEAT
	int "Repeat"
	range 1 10
	default 1

endif
"""

DUMMY_PLATFORM_HOOK_KCONFIG = """\
config DUMMY_B_LOUD
	bool "Loud greeting"
	depends on GREETING && BOARD_DUMMY_B
"""


@pytest.fixture
def ext_path(tmp_path, monkeypatch):
    """An out-of-tree dummy platform and hook on BOARD_TUNE_PATH."""
    ext = tmp_path / "ext"
    (ext / "platforms/dummy/hooks").mkdir(parents=True)
    (ext / "platforms/dummy/__init__.py").write_text(DUMMY_PLATFORM)
    (ext / "platforms/dummy/hooks/greeting.Kconfig").write_text(DUMMY_PLATFORM_HOOK_KCONFIG)
    (ext / "hooks/greeting").mkdir(parents=True)
    (ext / "hooks/greeting/__init__.py").write_text(DUMMY_HOOK)
    (ext / "hooks/greeting/Kconfig").write_text(DUMMY_HOOK_KCONFIG)
    monkeypatch.setenv("BOARD_TUNE_PATH", str(ext))
    return ext
