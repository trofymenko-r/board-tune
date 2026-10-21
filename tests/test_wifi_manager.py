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


@pytest.mark.parametrize('section', ['wifi', '802-11-wireless'])
@pytest.mark.parametrize('mode,number', list(wifi.POWERSAVE.items()))
def test_editor_power_saving_modes_preserve_profile(saved, target, section, mode, number):
    path = saved('Lab', 'uuid-lab')
    path.write_text(path.read_text().replace('[wifi]', f'[{section}]\npowersave=3\nband=bg'))
    profile = wifi.inventory(target)[0]
    editor = wifi.Editor.from_profile(profile)
    assert editor.powersave == 'enable'
    editor.powersave = mode
    identifier, text = editor.render(profile)
    assert identifier == 'uuid-lab' and 'psk=private-key' in text and 'band=bg' in text
    assert f'powersave={number}\n' in text
    assert text.count('powersave=') == 1
    assert f'[{section}]\n' in text
    if section != 'wifi':
        assert '[wifi]\n' not in text
    assert 'powersave=3' in path.read_text()  # edit is still only a draft


@pytest.mark.parametrize('number', ['', '2', '42'])
def test_editor_keep_preserves_unset_and_unknown_power_saving(saved, target, number):
    path = saved('Lab', 'uuid-lab')
    if number:
        path.write_text(path.read_text().replace('[wifi]', f'[wifi]\npowersave={number}'))
    profile = wifi.inventory(target)[0]
    editor = wifi.Editor.from_profile(profile)
    editor.powersave = 'keep'
    _, text = editor.render(profile)
    if number:
        assert f'powersave={number}\n' in text
    else:
        assert 'powersave=' not in text


def test_new_profile_and_invalid_power_mode():
    editor = wifi.Editor(ssid='Open', auth='open')
    assert 'powersave=' not in editor.render()[1]
    editor.powersave = 'disable'
    assert 'powersave=2\n' in editor.render()[1]
    editor.powersave = 'bad'
    with pytest.raises(wifi.Error, match='Unsupported Wi-Fi power saving mode'):
        editor.render()


def test_driver_power_status_uses_absolute_iw(target, rootfs, monkeypatch):
    tool = rootfs / 'usr/sbin/iw'
    tool.parent.mkdir(parents=True, exist_ok=True)
    tool.touch()
    target.live = True
    calls = []
    def run(argv, input=None):
        calls.append(argv)
        return 'Power save: off\n'
    monkeypatch.setattr(target, 'run', run)
    assert wifi.power_status(target, 'wlan0') == 'off'
    assert calls[0][-5:] == ['/usr/sbin/iw', 'dev', 'wlan0', 'get', 'power_save']


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


def test_matching_keeps_security_and_adapter_separate():
    profiles = [wifi.Profile('a', 'PSK', 'Lab', '', 'wpa-psk'),
                wifi.Profile('b', 'Open', 'Lab', 'wlan0', 'open'),
                wifi.Profile('c', 'Other adapter', 'Lab', 'wlan1', 'open')]
    ap = wifi.AccessPoint('Lab', '--', 50, '2412 MHz', '00:11:22:33:44:55', '1')
    assert [p.uuid for p in wifi.matching(profiles, ap, 'wlan0')] == ['b']
    ap.security = 'WPA2'
    assert [p.uuid for p in wifi.matching(profiles, ap, 'wlan0')] == ['a']
    assert wifi.band('2412 MHz') == '2.4 GHz' and wifi.band('5765 MHz') == '5 GHz'


def test_live_finish_consumes_only_selected_profile_draft(saved, target):
    saved('A', 'a')
    saved('B', 'b')
    session = wifi.Session(target)
    a, b = wifi.inventory(target)
    session.save(wifi.Editor.from_profile(a), a)
    session.save(wifi.Editor.from_profile(b), b)
    tx = wifi.LiveTransaction(session, a, 'wlan0')
    tx.directory = '/run/absent-test-job'
    tx.finish({'state': 'kept'})
    assert a.path not in session.edits and b.path in session.edits


def test_open_profile_omits_security_section_including_legacy_reference(saved, target):
    path = saved('Lab', 'lab')
    path.write_text(path.read_text().replace('ssid=Lab', 'ssid=Lab\nsecurity=wifi-security'))
    profile = wifi.inventory(target)[0]
    editor = wifi.Editor.from_profile(profile)
    editor.auth = 'open'
    _, text = editor.render(profile)
    assert '[wifi-security]' not in text and 'psk=' not in text and 'security=' not in text
    _, new = wifi.Editor(ssid='Open', auth='open').render()
    assert '[wifi-security]' not in new


def test_editing_priority_preserves_dhcp_dns_and_pmf(saved, target):
    path = saved('Lab', 'lab')
    path.write_text(path.read_text().replace('method=auto', 'method=auto\ndns=1.1.1.1;9.9.9.9;')
                    .replace('psk=private-key', 'psk=private-key\npmf=3'))
    profile = wifi.inventory(target)[0]
    editor = wifi.Editor.from_profile(profile)
    editor.priority = 200
    _, text = editor.render(profile)
    assert 'dns=1.1.1.1;9.9.9.9;' in text and 'pmf=3' in text


def test_cli_dry_run_previews_wifi_drafts_without_writing(rootfs, monkeypatch):
    from types import SimpleNamespace
    from board_tune import cli
    from board_tune.wifi_menu import WifiManager
    def menu(conf):
        assert conf.dry_run
        manager = WifiManager(conf.target, allow_live=not conf.dry_run)
        messages = []
        manager.dialog = SimpleNamespace(message=lambda *args: messages.append(args))
        assert not manager.live(wifi.Profile('uuid', 'Lab', 'Lab', 'wlan0', 'open'))
        assert messages[0][0] == 'Dry run'
        manager.session.save(wifi.Editor(ssid='Lab', auth='open'))
        conf.wifi_manager = manager
        return True
    monkeypatch.setattr(cli, '_require_tty', lambda what: None)
    monkeypatch.setattr(Configuration, 'menu', menu)
    assert cli.main([str(rootfs), '--dry-run']) == 0
    assert not list((rootfs / 'etc/NetworkManager/system-connections').glob('*.nmconnection'))
    assert not (rootfs / 'var/backups/board-tune').exists()


@pytest.mark.parametrize('radio, reason', [('disabled:enabled', 'radio is disabled'),
                                           ('enabled:disabled', 'blocked by hardware')])
def test_scan_distinguishes_radio_block_from_empty_results(target, monkeypatch, radio, reason):
    target.live = True
    monkeypatch.setattr(target, 'run', lambda argv, input=None: radio)
    with pytest.raises(Exception, match=reason):
        wifi.scan(target, 'wlan0')
