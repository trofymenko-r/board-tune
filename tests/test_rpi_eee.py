"""Pi 5 boot EEE disable, including an initially EEE-enabled Device Tree."""

import shutil
import struct
import subprocess

import pytest

from board_tune.cli import build_plan
from board_tune.errors import Error
from board_tune.kconfig import Configuration
from board_tune.platforms.rpi import eee
from board_tune.platforms.rpi.overlaymap import properties
from board_tune.registry import Registry

MAC = '/axi/pcie@1000120000/rp1/ethernet@100000'
PHY = MAC + '/ethernet-phy@1'
OVERLAY = 'boot/firmware/overlays/board-tune-eee-off.dtbo'
CONFIG = 'boot/firmware/config.txt'


def base_tree():
    return {'compatible': b'raspberrypi,5-model-b\0brcm,bcm2712\0',
            'aliases': {'ethernet0': MAC.encode() + b'\0'},
            '__symbols__': {'phy1': PHY.encode() + b'\0'},
            'axi': {'pcie@1000120000': {'rp1': {'ethernet@100000': {
                'compatible': b'raspberrypi,rp1-gem\0cdns,macb\0',
                'phy-handle': struct.pack('>I', 1),
                'ethernet-phy@1': {'reg': struct.pack('>I', 1), 'phandle': struct.pack('>I', 1)}}}}}}


def blob(tree):
    """Independent recursive FDT fixture; native fdtoverlay also validates it."""
    names, offsets, data = b'', {}, b''
    def emit(name, entries):
        nonlocal names, data
        encoded = name.encode() + b'\0'
        data += struct.pack('>I', 1) + encoded + bytes(-len(encoded) % 4)
        for name, value in entries.items():
            if isinstance(value, dict):
                continue
            if name not in offsets:
                offsets[name] = len(names)
                names += name.encode() + b'\0'
            data += struct.pack('>3I', 3, len(value), offsets[name]) + value + bytes(-len(value) % 4)
        for name, value in entries.items():
            if isinstance(value, dict):
                emit(name, value)
        data += struct.pack('>I', 2)
    emit('', tree)
    data += struct.pack('>I', 9)
    return struct.pack('>10I', 0xd00dfeed, 56 + len(data) + len(names), 56,
                       56 + len(data), 40, 17, 16, 0, len(names), len(data)) + bytes(16) + data + names


@pytest.fixture
def setup(rootfs, target, tmp_path):
    (rootfs / 'boot/firmware' / eee.BASE).write_bytes(blob(base_tree()))
    profile = rootfs / 'etc/NetworkManager/system-connections/admin.nmconnection'
    profile.write_text('[connection]\nid=Admin\ntype=ethernet\ninterface-name=end0\n'
                       '[ipv4]\nmethod=auto\nroute1=10.0.0.0/8,192.168.1.1\n')
    reg = Registry.load()
    conf = Configuration(reg, str(tmp_path))
    for setting in ('BOARD_RPI5=y', 'NETWORK=y', 'NETWORK_IFACE=end0'):
        conf.set(setting)
    def plan(mode='DISABLE'):
        conf.set(f'NETWORK_EEE_{mode}=y')
        return build_plan(reg, conf.config, target,
                          skip_hooks={h.name for h in reg.hooks if h.name != 'network'})
    return conf, plan


def apply(plan):
    plan.apply(plan.changes())


def live_port(rootfs, target):
    actual = rootfs / ('sys/firmware/devicetree/base' + PHY)
    actual.mkdir(parents=True)
    device = rootfs / 'sys/class/net/end0'
    (device / 'phydev').mkdir(parents=True)
    (device / 'phydev/phy_id').write_text('0x600d84a2\n')
    (device / 'phydev/of_node').symlink_to('/sys/firmware/devicetree/base' + PHY)
    (device / 'device').mkdir()
    driver = rootfs / 'sys/bus/platform/drivers/macb'
    driver.mkdir(parents=True)
    (device / 'device/driver').symlink_to('/sys/bus/platform/drivers/macb')
    model = rootfs / 'proc/device-tree/model'
    model.parent.mkdir(parents=True)
    model.write_bytes(b'Raspberry Pi 5 Model B Rev 1.0\0')
    target.live = True


def test_disable_from_enabled_base_is_persistent_and_idempotent(setup, rootfs, target, tmp_path):
    conf, make = setup
    config = rootfs / CONFIG
    original = config.read_bytes()
    base = (rootfs / 'boot/firmware' / eee.BASE).read_bytes()
    assert (PHY, 'eee-broken-1000t') not in properties(base)
    plan = make()
    assert not plan.commands and not plan.pre_commands
    assert {c.path for c in plan.changes()} == {'/' + CONFIG, '/' + OVERLAY}
    apply(plan)
    assert config.read_bytes().startswith(original)
    props = properties((rootfs / OVERLAY).read_bytes())
    assert props[('/fragment@0', 'target-path')] == PHY.encode() + b'\0'
    assert props[('/fragment@0/__overlay__', 'eee-broken-100tx')] == b''
    assert props[('/fragment@0/__overlay__', 'eee-broken-1000t')] == b''
    assert (rootfs / 'boot/firmware' / eee.BASE).read_bytes() == base
    assert make().changes() == [] and make('KEEP').changes() == []
    imported = Configuration(Registry.load(), str(tmp_path))
    imported.read_target(Registry.load(), target)
    assert imported.config.enabled('BOARD_RPI5')
    assert imported.config.enabled('NETWORK_EEE_DISABLE')
    apply(make('REMOVE'))
    assert not (rootfs / OVERLAY).exists()
    assert config.read_bytes() == original
    assert make('REMOVE').changes() == []


def test_native_overlay_application_blocks_an_enabled_tree(setup, rootfs, tmp_path):
    tool = shutil.which('fdtoverlay')
    if not tool:
        pytest.skip('optional native overlay application')
    _, make = setup
    plan = make()
    overlay = tmp_path / 'disable.dtbo'
    overlay.write_bytes(plan.read('/' + OVERLAY))
    merged = tmp_path / 'merged.dtb'
    subprocess.run([tool, '-i', str(rootfs / 'boot/firmware' / eee.BASE), '-o', str(merged), str(overlay)], check=True)
    before, after = properties((rootfs / 'boot/firmware' / eee.BASE).read_bytes()), properties(merged.read_bytes())
    assert (PHY, 'eee-broken-100tx') not in before
    assert after[(PHY, 'eee-broken-100tx')] == b'' and after[(PHY, 'eee-broken-1000t')] == b''
    assert all(after[k] == v for k, v in before.items())
    assert set(after) - set(before) == {(PHY, n) for n in ('eee-broken-100tx', 'eee-broken-1000t', 'board-tune,eee-disabled')}


def test_live_port_never_queries_or_changes_running_eee(setup, rootfs, target, monkeypatch):
    _, make = setup
    live_port(rootfs, target)
    monkeypatch.setattr(target, 'run', lambda argv, input=None: pytest.fail(f'no live commands: {argv}'))
    plan = make()
    assert plan.commands == [] and plan.pre_commands == []
    assert any('takes effect after reboot' in n for n in plan.notes)
    assert not (rootfs / OVERLAY).exists()  # still only a preview


@pytest.mark.parametrize('bad', ['include usercfg.txt', 'os_prefix=other/', 'device_tree=custom.dtb',
                                 'device_tree=', 'overlay_prefix=', 'overlay_prefix=custom/',
                                 'dtoverlay=board-tune-eee-off', 'dtoverlay= board-tune-eee-off'])
def test_ambiguous_boot_layout_is_refused(setup, rootfs, bad):
    _, make = setup
    with (rootfs / CONFIG).open('a') as stream:
        stream.write(bad + '\n')
    with pytest.raises(Error, match='Pi 5 EEE:'):
        make()
    assert not (rootfs / OVERLAY).exists()


@pytest.mark.parametrize('data', [b'foreign binary', b'', eee.overlay('/other/phy') + b'changed'])
def test_foreign_overlay_file_is_not_overwritten(setup, rootfs, data):
    _, make = setup
    path = rootfs / OVERLAY
    path.parent.mkdir(parents=True)
    path.write_bytes(data)
    original = path.read_bytes()
    with pytest.raises(Error, match='not an intact board-tune overlay'):
        make()
    assert path.read_bytes() == original


def test_corrupt_base_and_wrong_phy_handle_are_refused(setup, rootfs):
    _, make = setup
    path = rootfs / 'boot/firmware' / eee.BASE
    path.write_bytes(b'invalid DTB')
    with pytest.raises(Error, match='valid .*dtb is required'):
        make()
    tree = base_tree()
    tree['aliases']['ethernet0'] = b'/missing\0'
    path.write_bytes(blob(tree))
    with pytest.raises(Error, match='does not identify'):
        make()


def test_pending_boot_disable_cannot_be_enabled_silently(setup):
    _, make = setup
    apply(make())
    with pytest.raises(Error, match='remove the managed boot-disable policy'):
        make('ENABLE')


def test_other_interface_keep_and_unconfigured_remove_do_not_touch_boot(setup):
    conf, make = setup
    assert not any(c.path.startswith('/boot/') for c in make('KEEP').changes())
    assert make('REMOVE').changes() == []
    conf.set('NETWORK_IFACE=enx123')
    with pytest.raises(Error, match='requires ethtool'):
        make()


def test_dangling_overlay_symlink_is_not_followed(setup, rootfs):
    _, make = setup
    path = rootfs / OVERLAY
    path.parent.mkdir(parents=True)
    path.symlink_to('/boot/firmware/foreign.dtbo')
    with pytest.raises(Error, match='not an intact board-tune overlay'):
        make()
    assert path.is_symlink()


def test_removal_of_managed_boot_policy_is_recoverable(setup, rootfs, target):
    _, make = setup
    before = (rootfs / CONFIG).read_bytes()
    plan = make()
    apply(plan)
    assert plan.backup is not None
    plan.backup.restore()
    assert (rootfs / CONFIG).read_bytes() == before
    assert not (rootfs / OVERLAY).exists()


def test_usb_nic_does_not_remove_onboard_policy(setup, rootfs, target):
    conf, make = setup
    apply(make())
    original = (rootfs / CONFIG).read_bytes()
    live_port(rootfs, target)
    conf.set('NETWORK_IFACE=enx123')
    plan = make('REMOVE')
    assert not any(c.path in ('/' + CONFIG, '/' + OVERLAY) for c in plan.changes())
    assert (rootfs / CONFIG).read_bytes() == original


def test_site_settings_are_preserved_and_overlay_moves_last(setup, rootfs):
    _, make = setup
    apply(make())
    with (rootfs / CONFIG).open('a') as stream:
        stream.write('dtoverlay=custom-camera,cam0\n')
    apply(make())
    text = (rootfs / CONFIG).read_text()
    assert text.index('dtoverlay=custom-camera') < text.index('# >>> board-tune: network_eee')
    assert text.count('dtoverlay=board-tune-eee-off\n') == 1
    assert make().changes() == []


def test_old_boot_layout_uses_its_own_overlay_directory(setup, rootfs):
    _, make = setup
    for name in ('config.txt', 'cmdline.txt', eee.BASE):
        (rootfs / 'boot/firmware' / name).rename(rootfs / 'boot' / name)
    plan = make()
    assert {c.path for c in plan.changes()} == {'/boot/config.txt', '/boot/overlays/' + eee.NAME + '.dtbo'}
    apply(plan)
    assert make().changes() == []


def test_networkd_power_only_edit_preserves_ip_config(setup, rootfs):
    _, make = setup
    shutil.rmtree(rootfs / 'etc/NetworkManager')
    network = rootfs / 'etc/systemd/network/admin.network'
    network.parent.mkdir(parents=True)
    original = '[Match]\nName=end0\n[Network]\nDHCP=ipv4\n[Route]\nDestination=10.0.0.0/8\nGateway=192.168.1.1\n'
    network.write_text(original)
    plan = make()
    assert {c.path for c in plan.changes()} == {'/' + CONFIG, '/' + OVERLAY}
    apply(plan)
    assert network.read_text() == original


def test_legacy_ethtool_rules_are_migrated_without_activation(setup, rootfs, target):
    from board_tune import eee as generic
    _, make = setup
    unit, path, dispatcher = generic.paths('end0')
    old = rootfs / path.lstrip('/')
    old.write_text(generic.MARKER + '[Service]\nType=oneshot\n'
                   'ExecStart=/usr/sbin/ethtool --set-eee end0 eee on\n'
                   '[Install]\nWantedBy=multi-user.target\n')
    hook = rootfs / dispatcher.lstrip('/')
    hook.parent.mkdir(parents=True)
    hook.write_text('#!/bin/sh\n' + generic.MARKER + 'exit 0\n')
    link = rootfs / 'etc/systemd/system/multi-user.target.wants' / unit
    link.symlink_to(path)
    plan = make()
    assert not plan.commands and not plan.pre_commands
    apply(plan)
    assert not target.exists(path) and not target.exists(dispatcher) and not link.is_symlink()
    assert (rootfs / OVERLAY).exists()


def test_duplicate_managed_blocks_are_not_imported_or_rewritten(setup, rootfs, target, tmp_path):
    _, make = setup
    apply(make())
    path = rootfs / CONFIG
    text = path.read_text()
    start, end = eee.configtxt._markers(eee.BLOCK)
    path.write_text(text + start + text.partition(start)[2].partition(end)[0] + end + '\n')
    conf = Configuration(Registry.load(), str(tmp_path))
    notes = conf.read_target(Registry.load(), target)
    assert conf.config.enabled('NETWORK_EEE_KEEP')
    assert any('duplicate blocks' in note for note in notes)
    with pytest.raises(Error, match='duplicate managed'):
        make()
