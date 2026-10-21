"""Kernel-style presentation for the pinned Kconfiglib curses frontend.

Kconfiglib still owns navigation, dependencies, validation and text editing.
Its rendering hooks are private, so keep the adaptation scoped to one session
and test it against the exact version declared in pyproject.toml.
"""

from __future__ import annotations

import curses
import os
import textwrap
from contextlib import contextmanager

STYLE = """list=fg:black,bg:white selection=fg:white,bg:blue,bold
path=fg:white,bg:blue separator=fg:blue,bg:white,bold
help=fg:black,bg:white frame=fg:blue,bg:white,bold
body=fg:black,bg:white edit=fg:black,bg:white,underline
text=fg:black,bg:white"""
BUTTONS = ("Select", "Exit", "Help", "Save", "Load")


def geometry(rows: int, columns: int, entries: int) -> tuple[int, int, int, int]:
    """Return the centered dialog's height, width, top and left."""
    height = max(1, min(rows - 4, max(16, min(24, entries + 9))))
    width = max(1, min(columns - 8, 88))
    return height, width, (rows - height) // 2, (columns - width) // 2


class _KernelStyle:
    def __init__(self, ui, actions=None):
        self.ui = ui
        self.getch = ui._getch_compat
        self.input_dialog = ui._input_dialog
        self.draw_input = ui._draw_input_dialog
        self.input_window = None
        self.input_button = None
        self.button = 0
        self.current_menu = None
        self.menu_geometry = None
        self.actions = actions or {}
        self.discarded = False

    def resize(self):
        ui = self.ui
        rows, columns = ui._stdscr.getmaxyx()
        if rows < 14 or columns < 60:
            layout = (1, 1, 0, 0)
        else:
            height, width, top, left = geometry(rows, columns, len(ui._shown))
            layout = (height - 8, width - 6, top + 4, left + 3)
        if layout != self.menu_geometry:
            # Recreate rather than moving an oversized old window after a
            # terminal shrink; mvwin() would reject its old dimensions.
            ui._menu_win = curses.newwin(*layout)
            ui._set_style(ui._menu_win, "list")
            ui._menu_win.keypad(True)
            self.menu_geometry = layout
        ui._menu_scroll = max(0, min(ui._menu_scroll, ui._sel_node_i))
        if ui._sel_node_i >= ui._menu_scroll + layout[0]:
            ui._menu_scroll = ui._sel_node_i - layout[0] + 1

    def frame(self, win, title):
        ui = self.ui
        win.attrset(ui._style["frame"])
        try:
            win.box()
        except curses.error:
            pass
        width = win.getmaxyx()[1]
        title = f" {title} "[:max(0, width - 4)] if title else ""
        ui._safe_addstr(win, 0, max(1, (width - len(title)) // 2), title)
        win.attrset(ui._style["body"])

    def draw(self):
        ui = self.ui
        self.resize()
        if self.current_menu is not ui._cur_menu:
            self.current_menu = ui._cur_menu
            self.button = 0
        screen = ui._stdscr
        rows, columns = screen.getmaxyx()
        screen.bkgd(" ", ui._style["path"])
        screen.erase()
        ui._safe_addstr(screen, 0, 1, "board-tune - Board configuration", curses.A_BOLD)
        if rows < 14 or columns < 60:
            ui._safe_addstr(screen, 2, 1, "Resize terminal to at least 60 x 14")
            screen.noutrefresh()
            return

        path = []
        node = ui._cur_menu
        while node is not ui._kconf.top_node:
            if node.prompt:
                path.append(node.prompt[0])
            node = node.parent
        breadcrumb = " > ".join(["Main menu", *reversed(path)])
        ui._safe_addstr(screen, 1, 1, breadcrumb[:columns - 2])
        screen.noutrefresh()

        height, width, top, left = geometry(rows, columns, len(ui._shown))
        panel = curses.newwin(height, width, top, left)
        panel.bkgd(" ", ui._style["body"])
        panel.erase()
        title = ui._cur_menu.prompt[0] if ui._cur_menu.prompt else "Main menu"
        self.frame(panel, title)
        hint = "Arrows: move   Enter: select   Space: toggle   ?: help"
        ui._safe_addstr(panel, 2, 3, hint[:width - 6])
        ui._safe_hline(panel, height - 4, 1, curses.ACS_HLINE, width - 2)
        buttons = [f"< {label} >" for label in BUTTONS]
        start = max(1, (width - sum(map(len, buttons)) - 2 * (len(buttons) - 1)) // 2)
        for i, label in enumerate(buttons):
            if start + len(label) >= width:
                break
            style = ui._style["selection" if i == self.button else "body"]
            ui._safe_addstr(panel, height - 2, start, label, style)
            start += len(label) + 2
        if ui._menu_scroll:
            ui._safe_addstr(panel, 3, width - 5, "^", ui._style["frame"])
        if ui._menu_scroll + height - 8 < len(ui._shown):
            ui._safe_addstr(panel, height - 5, width - 5, "v", ui._style["frame"])
        panel.noutrefresh()

        ui._menu_win.erase()
        for row, node in enumerate(ui._shown[ui._menu_scroll:ui._menu_scroll + height - 8]):
            selected = row + ui._menu_scroll == ui._sel_node_i
            style = ui._style["selection" if selected else "list"]
            text = ui._node_str(node).replace(" (NEW)", "")
            ui._safe_addstr(ui._menu_win, row, 0, text[:width - 7], style)
        ui._menu_win.noutrefresh()

    def input(self, title, initial_text, info_text=None):
        previous = self.input_window, self.input_button
        self.input_window = self.input_button = None
        try:
            return self.input_dialog(title, initial_text, info_text)
        finally:
            self.input_window, self.input_button = previous

    def input_frame(self, win, title, info_lines, text, cursor, scroll):
        self.input_window = win
        self.draw_input(win, title, info_lines, text, cursor, scroll)
        ui = self.ui
        height, width = win.getmaxyx()
        labels = ("< OK >", "< Cancel >")
        left = max(1, (width - 20) // 2)
        for i, label in enumerate(labels):
            style = ui._style["selection" if self.input_button == i else "body"]
            ui._safe_addstr(win, height - 2, left, label, style)
            left += len(label) + 4
        if self.input_button is None:
            ui._safe_move(win, 2, 2 + cursor - scroll)
        ui._safe_curs_set(2 if self.input_button is None else 0)
        win.noutrefresh()

    def help(self, node, from_jump_to_dialog=False):
        title = node.prompt[0] if node.prompt else "Help"
        text = node.help or (
            "Enter opens a submenu or edits a value. Space toggles a checkbox. "
            "Use Y/N to set a checkbox, Esc to go back, and Tab to select a button. "
            "Exit returns to the change preview before anything is applied.")
        width = max(20, min(64, self.ui._stdscr.getmaxyx()[1] - 8))
        lines = "\n".join(textwrap.fill(paragraph, width) for paragraph in text.split("\n\n"))
        self.ui._msg(title, lines + "\n\nEnter / Esc: return")

    def save(self):
        ui = self.ui
        if ui._try_save(ui._kconf.write_config, ui._conf_filename, "configuration"):
            ui._conf_changed = False
            ui._msg("Saved", "Settings saved for this session.\nExit to review and apply changes.\n\nEnter / Esc: return")

    def load(self):
        ui = self.ui
        if ui._conf_changed:
            answer = ui._key_dialog("Load profile", "Replace unsaved changes?\n\n(Y)es  (N)o", "yn")
            if answer != "y":
                return
        filename = ui._input_dialog("Load profile", "", "Enter a configuration file path.\nEnter: load   Esc: cancel")
        if filename and ui._try_load(os.path.expanduser(filename)):
            # The imported profile is input, never the session's save target.
            ui._conf_changed = True
            ui._cur_menu = ui._kconf.top_node
            ui._shown = ui._shown_nodes(ui._cur_menu)
            ui._sel_node_i = ui._menu_scroll = 0
            ui._parent_screen_rows = []

    def quit(self):
        ui = self.ui
        if not ui._conf_changed:
            return "Configuration ready."
        while True:
            answer = self.exit_dialog()
            if answer in (None, "c"):
                return None
            if answer == "n":
                self.discarded = True
                return "Changes discarded."
            if ui._try_save(ui._kconf.write_config, ui._conf_filename, "configuration"):
                return "Configuration ready."

    def exit_dialog(self):
        """The compact two-button save prompt used by kernel menuconfig."""
        ui, selected = self.ui, 0
        while True:
            screen = ui._stdscr
            rows, columns = screen.getmaxyx()
            screen.bkgd(" ", ui._style["path"])
            screen.erase()
            ui._safe_addstr(screen, 0, 1, "board-tune - Board configuration", curses.A_BOLD)
            screen.noutrefresh()
            if rows < 14 or columns < 60:
                ui._safe_addstr(screen, 2, 1, "Resize terminal to at least 60 x 14")
                screen.noutrefresh()
                win = screen
            else:
                height, width = 6, min(60, columns - 4)
                win = curses.newwin(height, width, (rows - height) // 2, (columns - width) // 2)
                win.keypad(True)
                win.bkgd(" ", ui._style["body"])
                win.erase()
                self.frame(win, "")
                ui._safe_addstr(win, 1, 3, "Do you wish to save your new configuration?")
                ui._safe_addstr(win, 2, 3, "(Press <Esc> to return to configuration.)")
                ui._safe_hline(win, 3, 1, curses.ACS_HLINE, width - 2)
                labels = ("< Yes >", "< No >")
                left = (width - sum(map(len, labels)) - 6) // 2
                for index, label in enumerate(labels):
                    ui._safe_addstr(win, 4, left, label,
                                    ui._style["selection" if index == selected else "body"])
                    left += len(label) + 6
                win.noutrefresh()
            curses.doupdate()
            key = ui._getch_compat(win)
            if key in ("\x1b", "c", "C"):
                return None
            if key == curses.KEY_RESIZE:
                ui._resize_main()
                continue
            if rows < 14 or columns < 60:
                continue
            if key in (curses.KEY_LEFT, curses.KEY_RIGHT, "\t", curses.KEY_BTAB):
                selected = 1 - selected
            elif key in ("\n", " ", curses.KEY_ENTER):
                return ("y", "n")[selected]
            elif key in ("y", "Y", "n", "N"):
                return key.lower()

    def key(self, win):
        ui = self.ui
        key = self.getch(win)
        if win is self.input_window:
            if key in ("\t", curses.KEY_BTAB):
                choices = (None, 0, 1)
                step = -1 if key == curses.KEY_BTAB else 1
                self.input_button = choices[(choices.index(self.input_button) + step) % 3]
                return curses.KEY_RESIZE
            if self.input_button is not None and key in (curses.KEY_LEFT, curses.KEY_RIGHT):
                self.input_button = 1 - self.input_button
                return curses.KEY_RESIZE
            if key == "\n" and self.input_button == 1:
                return "\x1b"
            if isinstance(key, str) and key.isprintable():
                self.input_button = None
            return key
        if win is not ui._menu_win:
            return key
        rows, columns = ui._stdscr.getmaxyx()
        if (rows < 14 or columns < 60) and key not in ("q", "Q", "\x1b", curses.KEY_RESIZE):
            return curses.KEY_RESIZE
        if key in ("\t", curses.KEY_RIGHT, curses.KEY_BTAB, curses.KEY_LEFT):
            step = -1 if key in (curses.KEY_BTAB, curses.KEY_LEFT) else 1
            self.button = (self.button + step) % len(BUTTONS)
            return curses.KEY_RESIZE
        if key in (curses.KEY_UP, curses.KEY_DOWN, "j", "k", "J", "K"):
            self.button = 0
        if key == "\n" and self.button:
            key = ("\n", "\x1b", "?", "s", "o")[self.button]
            self.button = 0
        if key in ("\n", "l", "L", " ") and ui._shown:
            node = ui._shown[ui._sel_node_i]
            action = self.actions.get(node.prompt[0]) if node.prompt else None
            if action is not None:
                if action(ui):
                    return "\n"
                ui._stdscr.touchwin()
                return curses.KEY_RESIZE
        if key == "?":
            self.help(ui._shown[ui._sel_node_i])
        elif key in ("s", "S"):
            self.save()
        elif key in ("o", "O"):
            self.load()
        else:
            return key
        return curses.KEY_RESIZE


@contextmanager
def kernel_style(ui, actions=None):
    """Temporarily adapt Kconfiglib; leave its module and the environment intact."""
    adapter = _KernelStyle(ui, actions)
    replacements = {"_resize_main": adapter.resize, "_draw_main": adapter.draw,
                    "_draw_frame": adapter.frame, "_getch_compat": adapter.key,
                    "_info_dialog": adapter.help, "_load_dialog": adapter.load,
                    "_input_dialog": adapter.input, "_draw_input_dialog": adapter.input_frame,
                    "_quit_dialog": adapter.quit}
    originals = {name: getattr(ui, name) for name in replacements}
    old_style = os.environ.get("MENUCONFIG_STYLE")
    os.environ["MENUCONFIG_STYLE"] = STYLE + " " + (old_style or "")
    try:
        for name, replacement in replacements.items():
            setattr(ui, name, replacement)
        yield adapter
    finally:
        for name, original in originals.items():
            setattr(ui, name, original)
        if old_style is None:
            os.environ.pop("MENUCONFIG_STYLE", None)
        else:
            os.environ["MENUCONFIG_STYLE"] = old_style
