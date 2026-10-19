"""FDT overlay-map aliases are verified from target data, not host assumptions."""

import shutil
import struct
import subprocess

import pytest

from board_tune.platforms.rpi import PLATFORM
from board_tune.platforms.rpi.overlaymap import properties, uart_aliases


def make_map(entries=(("uart0", "bcm2712", b"uart0-pi5\0"),)):
    """Construct small v17 test blobs with independent FDT header/sections."""
    names = b""
    offsets = {}
    for _, name, _ in entries:
        if name not in offsets:
            offsets[name] = len(names)
            names += name.encode() + b"\0"
    tree = struct.pack(">I", 1) + b"\0\0\0\0"
    for node, name, value in entries:
        node_name = node.encode() + b"\0"
        tree += struct.pack(">I", 1) + node_name + b"\0" * (-len(node_name) % 4)
        tree += struct.pack(">3I", 3, len(value), offsets[name]) + value + b"\0" * (-len(value) % 4)
        tree += struct.pack(">I", 2)
    tree += struct.pack(">2I", 2, 9)
    strings = 56 + len(tree)
    header = struct.pack(">10I", 0xD00DFEED, strings + len(names), 56, strings,
                         40, 17, 16, 0, len(names), len(tree))
    return header + b"\0" * 16 + tree + names


def test_mapping_requires_exact_node_cpu_and_value():
    pi4, pi5 = PLATFORM.boards
    raw = make_map((("uart0", "bcm2712", b"uart0-pi5\0"),
                    ("uart2", "bcm2711", b"uart2-pi5\0"),
                    ("other", "bcm2712", b"uart3-pi5\0")))
    assert uart_aliases(raw, pi5) == {"uart0": "uart0-pi5"}
    assert uart_aliases(raw, pi4) == {}


@pytest.mark.parametrize("value", [b"uart0-pi5", b"uart1-pi5\0", b"uart0-pi5\0junk", b""])
def test_invalid_mapping_value_is_not_used(value):
    assert uart_aliases(make_map((("uart0", "bcm2712", value),)), PLATFORM.boards[1]) == {}


@pytest.mark.parametrize("raw", [b"", b"invalid", make_map()[:39], make_map()[:-1]])
def test_truncated_or_non_fdt_maps_are_ignored(raw):
    assert properties(raw) is None
    assert uart_aliases(raw, PLATFORM.boards[1]) == {}


@pytest.mark.parametrize("offset,value", [(0, 0), (8, 0xFFFFFFFC), (12, 0xFFFFFFF0),
                                         (20, 16), (24, 18), (32, 0xFFFFFFFF), (56, 99)])
def test_malformed_structure_or_header_is_ignored(offset, value):
    raw = bytearray(make_map())
    struct.pack_into(">I", raw, offset, value)
    assert properties(raw) is None


def test_duplicate_nodes_are_rejected():
    assert properties(make_map((("uart0", "bcm2712", b"uart0-pi5\0"),
                                ("uart0", "bcm2711", b"")))) is None


def test_native_dtc_output_is_accepted():
    compiler = shutil.which("dtc")
    if not compiler:
        pytest.skip("optional native dtc validation")
    result = subprocess.run([compiler, "-I", "dts", "-O", "dtb"],
                            input=b'/dts-v1/; / { uart0 { bcm2712 = "uart0-pi5"; }; };',
                            capture_output=True, check=True)
    assert uart_aliases(result.stdout, PLATFORM.boards[1]) == {"uart0": "uart0-pi5"}
