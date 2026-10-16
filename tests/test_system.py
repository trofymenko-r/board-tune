import pytest

from board_tune.cli import build_plan, main
from board_tune.kconfig import Configuration
from board_tune.registry import Registry


@pytest.fixture
def systemfs(rootfs):
    files = {
        "etc/hostname": "old\n",
        "etc/hosts": "127.0.0.1 localhost\n127.0.1.1 old alias # local host\n::1 localhost\n",
        "etc/timezone": "Etc/UTC\n",
        "usr/share/zoneinfo/Etc/UTC": "UTC test zone",
        "usr/share/zoneinfo/Europe/Kyiv": "Kyiv test zone",
        "etc/default/locale": "LANG=en_US.UTF-8\nLC_TIME=C\n",
        "etc/locale.gen": "en_US.UTF-8 UTF-8\n# uk_UA.UTF-8 UTF-8\n",
        "usr/share/i18n/SUPPORTED": "en_US.UTF-8 UTF-8\nuk_UA.UTF-8 UTF-8\n",
        "usr/sbin/locale-gen": "test binary\n",
        "etc/default/keyboard": 'XKBMODEL="pc105"\nXKBLAYOUT="us"\nXKBVARIANT=""\nXKBOPTIONS=""\n',
        "usr/lib/systemd/system/systemd-timesyncd.service": "[Unit]\nDescription=NTP\n[Service]\nExecStart=/usr/lib/systemd/systemd-timesyncd\n[Install]\nWantedBy=sysinit.target\n",
    }
    for name, text in files.items():
        path = rootfs / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    (rootfs / "etc/localtime").symlink_to("/usr/share/zoneinfo/Etc/UTC")
    return rootfs


@pytest.fixture
def run(systemfs, tmp_path, capsys):
    def invoke(config="", expect=0, extra=()):
        profile = tmp_path / "system.cfg"
        profile.write_text("CONFIG_SYSTEM=y\n" + config)
        result = main([str(systemfs), "--load", str(profile), "-y", *extra])
        out, err = capsys.readouterr()
        assert result == expect, out + err
        return out + err
    return invoke


def test_hostname_timezone_and_keyboard_idempotently(systemfs, run):
    config = ('CONFIG_SYSTEM_HOSTNAME="board-01"\nCONFIG_SYSTEM_TIMEZONE="Europe/Kyiv"\n'
              'CONFIG_SYSTEM_KEYBOARD_LAYOUT="us,ua"\nCONFIG_SYSTEM_KEYBOARD_OPTIONS="grp:alt_shift_toggle"\n')
    run(config)
    assert (systemfs / "etc/hostname").read_text() == "board-01\n"
    hosts = (systemfs / "etc/hosts").read_text()
    assert "127.0.1.1\tboard-01 alias # local host" in hosts
    assert "127.0.0.1 localhost" in hosts
    assert (systemfs / "etc/localtime").readlink().as_posix() == "/usr/share/zoneinfo/Europe/Kyiv"
    assert (systemfs / "etc/timezone").read_text() == "Europe/Kyiv\n"
    keyboard = (systemfs / "etc/default/keyboard").read_text()
    assert "XKBMODEL=\"pc105\"" in keyboard
    assert "XKBLAYOUT=us,ua" in keyboard
    assert "grp:alt_shift_toggle" in keyboard
    assert "Nothing to change." in run(config)


def test_offline_locale_generates_on_boot_and_keeps_overrides(systemfs, run):
    config = 'CONFIG_SYSTEM_LOCALE="uk_UA.UTF-8"\n'
    output = run(config)
    assert "locale generation is scheduled" in output
    assert "\nuk_UA.UTF-8 UTF-8\n" in (systemfs / "etc/locale.gen").read_text()
    language = (systemfs / "etc/default/locale").read_text()
    assert "LANG=uk_UA.UTF-8" in language and "LC_TIME=C" in language
    assert (systemfs / "var/lib/board-tune/locale-pending").read_text() == "uk_UA.UTF-8\n"
    assert (systemfs / "etc/systemd/system/multi-user.target.wants/board-tune-locale.service").is_symlink()
    assert "Nothing to change." in run(config)


def test_ntp_enable_readback_disable_and_idempotency(systemfs, target, tmp_path, run):
    config = 'CONFIG_SYSTEM_NTP_ENABLE=y\nCONFIG_SYSTEM_NTP_SERVERS="time.example.org 192.0.2.1 2001:db8::1"\n'
    run(config)
    dropin = systemfs / "etc/systemd/timesyncd.conf.d/90-board-tune.conf"
    assert "NTP=time.example.org 192.0.2.1 2001:db8::1" in dropin.read_text()
    conf = Configuration(Registry.load(), str(tmp_path))
    conf.read_target(Registry.load(), target)
    assert conf.config.enabled("SYSTEM_NTP_ENABLE")
    assert conf.config.str("SYSTEM_NTP_SERVERS") == "time.example.org 192.0.2.1 2001:db8::1"
    assert conf.config.str("SYSTEM_HOSTNAME") == "old"
    assert conf.config.str("SYSTEM_TIMEZONE") == "Etc/UTC"
    assert conf.config.str("SYSTEM_LOCALE") == "en_US.UTF-8"
    assert "Nothing to change." in run(config)
    run("CONFIG_SYSTEM_NTP_DISABLE=y\n")
    assert (systemfs / "etc/systemd/system/systemd-timesyncd.service").readlink().as_posix() == "/dev/null"
    assert "Nothing to change." in run("CONFIG_SYSTEM_NTP_DISABLE=y\n")


def test_hostname_change_does_not_mask_unmanaged_ntp(systemfs, target, tmp_path):
    registry = Registry.load()
    conf = Configuration(registry, str(tmp_path))
    conf.read_target(registry, target)
    assert conf.config.enabled("SYSTEM_NTP_KEEP")
    conf.set("SYSTEM_HOSTNAME=updated")
    plan = build_plan(registry, conf.config, target, {"serial_console", "ssh", "network", "users"})
    plan.apply(plan.changes())
    assert not (systemfs / "etc/systemd/system/systemd-timesyncd.service").is_symlink()


@pytest.mark.parametrize("config,message", [
    ('CONFIG_SYSTEM_HOSTNAME="bad host"\n', "hostname"),
    ('CONFIG_SYSTEM_TIMEZONE="../../etc/passwd"\n', "zone"),
    ('CONFIG_SYSTEM_TIMEZONE="Europe/Missing"\n', "zone"),
    ('CONFIG_SYSTEM_LOCALE="zz_ZZ.UTF-8"\n', "not supported"),
    ('CONFIG_SYSTEM_KEYBOARD_LAYOUT="us;touch /file"\n', "invalid characters"),
    ('CONFIG_SYSTEM_NTP_ENABLE=y\nCONFIG_SYSTEM_NTP_SERVERS="bad/server"\n', "invalid host"),
])
def test_invalid_system_settings_do_not_write(systemfs, run, config, message):
    before = (systemfs / "etc/hostname").read_bytes()
    assert message in run(config, expect=1)
    assert (systemfs / "etc/hostname").read_bytes() == before
    assert not (systemfs / "var/backups/board-tune").exists()


def test_live_generates_locale_without_scheduling_offline_service(systemfs, target, tmp_path, monkeypatch):
    registry = Registry.load()
    conf = Configuration(registry, str(tmp_path))
    for value in ("SYSTEM=y", "SYSTEM_LOCALE=uk_UA.UTF-8", "SYSTEM_HOSTNAME=board-02", "SYSTEM_NTP_ENABLE=y"):
        conf.set(value)
    target.live = True
    calls = []
    monkeypatch.setattr(target, "run", lambda argv, input=None: calls.append(argv))
    plan = build_plan(registry, conf.config, target)
    plan.apply(plan.changes())
    assert ["/usr/sbin/locale-gen"] in calls
    assert ["hostnamectl", "set-hostname", "board-02"] in calls
    assert ["systemctl", "restart", "systemd-timesyncd.service"] in calls
    assert not (systemfs / "var/lib/board-tune/locale-pending").exists()
