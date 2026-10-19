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
