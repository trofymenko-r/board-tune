from tempfile import TemporaryDirectory

import pytest

from board_tune import wifi
from board_tune.hooks.wifi import HOOK
from board_tune.kconfig import Configuration
from board_tune.plan import Plan
from board_tune.registry import Context, Registry


@pytest.fixture
def saved(rootfs):
    def add(name, identifier, ssid=None, auth='wpa-psk', extra=''):
        path = rootfs / 'etc/NetworkManager/system-connections' / (name + '.nmconnection')
        path.write_text(f'[connection]\nid={name}\nuuid={identifier}\ntype=wifi\ninterface-name=wlan0\n'
                        f'[wifi]\nssid={ssid or name}\n[wifi-security]\nkey-mgmt={auth}\npsk=private-key\n'
                        f'[ipv4]\nmethod=auto\n{extra}')
        return path
    return add


def test_escaped_nmcli_fields():
    assert wifi.fields(r'name\:with\\slash:uuid:wifi:wlan0') == ['name:with\\slash', 'uuid', 'wifi', 'wlan0']


def test_offline_inventory_keeps_all_profiles_and_secrets_private(saved, target):
    saved('A', 'a', 'Same SSID')
    saved('B', 'b', 'Same SSID', auth='wpa-eap')
    profiles = wifi.inventory(target)
    assert [p.uuid for p in profiles] == ['a', 'b']
    assert profiles[0].editable and not profiles[1].editable
    assert 'private-key' not in repr(profiles)


def test_active_readback_beats_alphabetical_order(saved, target, rootfs, monkeypatch):
    saved('A', 'a')
    saved('Z', 'z')
    binary = rootfs / 'usr/bin/nmcli'
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.touch()
    target.live = True
    monkeypatch.setattr(target, 'run', lambda argv, input=None: 'z:802-11-wireless:wlan0\n')
    with TemporaryDirectory() as work:
        reg = Registry.load()
        conf = Configuration(reg, work)
        platform, board = reg.board(conf.config)
        values = HOOK.read(Context(Plan(target), conf.config, platform, board))
    assert values['WIFI_SSID'] == 'Z'


def test_runtime_inventory_has_active_first(saved, target, rootfs, monkeypatch):
    saved('A', 'a')
    saved('Z', 'z')
    binary = rootfs / 'usr/bin/nmcli'
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.touch()
    target.live = True
    def run(argv, input=None):
        if '--active' in argv:
            return 'z:802-11-wireless:wlan0\n'
        return 'Z:z:802-11-wireless:wlan0\nA:a:802-11-wireless:\n'
    monkeypatch.setattr(target, 'run', run)
    profiles = wifi.inventory(target)
    assert [p.uuid for p in profiles] == ['z', 'a']
    assert profiles[0].active == 'wlan0'


def test_editor_preserves_uuid_unknown_settings_and_secret(saved, target):
    path = saved('Lab', 'uuid-lab', extra='route1=10.0.0.0/8,192.168.1.1\n[ipv6]\nmethod=disabled\n')
    before = path.read_bytes()
    profile = wifi.inventory(target)[0]
    editor = wifi.Editor.from_profile(profile)
    editor.priority = 123
    session = wifi.Session(target)
    updated = session.save(editor, profile)
    assert updated.uuid == 'uuid-lab' and updated.path == profile.path
    assert path.read_bytes() == before  # draft only
    plan = Plan(target)
    session.plan_into(plan)
    assert 'private-key' not in str(plan.changes())
    plan.apply(plan.changes())
    text = path.read_text()
    assert 'psk=private-key' in text and 'route1=10.0.0.0/8,192.168.1.1' in text
    assert 'method=disabled' in text and 'autoconnect-priority=123' in text
    assert len(wifi.inventory(target)) == 1


def test_forget_removes_selected_uuid_only_and_is_recoverable(saved, target):
    first = saved('First', 'first', 'Same SSID')
    second = saved('Second', 'second', 'Same SSID')
    session = wifi.Session(target)
    session.forget(wifi.inventory(target)[0])
    assert first.exists() and second.exists()
    plan = Plan(target)
    session.plan_into(plan)
    plan.apply(plan.changes())
    assert not first.exists() and second.exists()
    plan.backup.restore()
    assert first.exists() and second.exists()


def test_concurrent_profile_edits_are_refused(saved, target):
    file = saved('Lab', 'lab')
    profile = wifi.inventory(target)[0]
    session = wifi.Session(target)
    session.forget(profile)
    file.write_text(file.read_text() + '# concurrent administrator edit\n')
    with pytest.raises(Exception, match='changed outside'):
        session.plan_into(Plan(target))
    assert file.exists()


def test_new_profile_password_never_in_repr_or_preview(target):
    session = wifi.Session(target)
    editor = wifi.Editor(ssid='New network', password='super secret password')
    profile = session.save(editor)
    assert 'super secret password' not in repr(editor)
    assert 'super secret password' not in repr(profile)
    plan = Plan(target)
    session.plan_into(plan)
    assert 'super secret password' not in str(plan.changes())
    assert profile.uuid in plan.changes()[0].path


def test_changed_ssid_does_not_reuse_secret(saved, target):
    saved('Lab', 'lab')
    profile = wifi.inventory(target)[0]
    editor = wifi.Editor.from_profile(profile)
    editor.ssid = 'Other SSID'
    with pytest.raises(Exception, match='changing the SSID'):
        wifi.Session(target).save(editor, profile)


def test_scan_groups_ap_variants_and_escaped_ssids(target, monkeypatch):
    target.live = True
    monkeypatch.setattr(target, 'run', lambda argv, input=None:
                        '*:Lab\\:WiFi:72:WPA2:2412 MHz:AA\\:BB\\:CC\\:DD\\:EE\\:01:1\n'
                        ':Lab\\:WiFi:50:WPA2:5180 MHz:AA\\:BB\\:CC\\:DD\\:EE\\:02:36\n'
                        ':Lab\\:WiFi:40:WPA3:5180 MHz:AA\\:BB\\:CC\\:DD\\:EE\\:03:36\n')
    grouped = wifi.groups(wifi.scan(target, 'wlan0'))
    assert len(grouped) == 2 and len(grouped[0]) == 2
    assert grouped[0][0].ssid == 'Lab:WiFi'
    assert grouped[0][0].bssid == 'AA:BB:CC:DD:EE:01'
