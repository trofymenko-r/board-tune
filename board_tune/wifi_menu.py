"""Wi-Fi dialogs in the same curses session as kernel-style menuconfig."""

from __future__ import annotations

import curses
import os
import threading
import textwrap
import time
import unicodedata
from dataclasses import dataclass

from board_tune import wifi
from board_tune.errors import Error
from board_tune.menu import geometry


def visible(text):
    return "".join(c if unicodedata.category(c) not in ("Cc", "Cf") else "?" for c in str(text))


def cells(text):
    """Terminal columns, including wide letters and combining accents."""
    return sum(0 if unicodedata.category(c) in ('Mn', 'Me') else
               2 if unicodedata.east_asian_width(c) in ('W', 'F') else 1
               for c in visible(text))


def clipped(text, width):
    text = visible(text)
    if cells(text) <= width:
        return text
    result, used = '', 0
    for char in text:
        size = cells(char)
        if used + size > width - 1:
            break
        result += char
        used += size
    return result + '…' if width else ''


class SignalText(str):
    """Text plus signal spans in terminal columns; no ANSI escapes in data."""

    def __new__(cls, text, signals=()):
        value = super().__new__(cls, text)
        value.signals = tuple(signals)
        return value


SIGNAL_GLYPHS = '▂▄▆█'


def signal_bar(percent):
    percent = max(0, min(100, int(percent)))
    filled = (percent + 24) // 25
    return SignalText(SIGNAL_GLYPHS[:filled] + '·' * (4 - filled) + f' {percent:3}%',
                      [(0, filled, percent)])


@dataclass
class NetworkTable:
    headers: tuple
    rows: list
    minimums: tuple
    limits: tuple
    right: tuple = ()

    def render(self, width):
        gap = '  ' if sum(self.minimums) + 2 * (len(self.minimums) - 1) <= width else ' '
        widths = [max(low, min(high, max(cells(row[i]) for row in [self.headers, *self.rows])))
                  for i, (low, high) in enumerate(zip(self.minimums, self.limits))]
        while sum(widths) + len(gap) * (len(widths) - 1) > width:
            index = max(range(len(widths)), key=lambda i: widths[i] - self.minimums[i])
            if widths[index] == self.minimums[index]:
                break
            widths[index] -= 1

        def row(values):
            fields, signals, offset = [], [], 0
            for i, (value, size) in enumerate(zip(values, widths)):
                text = clipped(value, size)
                padding = ' ' * (size - cells(text))
                fields.append(padding + text if i in self.right else text + padding)
                for start, length, percent in getattr(value, 'signals', ()):
                    if start + length <= cells(text):
                        signals.append((offset + (len(padding) if i in self.right else 0) + start,
                                        length, percent))
                offset += size + len(gap)
            return SignalText(gap.join(fields), signals)

        return row(self.headers), gap.join('─' * size for size in widths), [row(r) for r in self.rows]


class Dialogs:
    def __init__(self, ui):
        self.ui = ui
        self.positions = {}
        self.signal_styles = {}

    def draw_line(self, win, row, left, text, width, style):
        ui = self.ui
        shown = clipped(text, width)
        ui._safe_addstr(win, row, left, shown, style)
        for start, length, percent in getattr(text, 'signals', ()):
            if not length or start + length > cells(shown):
                continue
            color = curses.COLOR_GREEN if percent >= 70 else curses.COLOR_YELLOW if percent >= 40 else curses.COLOR_RED
            key = style, color
            if key not in self.signal_styles:
                attr = style
                if (curses.has_colors() and 'NO_COLOR' not in os.environ
                        and 'monochrome' not in os.environ.get('MENUCONFIG_STYLE', '').split()):
                    try:
                        _, background = curses.pair_content(curses.pair_number(style))
                        attr = ui._style_attr(color, background, style & ~curses.A_COLOR)
                    except (curses.error, ValueError):
                        pass
                self.signal_styles[key] = attr
            ui._safe_addstr(win, row, left + start, SIGNAL_GLYPHS[:length], self.signal_styles[key])

    def choose(self, title, lines, items, ready=None, deadline=None, countdown=None, table=None):
        """Return an item index or None. Every modal handles scrolling/resize."""
        ui, scroll, back = self.ui, 0, False
        count = len(items) + (len(table.rows) if table else 0)
        selected = min(self.positions.get(title, 0), max(0, count - 1))
        while True:
            if ready is not None and ready():
                return -1
            if deadline is not None and time.monotonic() >= deadline:
                return None
            ui._stdscr.erase()
            ui._stdscr.bkgd(" ", ui._style["path"])
            ui._stdscr.noutrefresh()
            rows, columns = ui._stdscr.getmaxyx()
            height, width, top, left = geometry(rows, columns, len(lines) + count + (2 if table else 0))
            if rows < 14 or columns < 60:
                ui._safe_addstr(ui._stdscr, 1, 0, "Resize terminal to at least 60 x 14")
                ui._stdscr.refresh()
                key = ui._getch_compat(ui._stdscr)
                if key in ("q", "\x1b"):
                    return None
                continue
            win = curses.newwin(height, width, top, left)
            win.keypad(True)
            win.bkgd(" ", ui._style["body"])
            win.erase()
            ui._draw_frame(win, visible(title))
            if deadline is not None:
                seconds = countdown() if countdown else max(0, int(deadline - time.monotonic()))
                ui._safe_addstr(win, 1, 3, f"Automatic revert in {seconds}s")
            if table:
                header, separator, data = table.render(width - 6)
                shown_lines = [*lines[:max(0, height - 10)], header, separator]
                shown_items = [*data, *items]
            else:
                shown_lines = lines[:height - 8]
                shown_items = items
            content = len(shown_lines)
            for row, text in enumerate(shown_lines, 2):
                self.draw_line(win, row, 3, text, width - 6, ui._style['body'])
            start = 3 + content
            space = max(1, height - start - 4)
            scroll = max(0, min(scroll, selected))
            if selected >= scroll + space:
                scroll = selected - space + 1
            for row, text in enumerate(shown_items[scroll:scroll + space], start):
                style = ui._style["selection" if row - start + scroll == selected and not back else "body"]
                self.draw_line(win, row, 3, text, width - 6, style)
            if scroll:
                ui._safe_addstr(win, start - 1, width - 4, "^")
            if scroll + space < count:
                ui._safe_addstr(win, height - 4, width - 4, "v")
            ui._safe_addstr(win, height - 2, 4, "< Select >", ui._style["body" if back else "selection"])
            ui._safe_addstr(win, height - 2, 19, "< Back >", ui._style["selection" if back else "body"])
            win.noutrefresh()
            curses.doupdate()
            if ready is not None or deadline is not None:
                win.timeout(100)
            key = ui._getch_compat(win)
            if key in ("q", "Q", "\x1b"):
                return None
            if key in ("\t", curses.KEY_BTAB, curses.KEY_LEFT, curses.KEY_RIGHT):
                back = not back
            elif key in (curses.KEY_DOWN, "j") and count:
                selected = (selected + 1) % count
                back = False
            elif key in (curses.KEY_UP, "k") and count:
                selected = (selected - 1) % count
                back = False
            elif key in ("\n", curses.KEY_ENTER):
                self.positions[title] = selected
                return None if back or not count else selected

    def message(self, title, text):
        rows, columns = self.ui._stdscr.getmaxyx()
        width = max(1, geometry(rows, columns, 1)[1] - 6)
        lines = [part for line in str(text).splitlines()
                 for part in (textwrap.wrap(visible(line), width, break_on_hyphens=False) or [''])]
        self.choose(title, lines, ["OK"])

    def input(self, title, initial='', secret=False):
        ui, text = self.ui, list(initial)
        while True:
            rows, cols = ui._stdscr.getmaxyx()
            if rows < 14 or cols < 60:
                self.message('Terminal size', 'Resize terminal to at least 60 x 14')
                continue
            h, w, y, x = geometry(rows, cols, 4)
            win = curses.newwin(h, w, y, x)
            win.keypad(True)
            win.bkgd(' ', ui._style['body'])
            ui._draw_frame(win, title)
            shown = '*' * len(text) if secret else visible(''.join(text))
            ui._safe_addstr(win, 3, 3, shown[-(w - 7):], ui._style['edit'])
            ui._safe_addstr(win, h - 3, 3, 'Enter: accept   Esc: cancel   Ctrl-U: clear')
            win.refresh()
            key = ui._getch_compat(win)
            if key == '\x1b':
                return None
            if key in ('\n', curses.KEY_ENTER):
                return ''.join(text)
            if key == '\x15':
                text.clear()
            elif key in (curses.KEY_BACKSPACE, '\x7f', '\b'):
                if text:
                    text.pop()
            elif isinstance(key, str) and key.isprintable() and len(text) < 1024:
                text.append(key)

    def work(self, title, function):
        result = []
        def run():
            try:
                result.append((True, function()))
            except Exception as error:
                result.append((False, error))
        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        self.choose(title, ['Please wait. Esc: cancel view'], ['Cancel'], ready=lambda: bool(result))
        if not result:
            return None
        if not result[0][0]:
            raise result[0][1]
        return result[0][1]


class WifiManager:
    def __init__(self, target, allow_live=True):
        self.target = target
        self.allow_live = allow_live
        self.iface = 'wlan0'
        self.session = wifi.Session(target)
        self.points = []

    def refresh(self):
        self.notes = []
        self.profiles = self.session.overlay(wifi.inventory(self.target, self.notes))

    def show(self, ui):
        self.dialog = Dialogs(ui)
        while True:
            try:
                self.refresh()
                adapters = sorted({p.active or p.iface for p in self.profiles if p.active or p.iface})
                try:
                    if self.target.live:
                        rows = wifi.nmcli(self.target, '-f', 'DEVICE,TYPE', 'device', 'status')
                        adapters = sorted(set(adapters) | {r[0] for line in rows.splitlines()
                                                          if len(r := wifi.fields(line)) == 2 and r[1] == 'wifi'})
                except Error:
                    self.notes.append('Live adapter list unavailable; saved profiles remain accessible')
                if adapters and self.iface not in adapters:
                    self.iface = adapters[0]
                try:
                    state = wifi.device_status(self.target, self.iface)
                    if self.target.live:
                        self.points = wifi.scan(self.target, self.iface, False)
                        current = [a for a in self.points if a.active]
                        if current:
                            bar = signal_bar(current[0].signal)
                            state.insert(2, SignalText(f'Signal: {bar}; {wifi.band(current[0].freq)}',
                                                      [(8 + start, length, percent) for start, length, percent in bar.signals]))
                except Error:
                    state = ['Live state unavailable (adapter absent or NetworkManager not running)']
                action = self.dialog.choose('Wi-Fi', [f'Interface: {self.iface}', *state[:5], *self.notes[:1]],
                                            [f'Saved networks ({len(self.profiles)}) --->',
                                             'Available networks --->', 'Add network / hidden SSID --->',
                                             'Select interface --->', 'Radio / boot country / batch options --->',
                                             'Discard Wi-Fi drafts',
                                             f'Refresh (pending profile changes: {len(self.session.edits)})'])
                if action is None:
                    return False
                if action == 0:
                    self.saved()
                elif action == 1:
                    self.nearby()
                elif action == 2:
                    self.edit()
                elif action == 3:
                    index = self.dialog.choose('Wi-Fi interface', [], adapters)
                    if index is not None:
                        self.iface = adapters[index]
                elif action == 4:
                    try:
                        radio = wifi.radio_status(self.target)
                    except Error:
                        radio = 'Radio status unavailable'
                    if self.dialog.choose('Radio and boot settings',
                                          [radio, 'Country changes apply after reboot.'],
                                          ['Back', 'Open boot country / batch profile options']) == 1:
                        return True
                elif action == 5 and self.session.edits:
                    if self.dialog.choose('Discard Wi-Fi drafts',
                                          ['Discard pending profile edits/deletions? Live confirmed changes stay applied.'],
                                          ['Cancel', 'Discard Wi-Fi drafts']) == 1:
                        self.session.edits.clear()
                        self.session.originals.clear()
            except Error as error:
                self.dialog.message('Wi-Fi', error)
                return False

    def saved(self):
        while True:
            self.profiles.sort(key=lambda p: (not bool(p.active),
                               not any(wifi.matching([p], a, self.iface) for a in self.points), p.name, p.uuid))
            rows = []
            for p in self.profiles:
                nearby = any(wifi.matching([p], a, self.iface) for a in self.points)
                state = 'connected' if p.active else 'nearby' if nearby else 'not seen'
                rows.append(('*' if p.active else '', p.ssid, p.uuid[:8] or '-', state,
                             'yes' if p.autoconnect else 'no', str(p.priority),
                             'yes' if p.path in self.session.edits else ''))
            table = NetworkTable(('', 'SSID', 'UUID', 'State', 'Auto', 'Prio', 'Draft'), rows,
                                 (1, 6, 6, 8, 4, 4, 5), (1, 32, 8, 9, 4, 4, 5), right=(5,))
            index = self.dialog.choose('Saved Wi-Fi networks',
                                       ['All adapters. Availability is cached; offline: unknown. Enter: actions'],
                                       [], table=table)
            if index is None:
                return
            try:
                self.profile_actions(self.profiles[index])
            except Error as error:
                self.dialog.message('Wi-Fi profile', error)
            self.refresh()

    def profile_actions(self, profile):
        action = self.dialog.choose(profile.ssid,
                                    [f'Profile: {profile.name}', f'UUID: {profile.uuid or "(not assigned)"}',
                                     f'Security: {profile.auth}; mode: {profile.mode}',
                                     f'Active: {profile.active or "no"}; interface: {profile.iface or "any"}'],
                                    ['Details', 'Connect now', 'Edit profile', 'Forget network'])
        if action == 0:
            from board_tune.hooks.network import _sections
            data = _sections(profile.text)
            lines = [f'UUID: {profile.uuid}', f'Autoconnect: {profile.autoconnect}', f'Priority: {profile.priority}']
            wireless = data.get('wifi', data.get('802-11-wireless', {}))
            mode = wifi.powersave_mode(wireless)
            raw = wireless.get('powersave', [''])[0]
            label = wifi.POWERSAVE_LABELS[mode] if mode != 'keep' else f'Unspecified/kept ({raw or "default"})'
            lines.append(f'Power saving (profile): {label}')
            for key in ('method', 'address1', 'gateway', 'dns'):
                lines.append(f'IPv4 {key}: {data.get("ipv4", {}).get(key, [""])[0]}')
            if profile.active:
                lines += wifi.device_status(self.target, profile.active)
                lines.append(f'Power saving (driver): {wifi.power_status(self.target, profile.active)}')
            self.dialog.message(profile.ssid, '\n'.join(lines))
        elif action == 1:
            self.live(profile)
        elif action == 2:
            self.edit(profile)
        elif action == 3:
            if profile.active:
                self.live(profile, 'forget')
            elif self.dialog.choose('Forget network',
                                    [f'Remove saved profile {profile.name}?', f'UUID: {profile.uuid}',
                                     'Deletion will be reviewed/applied after leaving menuconfig.'],
                                    ['Cancel', 'Forget on apply']) == 1:
                self.session.forget(profile)

    def nearby(self):
        if not self.target.live:
            self.dialog.message('Available networks', 'Scanning requires a running board; saved profiles can still be edited.')
            return
        while True:
            cached = False
            try:
                points = self.dialog.work('Scanning on ' + self.iface,
                                          lambda: wifi.scan(self.target, self.iface))
                if points is None:
                    return
                self.points = points
            except Error as error:
                self.dialog.message('Scan failed; showing cached results', error)
                cached = True
                try:
                    self.points = wifi.scan(self.target, self.iface, False)
                except Error:
                    self.points = []
            grouped = wifi.groups(self.points)
            rows = []
            for aps in grouped:
                best = aps[0]
                saved = bool(wifi.matching(self.profiles, best, self.iface))
                rows.append(('*' if any(a.active for a in aps) else '', best.ssid or '<hidden>',
                             signal_bar(max(a.signal for a in aps)), best.security if best.security not in ('', '--') else 'open',
                             '/'.join(sorted({wifi.band(a.freq) for a in aps})), 'yes' if saved else 'no', str(len(aps))))
            table = NetworkTable(('', 'SSID', 'Signal', 'Security', 'Band', 'Saved', 'APs'), rows,
                                 (1, 6, 9, 8, 4, 5, 3), (1, 32, 9, 20, 20, 5, 3), right=(2, 6))
            index = self.dialog.choose('Available Wi-Fi networks',
                                       [f'Adapter: {self.iface}; {"cached" if cached else "fresh scan"}',
                                        'No networks returned' if not grouped else 'Enter: network actions'],
                                       ['Rescan'], table=table)
            if index is None:
                return
            if index == len(grouped):
                continue
            aps = grouped[index]
            best = aps[0]
            action = self.dialog.choose(best.ssid or '<hidden>', [], ['Select saved profile / add', 'Access point details'])
            if action == 1:
                table = NetworkTable(('BSSID', 'Channel', 'Band', 'Signal'),
                                     [(a.bssid, a.channel, wifi.band(a.freq), signal_bar(a.signal)) for a in aps],
                                     (17, 7, 7, 9), (17, 7, 7, 9), right=(1, 3))
                self.dialog.choose('Access points', [], [], table=table)
            elif action == 0:
                matches = wifi.matching(self.profiles, best, self.iface)
                if matches:
                    selection = self.dialog.choose('Select saved profile', [], [f'{p.name} [{p.uuid}]' for p in matches])
                    if selection is not None:
                        self.profile_actions(matches[selection])
                else:
                    auth = 'open' if not best.security or best.security == '--' else 'sae' if best.security == 'WPA3' else 'wpa-psk'
                    if any(word in best.security for word in ('802.1X', 'WEP', 'Enterprise')):
                        self.dialog.message('Unsupported security', 'Creating Enterprise/WEP credentials is not supported here.')
                    else:
                        self.edit(initial=wifi.Editor(ssid=best.ssid, iface=self.iface, auth=auth, hidden=not bool(best.ssid)))
            self.refresh()

    def edit(self, profile=None, initial=None):
        try:
            if profile and not profile.editable:
                raise Error('This profile is visible but its security/mode/storage cannot be edited here')
            editor = wifi.Editor.from_profile(profile) if profile else initial or wifi.Editor(iface=self.iface)
            while True:
                values = [('SSID', 'ssid'), ('Profile name', 'name'), ('Interface (empty: any)', 'iface'),
                          ('Security', 'auth'), ('Password (empty: keep stored)', 'password'),
                          ('Hidden SSID', 'hidden'), ('Autoconnect', 'autoconnect'), ('Priority', 'priority'),
                          ('IPv4 mode', 'method'), ('IPv4 address / prefix', 'address'),
                          ('Gateway (static mode)', 'gateway'), ('DNS servers', 'dns'),
                          ('Power saving', 'powersave')]
                labels = []
                for label, key in values:
                    value = getattr(editor, key)
                    if key == 'password':
                        value = '(hidden)'
                    elif key == 'powersave':
                        value = wifi.POWERSAVE_LABELS[value]
                    labels.append(f'{label}: {value}')
                action = self.dialog.choose('Edit Wi-Fi profile', ['Save only: review/apply when leaving menuconfig'],
                                            [*labels, 'Save only', 'Connect now'])
                if action is None:
                    return
                if action in (len(values), len(values) + 1):
                    previous = dict(self.session.edits), dict(self.session.originals)
                    try:
                        updated = self.session.save(editor, profile)
                        if action == len(values) + 1 and not self.live(updated):
                            self.session.edits, self.session.originals = previous
                            continue
                    except Error as error:
                        if action == len(values) + 1:
                            self.session.edits, self.session.originals = previous
                        self.dialog.message('Wi-Fi profile', error)
                        continue
                    return
                label, key = values[action]
                value = getattr(editor, key)
                if key in ('hidden', 'autoconnect'):
                    setattr(editor, key, not value)
                elif key in ('auth', 'method', 'powersave'):
                    choices = (tuple(wifi.POWERSAVE_LABELS) if key == 'powersave' else
                               ('open', 'wpa-psk', 'sae') if key == 'auth' else ('auto', 'manual'))
                    labels = [wifi.POWERSAVE_LABELS[c] for c in choices] if key == 'powersave' else list(choices)
                    index = self.dialog.choose(label, ['Applies to this profile on its next connection.'] if key == 'powersave' else [], labels)
                    if index is not None:
                        setattr(editor, key, choices[index])
                else:
                    text = self.dialog.input(label, '' if key == 'password' else str(value), secret=key == 'password')
                    if text is not None:
                        if key == 'priority':
                            try:
                                value = int(text)
                            except ValueError:
                                self.dialog.message('Priority', 'Enter an integer from -999 to 999')
                                continue
                        else:
                            value = (text or None) if key == 'password' else text
                        setattr(editor, key, value)
        except Error as error:
            self.dialog.message('Wi-Fi profile', error)

    def live(self, profile, action='connect'):
        if not self.allow_live:
            self.dialog.message('Dry run', 'Live Wi-Fi actions are disabled by --dry-run. Save only prepares a preview.')
            return False
        if not self.target.live:
            self.dialog.message('Live Wi-Fi action', 'Offline target: use Save only; live actions require a running board.')
            return False
        access = getattr(self.target, 'access_interface', lambda: None)()
        iface = profile.active or profile.iface or self.iface
        if self.dialog.choose('Forget active network' if action == 'forget' else 'Connect now',
                              [f'Profile: {profile.name}', f'UUID: {profile.uuid}',
                               f'Affected Wi-Fi adapter: {iface}; SSH adapter: {access or "unknown/local"}',
                               'This disconnects Wi-Fi.' if action == 'forget' else 'Switch Wi-Fi now; other menu edits stay pending.',
                               'Without confirmation, the board reverts within 60 seconds.'],
                              ['Cancel', 'Disconnect and forget now' if action == 'forget' else 'Connect now']) != 1:
            return False
        transaction = wifi.LiveTransaction(self.session, profile, iface, action)
        transaction.start()
        overall = time.monotonic() + 80
        state = {'state': 'starting', 'remaining': 60}
        def wait_for(states, title):
            last = [0]
            def poll():
                nonlocal state
                now = time.monotonic()
                if now - last[0] < 0.4:
                    return False
                last[0] = now
                try:
                    state = transaction.status()
                except Error:
                    # SSH retries the original destination; the worker/NM timer
                    # continue on the board while that address is unreachable.
                    return False
                return state['state'] in states
            selected = self.dialog.choose(title,
                                          ['Board-owned 60s rollback is active.',
                                           'If SSH drops, retrying the original address.',
                                           'Esc / Revert requests the previous connection.'],
                                          ['Revert'], ready=poll, deadline=overall,
                                          countdown=lambda: state.get('remaining', 60))
            return selected == -1
        def revert():
            try:
                transaction.decide(False)
            except Error:
                pass  # The board will revert without client confirmation.
        if wait_for(('confirm', *transaction.TERMINAL), 'Changing Wi-Fi') and state['state'] == 'confirm':
            decision = self.dialog.choose('Keep Wi-Fi change?', state.get('details', '').splitlines(),
                                          ['Revert', 'Keep change'],
                                          deadline=time.monotonic() + max(0, state['remaining'] - 2))
            if decision == 1:
                try:
                    transaction.decide(True)
                except Error:
                    revert()
            else:
                revert()
        elif state['state'] not in transaction.TERMINAL:
            revert()
        if state['state'] not in transaction.TERMINAL:
            wait_for(transaction.TERMINAL, 'Waiting for Wi-Fi result')
        if state['state'] in transaction.TERMINAL:
            transaction.finish(state)
            self.dialog.message('Wi-Fi: ' + state['state'],
                                (state.get('error') or ('Change applied immediately.' if state['state'] == 'kept'
                                                       else 'Previous configuration restored.'))
                                + f'\nFile backup: {transaction.backup_id}')
        else:
            self.dialog.message('Wi-Fi status unavailable',
                                f'Final result could not be read. Reconnect to inspect Wi-Fi.\n'
                                f'The board reverts unless Keep was accepted.\n'
                                f'Recovery backup: {transaction.backup_id}\nReconnect to check the result.')
        return state['state'] == 'kept'
