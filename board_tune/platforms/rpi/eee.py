"""Boot-time EEE disable for the Pi 5 Model B's RP1/BCM54213PE port.

An overlay adds PHY restrictions before autonegotiation. No running PHY or
MAC is changed; target-path is verified against the boot DTB and live device.
"""

import re
import struct

from board_tune.errors import Error
from . import configtxt, overlaymap

NAME = 'board-tune-eee-off'
BLOCK = 'network_eee'
MARKER = b'board-tune-eee-off-v1\0'
BASE = 'bcm2712-rpi-5-b.dtb'


def overlay(path):
    """Build a small FDT v17 overlay without requiring a host/target compiler."""
    if (not re.fullmatch(r'/[A-Za-z0-9,._+@/-]+', path)
            or any(p in ('', '.', '..') for p in path.split('/')[1:])):
        raise Error('EEE: invalid PHY Device Tree path')
    names, offsets = bytearray(), {}
    def string(text):
        value = text.encode('ascii') + b'\0'
        return value + b'\0' * (-len(value) % 4)
    def prop(name, value):
        if name not in offsets:
            offsets[name] = len(names)
            names.extend(name.encode() + b'\0')
        return struct.pack('>3I', 3, len(value), offsets[name]) + value + b'\0' * (-len(value) % 4)
    def node(name, body):
        return struct.pack('>I', 1) + string(name) + body + struct.pack('>I', 2)
    phy = node('__overlay__', prop('eee-broken-100tx', b'') + prop('eee-broken-1000t', b'')
               + prop('board-tune,eee-disabled', b''))
    fragment = node('fragment@0', prop('target-path', path.encode() + b'\0') + phy)
    tree = node('', prop('compatible', b'brcm,bcm2712\0') + prop('board-tune,managed', MARKER)
                + fragment) + struct.pack('>I', 9)
    strings = 56 + len(tree)
    return (struct.pack('>10I', 0xd00dfeed, strings + len(names), 56, strings,
                        40, 17, 16, 0, len(names), len(tree)) + bytes(16) + tree + names)


def _owned(data):
    props = overlaymap.properties(data or b'')
    if props is None or props.get(('/', 'board-tune,managed')) != MARKER:
        return None
    try:
        path = props[('/fragment@0', 'target-path')].removesuffix(b'\0').decode('ascii')
        return path if data == overlay(path) else None
    except (KeyError, UnicodeError, Error):
        return None


def _directory(ctx):
    return next((d for d in ('/boot/firmware', '/boot') if ctx.plan.exists(f'{d}/config.txt')), None)


def _outside(text):
    start, end = configtxt._markers(BLOCK)
    if text.count(start) != text.count(end) or text.count(start) > 1:
        raise Error('EEE: incomplete or duplicate managed config.txt blocks')
    return configtxt.set_block(text, BLOCK, [])


def _check_config(text):
    # Includes and alternate boot layouts need explicit resolution. Other
    # overlays (including the camera) are retained; our block is appended last.
    for raw in _outside(text).splitlines():
        line = raw.split('#', 1)[0].strip()
        if re.match(r'include\s', line):
            raise Error('Pi 5 EEE: resolve config.txt includes before changing the boot policy')
        if line.startswith(('device_tree=', 'os_prefix=', 'overlay_prefix=')):
            key, value = line.split('=', 1)
            if ((key == 'device_tree' and value != BASE)
                    or (key == 'overlay_prefix' and value != 'overlays/')
                    or (key == 'os_prefix' and value)):
                raise Error(f'Pi 5 EEE: custom {key} is not supported by the boot policy')
        if (line.startswith(('dtoverlay=', 'device_tree_overlay='))
                and re.split('[:,]', line.partition('=')[2], maxsplit=1)[0].strip() == NAME):
            raise Error('Pi 5 EEE: an external directive uses the managed overlay; resolve it first')


def _target(ctx, directory, iface):
    props = overlaymap.properties(ctx.plan.read(f'{directory}/{BASE}') or b'')
    if props is None:
        raise Error(f'Pi 5 EEE: valid {BASE} is required on the target boot partition')
    try:
        if b'raspberrypi,5-model-b' not in props[('/', 'compatible')].split(b'\0'):
            raise ValueError
        mac = props[('/aliases', 'ethernet0')].removesuffix(b'\0').decode('ascii')
        if b'raspberrypi,rp1-gem' not in props[(mac, 'compatible')].split(b'\0'):
            raise ValueError
        handle = props[(mac, 'phy-handle')]
        if len(handle) != 4:
            raise ValueError
        matches = [p for (p, name), value in props.items() if name == 'phandle' and value == handle]
        if len(matches) != 1:
            raise ValueError
        phy = matches[0]
        if (not phy.startswith(mac + '/') or props[(phy, 'reg')] != struct.pack('>I', 1)
                or props[('/__symbols__', 'phy1')] != phy.encode() + b'\0'):
            raise ValueError
    except (KeyError, UnicodeError, ValueError):
        raise Error('Pi 5 EEE: boot DTB does not identify the expected onboard RP1 PHY') from None
    if ctx.target.live:
        root = f'/sys/class/net/{iface}'
        identifier = (ctx.target.read(f'{root}/phydev/phy_id') or b'').strip().lower()
        actual = ctx.target.realpath(f'{root}/phydev/of_node')
        driver = ctx.target.realpath(f'{root}/device/driver')
        if (identifier != b'0x600d84a2' or not driver.endswith('/drivers/macb')
                or actual != '/sys/firmware/devicetree/base' + phy):
            raise Error(f'Pi 5 EEE: {iface} is not the verified onboard macb/BCM54213PE port')
    elif iface not in ('eth0', 'end0'):
        raise Error('Pi 5 EEE: offline onboard interface must be eth0 or end0')
    return phy


def read(ctx, iface):
    if ctx.board.id != 'RPI5':
        return None
    directory = _directory(ctx)
    if directory is None:
        return None
    text = ctx.plan.read_text(f'{directory}/config.txt') or ''
    start, end = configtxt._markers(BLOCK)
    if start not in text or end not in text:
        return None
    try:
        _outside(text)
    except Error:
        ctx.plan.note('Pi 5 EEE boot policy has incomplete/duplicate blocks; not imported')
        return None
    block = text.partition(start)[2].partition(end)[0]
    if ([line.strip() for line in block.splitlines() if line.strip()] ==
            ['[all]', f'[{ctx.board.filter}]', 'dtoverlay=', f'dtoverlay={NAME}', 'dtoverlay=', '[all]']
            and _owned(ctx.plan.read(f'{directory}/overlays/{NAME}.dtbo'))):
        try:
            if _owned(ctx.plan.read(f'{directory}/overlays/{NAME}.dtbo')) == _target(ctx, directory, iface):
                return 'DISABLE'
        except Error:
            ctx.plan.note('Pi 5 EEE boot policy cannot be mapped to this interface; check boot layout/PHY')
    return None


def setup(ctx, iface, mode):
    if ctx.board.id != 'RPI5':
        return False
    directory = _directory(ctx)
    if directory is None:
        return False
    config = f'{directory}/config.txt'
    path = f'{directory}/overlays/{NAME}.dtbo'
    text = ctx.plan.read_text(config) or ''
    managed = configtxt._markers(BLOCK)[0] in text or ctx.plan.exists(path)
    if mode == 'REMOVE' and not managed:
        return False
    if ctx.target.live:
        # A boot file is global, whereas a user may select a USB NIC. Never
        # create/remove the onboard policy for a different live interface.
        root = f'/sys/class/net/{iface}'
        if ((ctx.target.read(f'{root}/phydev/phy_id') or b'').strip().lower() != b'0x600d84a2'
                or not ctx.target.exists(f'{root}/device/driver')
                or not ctx.target.realpath(f'{root}/device/driver').endswith('/drivers/macb')):
            return False
    elif iface not in ('eth0', 'end0'):
        return False
    # Only this model's onboard port uses this backend. USB NICs and other
    # Pi 5-family models retain the generic ethtool path.
    if not managed and (not ctx.plan.exists(f'{directory}/{BASE}')
                        or ctx.target.live and (ctx.target.read('/proc/device-tree/model') or b'').startswith(
                            (b'Raspberry Pi 500', b'Raspberry Pi Compute Module 5'))):
        return False
    if mode == 'ENABLE':
        if managed:
            raise Error('Pi 5 EEE: remove the managed boot-disable policy before requesting Enable; removal alone does not enable EEE')
        return False
    if mode not in ('DISABLE', 'REMOVE'):
        return False
    _check_config(text)
    if ctx.plan.readlink(path) is not None or ctx.plan.exists(path) and not _owned(ctx.plan.read(path)):
        raise Error(f'Pi 5 EEE: {path} is not an intact board-tune overlay')
    if mode == 'REMOVE':
        updated = configtxt.set_block(text, BLOCK, [])
        ctx.plan.remove(path)
    else:
        phy = _target(ctx, directory, iface)
        ctx.plan.write(path, overlay(phy))  # FAT boot partition: preserve mount metadata.
        lines = ['[all]', f'[{ctx.board.filter}]', 'dtoverlay=', f'dtoverlay={NAME}', 'dtoverlay=', '[all]']
        updated = configtxt.set_block(text, BLOCK, lines, at_end=True)
    if updated != text:
        ctx.plan.write(config, updated)
    if ctx.plan.changed(config, path):
        ctx.plan.note(f'Pi 5 EEE boot-disable policy {"removed" if mode == "REMOVE" else "saved"} for {iface}; '
                      'takes effect after reboot. The running link is unchanged; removal does not force EEE on.')
    return True
