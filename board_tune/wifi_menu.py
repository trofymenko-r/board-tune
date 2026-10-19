"""Wi-Fi dialogs in the same curses session as kernel-style menuconfig."""

from __future__ import annotations

import curses
import threading
import time
import unicodedata

from board_tune import wifi
from board_tune.errors import Error
from board_tune.menu import geometry


def visible(text):
    return "".join(c if unicodedata.category(c) not in ("Cc", "Cf") else "?" for c in str(text))


class Dialogs:
    def __init__(self, ui):
        self.ui = ui
        self.positions = {}

    def choose(self, title, lines, items, ready=None, deadline=None):
        """Return an item index or None. Every modal handles scrolling/resize."""
        ui, scroll, back = self.ui, 0, False
        selected = min(self.positions.get(title, 0), max(0, len(items) - 1))
        while True:
            if ready is not None and ready():
                return -1
            if deadline is not None and time.monotonic() >= deadline:
                return None
            ui._stdscr.erase()
            ui._stdscr.bkgd(" ", ui._style["path"])
            ui._stdscr.noutrefresh()
            rows, columns = ui._stdscr.getmaxyx()
            height, width, top, left = geometry(rows, columns, len(lines) + len(items))
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
                ui._safe_addstr(win, 1, 3, f"Automatic revert in {max(0, int(deadline - time.monotonic()))}s")
            content = min(len(lines), height - 8)
            for row, text in enumerate(lines[:content], 2):
                ui._safe_addstr(win, row, 3, visible(text)[:width - 6])
            start = 3 + content
            space = max(1, height - start - 4)
            scroll = max(0, min(scroll, selected))
            if selected >= scroll + space:
                scroll = selected - space + 1
            for row, text in enumerate(items[scroll:scroll + space], start):
                style = ui._style["selection" if row - start + scroll == selected and not back else "body"]
                ui._safe_addstr(win, row, 3, visible(text)[:width - 6], style)
            if scroll:
                ui._safe_addstr(win, start - 1, width - 4, "^")
            if scroll + space < len(items):
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
            elif key in (curses.KEY_DOWN, "j") and items:
                selected = (selected + 1) % len(items)
                back = False
            elif key in (curses.KEY_UP, "k") and items:
                selected = (selected - 1) % len(items)
                back = False
            elif key in ("\n", curses.KEY_ENTER):
                self.positions[title] = selected
                return None if back or not items else selected

    def message(self, title, text):
        self.choose(title, [], str(text).splitlines() + ["OK"])

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
    def __init__(self, target):
        self.target = target
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
                if self.target.live:
                    rows = wifi.nmcli(self.target, '-f', 'DEVICE,TYPE', 'device', 'status')
                    adapters = sorted(set(adapters) | {r[0] for line in rows.splitlines()
                                                      if len(r := wifi.fields(line)) == 2 and r[1] == 'wifi'})
                if adapters and self.iface not in adapters:
                    self.iface = adapters[0]
                state = wifi.device_status(self.target, self.iface)
                action = self.dialog.choose('Wi-Fi', [f'Interface: {self.iface}', *state[:4], *self.notes[:1]],
                                            [f'Saved networks ({len(self.profiles)}) --->',
                                             'Available networks --->', 'Add network / hidden SSID --->',
                                             'Select interface --->', 'Batch profile / boot country options --->',
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
                    return True
            except Error as error:
                self.dialog.message('Wi-Fi', error)
                return False

    def saved(self):
        while True:
            labels = [f'{"*" if p.active else " "} {p.ssid}  [{p.name}]  '
                      f'auto={"yes" if p.autoconnect else "no"} priority={p.priority} '
                      f'({p.active or p.iface or "any adapter"})' for p in self.profiles]
            index = self.dialog.choose('Saved Wi-Fi networks', ['* connected; all adapters; Enter: actions'], labels)
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
                                    ['Details', 'Edit profile', 'Forget network'])
        if action == 0:
            from board_tune.hooks.network import _sections
            data = _sections(profile.text)
            lines = [f'UUID: {profile.uuid}', f'Autoconnect: {profile.autoconnect}', f'Priority: {profile.priority}']
            for key in ('method', 'address1', 'gateway', 'dns'):
                lines.append(f'IPv4 {key}: {data.get("ipv4", {}).get(key, [""])[0]}')
            if profile.active:
                lines += wifi.device_status(self.target, profile.active)
            self.dialog.message(profile.ssid, '\n'.join(lines))
        elif action == 1:
            self.edit(profile)
        elif action == 2:
            if self.dialog.choose('Forget network',
                                  [f'Remove saved profile {profile.name}?', f'UUID: {profile.uuid}'],
                                  ['Cancel', 'Forget saved profile']) == 1:
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
            labels = []
            for aps in grouped:
                best = aps[0]
                saved = any(p.ssid == best.ssid and (not p.iface or p.iface == self.iface) for p in self.profiles)
                labels.append(f'{"*" if any(a.active for a in aps) else " "} {best.ssid or "<hidden>"} '
                              f'{max(a.signal for a in aps)}%  {best.security or "open"}  '
                              f'{"saved" if saved else "new"} ({len(aps)} AP)')
            index = self.dialog.choose('Available Wi-Fi networks',
                                       [f'Adapter: {self.iface}; {"cached" if cached else "fresh scan"}',
                                        'No networks returned' if not grouped else 'Enter: network actions'],
                                       [*labels, 'Rescan'])
            if index is None:
                return
            if index == len(grouped):
                continue
            aps = grouped[index]
            best = aps[0]
            action = self.dialog.choose(best.ssid or '<hidden>', [], ['Select saved profile / add', 'Access point details'])
            if action == 1:
                self.dialog.choose('Access points', [], [f'{a.bssid}  channel {a.channel}  {a.freq}  {a.signal}%' for a in aps])
            elif action == 0:
                matches = [p for p in self.profiles if p.ssid == best.ssid and (not p.iface or p.iface == self.iface)]
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
                          ('Gateway', 'gateway'), ('DNS servers', 'dns')]
                labels = [f'{label}: {"(hidden)" if key == "password" else getattr(editor, key)}' for label, key in values]
                action = self.dialog.choose('Edit Wi-Fi profile', ['Save only: review/apply when leaving menuconfig'],
                                            [*labels, 'Save only'])
                if action is None:
                    return
                if action == len(values):
                    try:
                        self.session.save(editor, profile)
                    except Error as error:
                        self.dialog.message('Wi-Fi profile', error)
                        continue
                    return
                label, key = values[action]
                value = getattr(editor, key)
                if key in ('hidden', 'autoconnect'):
                    setattr(editor, key, not value)
                elif key in ('auth', 'method'):
                    choices = ('open', 'wpa-psk', 'sae') if key == 'auth' else ('auto', 'manual')
                    index = self.dialog.choose(label, [], list(choices))
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
