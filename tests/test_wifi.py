import pytest

from board_tune.cli import build_plan, main
from board_tune.kconfig import Configuration
from board_tune.registry import Registry

PASSWORD = 'a password\\with=signs'


@pytest.fixture
def wififs(rootfs):
    for name in ("usr/sbin/iw", "usr/sbin/rfkill", "usr/bin/nmcli"):
        file = rootfs / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("test binary\n")
        file.chmod(0o755)
    countries = rootfs / "usr/share/zoneinfo/iso3166.tab"
    countries.parent.mkdir(parents=True, exist_ok=True)
    countries.write_text("# country codes\nUA\tUkraine\nGB\tBritain\nUS\tUSA\n")
    unit = rootfs / "usr/lib/systemd/system/NetworkManager.service"
    unit.write_text("[Unit]\nDescription=NetworkManager\n[Service]\nExecStart=/usr/sbin/NetworkManager\n[Install]\nWantedBy=multi-user.target\n")
    return rootfs


@pytest.fixture
def source(tmp_path):
    file = tmp_path / "wifi-password"
    file.write_text(PASSWORD + "\n")
    return file


@pytest.fixture
def run(wififs, tmp_path, capsys):
    def invoke(config="", expect=0, extra=()):
        profile = tmp_path / "wifi.cfg"
        profile.write_text('CONFIG_WIFI=y\nCONFIG_WIFI_SSID="Lab WiFi"\n' + config)
        result = main([str(wififs), "--load", str(profile), "-y", *extra])
        out, err = capsys.readouterr()
        assert result == expect, out + err
        return out + err
    return invoke


def keyfile(root):
    return next((root / "etc/NetworkManager/system-connections").glob("board-tune-wifi-*.nmconnection"))


def test_private_wifi_profile_and_readback(wififs, source, run, target, tmp_path):
    config = f'CONFIG_WIFI_PASSWORD_FILE="{source}"\nCONFIG_WIFI_COUNTRY="UA"\nCONFIG_WIFI_HIDDEN=y\n'
    output = run(config)
    file = keyfile(wififs)
    text = file.read_text()
    assert "ssid=Lab\\sWiFi" in text
    assert "hidden=true" in text and "key-mgmt=wpa-psk" in text
    assert "method=auto" in text and "autoconnect-priority=100" in text
    assert file.stat().st_mode & 0o777 == 0o600
    assert PASSWORD not in output
    assert "sensitive content hidden" in output
    unit = (wififs / "etc/systemd/system/board-tune-wifi.service").read_text()
    assert "ExecStartPre=/usr/sbin/iw reg set UA" in unit
    assert "Before=NetworkManager.service" in unit
    radio = (wififs / "etc/systemd/system/board-tune-wifi-enable.service").read_text()
    assert "After=NetworkManager.service" in radio and "nmcli radio wifi on" in radio
    assert "Nothing to change." in run(config)
    conf = Configuration(Registry.load(), str(tmp_path))
    conf.read_target(Registry.load(), target)
    assert conf.config.enabled("WIFI")
    assert conf.config.str("WIFI_SSID") == "Lab WiFi"
    assert conf.config.str("WIFI_COUNTRY") == "UA"
    assert conf.config.enabled("WIFI_HIDDEN")
    assert conf.config.str("WIFI_PASSWORD_FILE") == ""
    assert conf.problems() == []


def test_keep_existing_secret_and_change_profile_without_local_password(wififs, source, run):
    run(f'CONFIG_WIFI_PASSWORD_FILE="{source}"\n')
    assert "Nothing to change." in run()
    run("CONFIG_WIFI_PRIORITY=200\n")
    assert "autoconnect-priority=200" in keyfile(wififs).read_text()
    assert "psk=a\\spassword\\\\with=signs" in keyfile(wififs).read_text()


def test_open_static_wifi_and_disabled_autoconnect(wififs, run):
    run('CONFIG_WIFI_AUTH_OPEN=y\nCONFIG_WIFI_AUTOCONNECT=n\nCONFIG_WIFI_STATIC=y\n'
        'CONFIG_WIFI_ADDRESS="10.0.0.20/24"\nCONFIG_WIFI_GATEWAY="10.0.0.1"\nCONFIG_WIFI_DNS="1.1.1.1"\n')
    text = keyfile(wififs).read_text()
    assert "wifi-security" not in text
    assert "autoconnect=false" in text
    assert "address1=10.0.0.20/24" in text
    assert "gateway=10.0.0.1" in text and "dns=1.1.1.1;" in text


def test_wpa3_requires_pmf(wififs, source, run):
    run(f'CONFIG_WIFI_AUTH_SAE=y\nCONFIG_WIFI_PASSWORD_FILE="{source}"\n')
    text = keyfile(wififs).read_text()
    assert "key-mgmt=sae" in text and "pmf=3" in text


def test_changing_ssid_does_not_copy_secret(wififs, source, run):
    run(f'CONFIG_WIFI_PASSWORD_FILE="{source}"\n')
    output = run('CONFIG_WIFI_SSID="Other network"\n', expect=1)
    assert "required" in output
    assert len(list((wififs / "etc/NetworkManager/system-connections").glob("*.nmconnection"))) == 1


@pytest.mark.parametrize("config,message", [
    ('CONFIG_WIFI_AUTH_OPEN=y\nCONFIG_WIFI_SSID=""\n', "WIFI_SSID"),
    ('CONFIG_WIFI_AUTH_OPEN=y\nCONFIG_WIFI_COUNTRY="ZZ"\n', "ISO country"),
    ('CONFIG_WIFI_AUTH_OPEN=y\nCONFIG_WIFI_IFACE="bad interface"\n', "WIFI_IFACE"),
    ('CONFIG_WIFI_AUTH_OPEN=y\nCONFIG_WIFI_STATIC=y\nCONFIG_WIFI_ADDRESS="10.0.0.1"\n', "prefix length"),
])
def test_invalid_wifi_does_not_write(wififs, run, config, message):
    assert message in run(config, expect=1)
    assert not (wififs / "var/backups/board-tune").exists()


def test_invalid_password_does_not_leak(wififs, source, run):
    source.write_text("tiny")
    output = run(f'CONFIG_WIFI_PASSWORD_FILE="{source}"\n', expect=1)
    assert "tiny" not in output


def test_live_changes_reload_profiles_but_do_not_activate_or_unblock(wififs, source, target, tmp_path, monkeypatch):
    conf = Configuration(Registry.load(), str(tmp_path))
    for value in ("WIFI=y", "WIFI_SSID=Lab WiFi", f"WIFI_PASSWORD_FILE={source}", "WIFI_COUNTRY=UA"):
        conf.set(value)
    target.live = True
    calls = []
    monkeypatch.setattr(target, "run", lambda argv, input=None: calls.append(argv))
    plan = build_plan(Registry.load(), conf.config, target)
    plan.apply(plan.changes())
    assert ["nmcli", "connection", "reload"] in calls
    assert not any("restart" in argv or "up" in argv or "unblock" in argv or "radio" in argv for argv in calls)


def test_byte_list_like_ssid_roundtrip(wififs, run, target, tmp_path):
    run('CONFIG_WIFI_AUTH_OPEN=y\nCONFIG_WIFI_SSID="65;66;"\n')
    conf = Configuration(Registry.load(), str(tmp_path))
    conf.read_target(Registry.load(), target)
    assert conf.config.str("WIFI_SSID") == "65;66;"
