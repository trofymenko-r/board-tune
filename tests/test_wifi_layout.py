"""Display columns are measured in terminal cells, including colored signals."""

from types import SimpleNamespace

import pytest

from board_tune import wifi
from board_tune.wifi_menu import KeyValueRows, SignalText, WifiManager, cells, signal_bar


@pytest.mark.parametrize('width', [46, 66, 82])
def test_property_values_share_a_column_across_widths(width):
    source = [('SSID', 'Lab: 5G'), ('Interface (empty: any)', 'wlan0'),
              ('Password (empty: keep stored)', '(hidden)'),
              ('IPv4 address / prefix', '192.168.1.130/24'),
              ('Power saving', 'Keep existing setting'), 'Save only', 'Connect now']
    rows = KeyValueRows(source)
    rendered = rows.render(width)
    starts = [cells(line[:line.index(value)]) for line, (_, value) in zip(rendered, source[:5])]
    assert len(set(starts)) == 1
    assert all(cells(line) <= width for line in rendered)
    assert rendered[-2:] == ['Save only', 'Connect now']
    assert rows.rows == source  # presentation never edits the underlying values


def test_wide_and_combining_labels_use_display_cells():
    rendered = KeyValueRows([('界面', 'wlan0'), ('Cafe\u0301', 'Lab')]).render(46)
    assert cells(rendered[0][:rendered[0].index('wlan0')]) == cells(rendered[1][:rendered[1].index('Lab')])


def test_reflow_preserves_full_values_and_signal_color_spans():
    bar = signal_bar(58)
    value = SignalText(f'{bar}; 5 GHz', bar.signals)
    rows = KeyValueRows([('Interface', 'wlan0'), ('Signal', value),
                        ('Connection', 'A long Wi-Fi network name ' * 3)])
    narrow = rows.render(46)
    wide = rows.render(150)
    assert narrow[-1].endswith('…')
    assert wide[-1].endswith(rows.rows[-1][1])
    for rendered in (narrow, wide):
        line = rendered[1]
        start, length, percent = line.signals[0]
        assert (length, percent) == (3, 58)
        assert line[start:start + length] == '▂▄▆'
        assert ' 58%; 5 GHz' in line


def test_status_parser_preserves_escaped_connection_names_and_indexed_addresses():
    rows = wifi.status_fields('GENERAL.STATE:100 (connected)\n'
                              r'GENERAL.CONNECTION:Lab\: 5G' '\n'
                              'IP4.ADDRESS[1]:192.168.1.130/24\n'
                              'IP4.GATEWAY:192.168.1.254\n'
                              'IP4.DNS[1]:1.1.1.1\nIP4.DNS[2]:8.8.8.8\n')
    assert rows == [('State', '100 (connected)'), ('Connection', 'Lab: 5G'),
                    ('IPv4 address [1]', '192.168.1.130/24'), ('Gateway', '192.168.1.254'),
                    ('DNS [1]', '1.1.1.1'), ('DNS [2]', '8.8.8.8')]


def test_live_status_is_returned_as_properties(target, monkeypatch):
    target.live = True
    monkeypatch.setattr(target, 'run', lambda argv, input=None: 'GENERAL.STATE:100 (connected)\n')
    assert wifi.device_status(target, 'wlan0') == [('State', '100 (connected)')]


def test_editor_masks_password_before_building_rows(target):
    manager = WifiManager(target)
    calls = []
    manager.dialog = SimpleNamespace(choose=lambda *args: calls.append(args))
    manager.edit(initial=wifi.Editor(ssid='Lab', password='NeverDisplayThisPassword'))
    title, lines, items = calls[0]
    assert title == 'Edit Wi-Fi profile' and isinstance(items, KeyValueRows)
    assert len(items) == 15 and items.rows[-2:] == ['Save only', 'Connect now']
    assert ('Password (empty: keep stored)', '(hidden)') in items.rows
    assert 'NeverDisplayThisPassword' not in repr(items)
    assert 'NeverDisplayThisPassword' not in '\n'.join(items.render(82))
    assert not manager.session.edits
