import shutil

import pytest

from board_tune import cli, eee
from board_tune.cli import build_plan
from board_tune.errors import Error
from board_tune.kconfig import Configuration
from board_tune.registry import Registry


@pytest.fixture
def setup(rootfs, target, tmp_path):
    tool = rootfs / 'usr/sbin/ethtool'
    tool.parent.mkdir(parents=True, exist_ok=True)
    tool.touch()
    reg = Registry.load()
    conf = Configuration(reg, str(tmp_path))
    for setting in ('BOARD_RPI5=y', 'NETWORK=y', 'NETWORK_IFACE=end0'):
        conf.set(setting)

    def make(mode):
        conf.set(f'NETWORK_EEE_{mode}=y')
        return build_plan(reg, conf.config, target,
                          skip_hooks={h.name for h in reg.hooks if h.name != 'network'})
    return conf, make


def apply(plan):
    plan.apply(plan.changes())


def test_default_keeps_eee_unmanaged(setup):
    _, make = setup
    plan = make('KEEP')
    assert not any('eee' in c.path for c in plan.changes())


def test_power_only_change_preserves_foreign_ip_profile(setup, rootfs):
    _, make = setup
    path = rootfs / 'etc/NetworkManager/system-connections/admin.nmconnection'
    text = ('[connection]\nid=Admin\ntype=ethernet\ninterface-name=end0\n'
            '[ipv4]\nmethod=auto\nroute1=10.0.0.0/8,192.168.1.1\n')
    path.write_text(text)
    plan = make('DISABLE')
    assert not any('system-connections' in c.path for c in plan.changes())
    apply(plan)
    assert path.read_text() == text
    assert make('KEEP').changes() == []


@pytest.mark.parametrize('mode,state', [('DISABLE', 'off'), ('ENABLE', 'on')])
def test_persistent_policy_readback_idempotency_and_removal(setup, target, rootfs, mode, state):
    conf, make = setup
    plan = make(mode)
    apply(plan)
    unit, path, dispatcher = eee.paths('end0')
    text = target.read(path).decode()
    assert f'ExecStart=/usr/sbin/ethtool --set-eee end0 eee {state}\n' in text
    assert 'BindsTo=sys-subsystem-net-devices-end0.device\n' in text
    assert (rootfs / f'etc/systemd/system/sys-subsystem-net-devices-end0.device.wants/{unit}').is_symlink()
    assert target.stat(dispatcher).mode & 0o777 == 0o755
    assert f'"$2" = up' in target.read(dispatcher).decode()
    assert eee.policy(make('KEEP'), 'end0') == mode
    conf.read_target(Registry.load(), target)
    assert conf.config.enabled(f'NETWORK_EEE_{mode}')
    assert make(mode).changes() == []
    assert make('KEEP').changes() == []
    apply(make('REMOVE'))
    assert not target.exists(path) and not target.exists(dispatcher)
    assert eee.policy(make('KEEP'), 'end0') == 'KEEP'
    assert make('REMOVE').changes() == []


def test_networkd_policy_without_dispatcher(setup, rootfs, target):
    _, make = setup
    shutil.rmtree(rootfs / 'etc/NetworkManager')
    apply(make('DISABLE'))
    assert target.exists(eee.paths('end0')[1])
    assert not target.exists(eee.paths('end0')[2])


def test_missing_ethtool_does_not_write_network_files(setup, rootfs):
    _, make = setup
    (rootfs / 'usr/sbin/ethtool').unlink()
    with pytest.raises(Error, match='requires ethtool'):
        make('DISABLE')
    assert not list((rootfs / 'etc/NetworkManager/system-connections').iterdir())


@pytest.mark.parametrize('owned', [1, 2])
@pytest.mark.parametrize('mode', ['DISABLE', 'REMOVE'])
def test_foreign_files_are_preserved(setup, rootfs, owned, mode):
    _, make = setup
    path = rootfs / eee.paths('end0')[owned].lstrip('/')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('administrator setting\n')
    with pytest.raises(Error, match='not owned by board-tune'):
        make(mode)
    assert path.read_text() == 'administrator setting\n'


@pytest.mark.parametrize('owned', [1, 2])
def test_dangling_foreign_policy_symlinks_are_preserved(setup, rootfs, owned):
    _, make = setup
    path = rootfs / eee.paths('end0')[owned].lstrip('/')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to('/etc/foreign-missing-policy')
    with pytest.raises(Error, match='not owned by board-tune'):
        make('REMOVE')
    assert path.is_symlink()


def test_live_probe_never_changes_running_link(setup, target, rootfs, monkeypatch):
    _, make = setup
    (rootfs / 'sys/class/net/end0').mkdir(parents=True)
    target.live = True
    calls = []
    def run(argv, input=None):
        calls.append(argv)
        return 'EEE settings for end0:\n\tEEE status: enabled - inactive\n'
    monkeypatch.setattr(target, 'run', run)
    plan = make('DISABLE')
    assert len(calls) == 1 and calls[0][-2:] == ['--show-eee', 'end0']
    assert plan.commands == [['systemctl', 'daemon-reload'], ['nmcli', 'connection', 'reload']]
    assert all('--set-eee' not in cmd and 'restart' not in cmd for cmd in plan.commands)


@pytest.mark.parametrize('output', ['', 'EEE status: unknown\n'])
def test_live_unsupported_status_refused(setup, target, rootfs, monkeypatch, output):
    _, make = setup
    (rootfs / 'sys/class/net/end0').mkdir(parents=True)
    target.live = True
    monkeypatch.setattr(target, 'run', lambda argv, input=None: output)
    with pytest.raises(Error, match='status is unavailable or unrecognized'):
        make('DISABLE')
    assert not target.exists(eee.paths('end0')[1])


@pytest.mark.parametrize('failed', [True, False])
def test_live_unsupported_driver_skips_only_eee(setup, target, rootfs, monkeypatch, failed):
    _, make = setup
    (rootfs / 'sys/class/net/end0').mkdir(parents=True)
    target.live = True
    def run(argv, input=None):
        if failed:
            raise Error('netlink error: Operation not supported')
        return 'EEE status: not supported\n'
    monkeypatch.setattr(target, 'run', run)
    plan = make('DISABLE')
    assert any('requested policy skipped' in note for note in plan.notes)
    assert not any('eee' in c.path for c in plan.changes())
    assert any('system-connections' in c.path for c in plan.changes())


def test_unchanged_policy_does_not_probe_unsupported_driver(setup, target, rootfs, monkeypatch):
    _, make = setup
    apply(make('DISABLE'))
    target.live = True
    def run(argv, input=None):
        pytest.fail(f'unchanged EEE must not execute {argv}')
    monkeypatch.setattr(target, 'run', run)
    assert make('DISABLE').changes() == []


def test_unknown_probe_errors_still_stop_apply(setup, target, rootfs, monkeypatch):
    _, make = setup
    (rootfs / 'sys/class/net/end0').mkdir(parents=True)
    target.live = True
    def run(argv, input=None):
        raise Error('Operation not permitted')
    monkeypatch.setattr(target, 'run', run)
    with pytest.raises(Error, match='no policy applied.*Operation not permitted'):
        make('DISABLE')


@pytest.mark.parametrize('edit_ssh', [False, True])
def test_menu_unsupported_eee_note_survives_no_change_exit(setup, target, rootfs, monkeypatch, capsys, edit_ssh):
    _, make = setup
    apply(make('KEEP'))
    (rootfs / 'sys/class/net/end0').mkdir(parents=True)
    before = {str(p): p.read_bytes() for p in rootfs.rglob('*') if p.is_file() and not p.is_symlink()}
    target.live = True
    def run(argv, input=None):
        assert '--show-eee' in argv
        raise Error('netlink error: Operation not supported')
    def menu(conf):
        conf.set('NETWORK_EEE_DISABLE=y')
        if edit_ssh:
            conf.set('SSH_PORT=2222')
        return True
    monkeypatch.setattr(target, 'run', run)
    monkeypatch.setattr(cli, 'open_target', lambda spec: target)
    monkeypatch.setattr(cli, '_require_tty', lambda what: None)
    monkeypatch.setattr(Configuration, 'menu', menu)
    assert cli.main(['board@example', '--dry-run']) == 0
    output = capsys.readouterr().out
    assert 'EEE control via ethtool is unavailable on end0; requested policy skipped' in output
    assert 'board-tune-eee-' not in output
    assert ('+Port 2222' in output) if edit_ssh else ('Nothing to change.' in output)
    assert {str(p): p.read_bytes() for p in rootfs.rglob('*') if p.is_file() and not p.is_symlink()} == before


def test_escaped_device_unit_for_dashed_interface(setup, target):
    conf, make = setup
    conf.set('NETWORK_IFACE=en-test0')
    plan = make('DISABLE')
    unit, path, _ = eee.paths('en-test0')
    assert unit == r'board-tune-eee-en\x2dtest0.service'
    assert r'BindsTo=sys-subsystem-net-devices-en\x2dtest0.device' in plan.read_text(path)


def test_bcm54213_boot_disable_is_imported_and_needs_no_ethtool(setup, rootfs, target, monkeypatch):
    conf, make = setup
    apply(make('KEEP'))
    directory = rootfs / 'etc/NetworkManager/system-connections'
    # A real board usually has an administrator profile, rather than the
    # generated filename. A Keep action must not create a duplicate of it.
    (directory / 'board-tune-end0.nmconnection').rename(directory / 'admin.nmconnection')
    phy = rootfs / 'sys/class/net/end0/phydev'
    (phy / 'of_node').mkdir(parents=True)
    (phy / 'phy_id').write_text('0x600d84a2\n')
    for mode in ('100tx', '1000t'):
        (phy / f'of_node/eee-broken-{mode}').touch()
    target.live = True
    def run(argv, input=None):
        pytest.fail(f'boot disable must not execute {argv}')
    monkeypatch.setattr(target, 'run', run)
    notes = conf.read_target(Registry.load(), target)
    assert conf.config.enabled('NETWORK_EEE_DISABLE')
    assert any('negotiated state not queried' in note for note in notes)
    plan = make('DISABLE')
    assert plan.changes() == [] and plan.commands == []
    assert any('already configured by Device Tree' in note for note in plan.notes)
    assert not target.exists(eee.paths('end0')[1])
    assert make('KEEP').changes() == []  # Keep must not normalize the IP profile either.


@pytest.mark.parametrize('identifier,modes,live', [
    ('0x600d84a2', ('100tx',), True),
    ('0x600d84a2', ('1000t',), True),
    ('0x600d84a2', ('100tx', '1000t'), False),
    ('0x12345678', ('100tx', '1000t'), True),
])
def test_boot_disable_requires_exact_live_phy_and_both_modes(rootfs, target, identifier, modes, live):
    phy = rootfs / 'sys/class/net/end0/phydev'
    (phy / 'of_node').mkdir(parents=True)
    (phy / 'phy_id').write_text(identifier + '\n')
    for mode in modes:
        (phy / f'of_node/eee-broken-{mode}').touch()
    target.live = live
    assert not eee.boot_disables_eee(target, 'end0')


def test_bcm54213_enable_is_not_mistaken_for_boot_disable(setup, rootfs, target, monkeypatch):
    _, make = setup
    apply(make('KEEP'))
    phy = rootfs / 'sys/class/net/end0/phydev'
    (phy / 'of_node').mkdir(parents=True)
    (phy / 'phy_id').write_text('0x600d84a2\n')
    for mode in ('100tx', '1000t'):
        (phy / f'of_node/eee-broken-{mode}').touch()
    target.live = True
    def run(argv, input=None):
        raise Error('netlink error: Operation not supported')
    monkeypatch.setattr(target, 'run', run)
    plan = make('ENABLE')
    assert plan.changes() == []
    assert any('control via ethtool is unavailable' in note for note in plan.notes)
    assert any('Device Tree blocks BCM54213PE EEE' in note for note in plan.notes)
