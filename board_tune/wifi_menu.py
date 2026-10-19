"""Wi-Fi dialogs in the same curses session as kernel-style menuconfig."""

from __future__ import annotations

import curses
import unicodedata

from board_tune import wifi
from board_tune.errors import Error
from board_tune.menu import geometry


def visible(text):
    return "".join(c if unicodedata.category(c) not in ("Cc", "Cf") else "?" for c in str(text))


class Dialogs:
    def __init__(self, ui):
        self.ui = ui

    def choose(self, title, lines, items):
        """Return an item index or None. Every modal handles scrolling/resize."""
        ui, selected, scroll, back = self.ui, 0, 0, False
        while True:
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
                return None if back or not items else selected

    def message(self, title, text):
        self.choose(title, [], str(text).splitlines() + ["OK"])


class WifiManager:
    def __init__(self, target):
        self.target = target
        self.iface = "wlan0"

    def show(self, ui):
        self.dialog = Dialogs(ui)
        while True:
            notes = []
            try:
                self.profiles = wifi.inventory(self.target, notes)
                adapters = sorted({p.active or p.iface for p in self.profiles if p.active or p.iface})
                if self.target.live:
                    rows = wifi.nmcli(self.target, "-f", "DEVICE,TYPE", "device", "status")
                    adapters = sorted(set(adapters) | {r[0] for line in rows.splitlines()
                                                      if len(r := wifi.fields(line)) == 2 and r[1] == "wifi"})
                if adapters and self.iface not in adapters:
                    self.iface = adapters[0]
                state = wifi.device_status(self.target, self.iface)
                action = self.dialog.choose("Wi-Fi", [f"Interface: {self.iface}", *state[:4], *notes[:1]],
                                            [f"Saved networks ({len(self.profiles)}) --->",
                                             "Select interface --->", "Batch profile / boot country options --->",
                                             "Refresh"])
                if action is None:
                    return False
                if action == 0:
                    self.saved()
                elif action == 1:
                    index = self.dialog.choose("Wi-Fi interface", [], adapters)
                    if index is not None:
                        self.iface = adapters[index]
                elif action == 2:
                    return True
            except Error as error:
                self.dialog.message("Wi-Fi", error)
                return False

    def saved(self):
        while True:
            labels = [f"{'*' if p.active else ' '} {p.ssid}  [{p.name}]  "
                      f"auto={'yes' if p.autoconnect else 'no'} priority={p.priority} "
                      f"({p.active or p.iface or 'any adapter'})" for p in self.profiles]
            index = self.dialog.choose("Saved Wi-Fi networks", ["* connected; all adapters; Enter: details"], labels)
            if index is None:
                return
            profile = self.profiles[index]
            self.dialog.message(profile.ssid,
                                f"Profile: {profile.name}\nUUID: {profile.uuid or '(not assigned)'}\n"
                                f"SSID: {profile.ssid}\nInterface: {profile.iface or 'any'}\n"
                                f"Security: {profile.auth}\nMode: {profile.mode}\n"
                                f"Active on: {profile.active or 'not connected'}\n"
                                f"Autoconnect: {profile.autoconnect}\nPriority: {profile.priority}")
