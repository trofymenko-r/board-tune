"""Configuration handling on top of kconfiglib."""

from __future__ import annotations

import os
from pathlib import Path

import kconfiglib
from kconfiglib import BOOL, TRI_TO_STR, TRISTATE

from board_tune.errors import Error
from board_tune.menu import kernel_style
from board_tune.plan import Plan
from board_tune.registry import Board, Context, Hook, Registry
from board_tune.target import Target

HEADER = "# board-tune configuration\n"


def _quote(path: Path) -> str:
    return '"' + str(path).replace("\\", "\\\\").replace('"', '\\"') + '"'


def generate_kconfig(registry: Registry) -> str:
    """Top-level Kconfig: the board choice, platforms and hooks.

    Platform fragments for a hook are sourced right after the hook's
    own Kconfig, so options depending on the hook symbol show up in
    its menu.
    """
    out = ['mainmenu "board-tune configuration"', "", "choice", '\tprompt "Board"', ""]
    for platform in registry.platforms:
        for board in platform.boards:
            out += [f"config BOARD_{board.id}", f'\tbool "{board.title}"',
                    f"\tselect PLATFORM_{platform.id}", ""]
    out += ["endchoice", ""]
    for platform in registry.platforms:
        out += [f"config PLATFORM_{platform.id}", "\tbool", ""]
    for platform in registry.platforms:
        if platform.kconfig():
            out.append(f"source {_quote(platform.kconfig())}")
    for hook in registry.hooks:
        title = hook.title.replace('"', '\"')
        out.append(f'menu "{title}"')
        if hook.kconfig():
            out.append(f"source {_quote(hook.kconfig())}")
        for platform in registry.platforms:
            if platform.hook_kconfig(hook.name):
                out.append(f"source {_quote(platform.hook_kconfig(hook.name))}")
        out.append("endmenu")
    return "\n".join(out) + "\n"


class _Kconfig(kconfiglib.Kconfig):
    """Kconfig remembering where menuconfig saved the configuration."""

    def __init__(self, *args, **kwargs):
        self.saved_to: list[str] = []
        super().__init__(*args, **kwargs)

    def write_config(self, filename=None, *args, **kwargs):
        self.saved_to.append(filename)
        return super().write_config(filename, *args, **kwargs)


class Config:
    """Read-only typed access to the final configuration, for hooks."""

    def __init__(self, kconf: kconfiglib.Kconfig):
        self._kconf = kconf

    def _sym(self, name: str) -> kconfiglib.Symbol:
        sym = self._kconf.syms.get(name)
        if sym is None or not sym.nodes:
            raise Error(f"unknown configuration symbol {name}")
        return sym

    def enabled(self, name: str) -> bool:
        return self._sym(name).tri_value == 2

    def str(self, name: str) -> str:
        return self._sym(name).str_value

    def int(self, name: str) -> int:
        value = self._sym(name).str_value
        return int(value, 0) if value else 0


class Configuration:
    """The configuration being assembled from files, --set and the menu."""

    def __init__(self, registry: Registry, workdir: str):
        self._workdir = workdir
        self.target = None
        self.dry_run = False
        kconfig = os.path.join(workdir, "Kconfig")
        with open(kconfig, "w") as f:
            f.write(generate_kconfig(registry))
        try:
            self.kconf = _Kconfig(kconfig, warn_to_stderr=False)
        except kconfiglib.KconfigError as e:
            raise Error(str(e)) from None
        self.kconf.warn_assign_undef = True
        self.kconf.warn_assign_override = False
        self.kconf.warn_assign_redun = False
        self._seen_warnings = len(self.kconf.warnings)
        self.config = Config(self.kconf)

    def read_target(self, registry: Registry, target: Target,
                    board_hint: Board | None = None) -> list[str]:
        """Seed the menu from target files; explicit profiles are loaded afterwards."""
        plan = Plan(target)
        self.target = target
        for platform in registry.platforms:
            board = (platform.detect_board(target) if target.live
                     else platform.detect_offline_board(target))
            if board is not None:
                self.set(f"BOARD_{board.id}=y")
                break
        else:
            if board_hint is not None:
                self.set(f"BOARD_{board_hint.id}=y")
            else:
                plan.note("board model could not be determined; check the Board selection")
        platform, board = registry.board(self.config)
        ctx = Context(plan, self.config, platform, board)
        for hook in registry.hooks:
            for name, value in hook.read(ctx).items():
                self.set(f"{name}={value}")
        return plan.notes

    def hook_values(self, registry: Registry, hook: Hook) -> dict[str, str]:
        """Effective options defined by a hook and its platform fragments."""
        fragments = [hook.kconfig()] + [platform.hook_kconfig(hook.name)
                                        for platform in registry.platforms]
        paths = {path.resolve() for path in fragments if path}
        return {sym.name: sym.str_value for sym in self.kconf.unique_defined_syms
                if sym.name == hook.symbol or any(
                    Path(self.kconf.srctree, node.filename).resolve() in paths
                    for node in sym.nodes)}

    def load(self, path: str) -> None:
        """Merge a configuration fragment over the current values."""
        if not os.path.isfile(path):
            raise Error(f"{path}: no such configuration file")
        try:
            self.kconf.load_config(path, replace=False)
        except kconfiglib.KconfigError as e:
            raise Error(str(e)) from None

    def set(self, assignment: str) -> None:
        """Apply a SYMBOL=VALUE assignment from the command line."""
        name, sep, value = assignment.partition("=")
        name = name.strip().removeprefix(self.kconf.config_prefix)
        sym = self.kconf.syms.get(name)
        if not sep or sym is None or not sym.nodes:
            raise Error(f"--set {assignment}: expected SYMBOL=VALUE with a known symbol")
        if not sym.set_value(value):
            raise Error(f"--set {assignment}: invalid value for {name}")

    def problems(self) -> list[str]:
        """Warnings and requested values that did not take effect."""
        out = [w.removeprefix("warning: ")
               for w in self.kconf.warnings[self._seen_warnings:]]
        for sym in self.kconf.unique_defined_syms:
            if sym.user_value is None or not any(n.prompt for n in sym.nodes):
                continue
            if sym.orig_type in (BOOL, TRISTATE):
                wanted = TRI_TO_STR[sym.user_value]
            else:
                wanted = sym.user_value
            if wanted != sym.str_value:
                got = sym.str_value or '""'
                deps = kconfiglib.expr_str(sym.direct_dep)
                out.append(f"{sym.name}={wanted} is not possible with the rest "
                           f"of the configuration (got {got}; depends on {deps})")
        return out

    def save(self, path: str) -> str:
        """Write the minimal configuration: values that differ from defaults."""
        return self.kconf.write_min_config(path, header=HEADER)

    def menu(self) -> bool:
        """Run menuconfig; return False if the user discarded the result."""
        import menuconfig

        path = os.path.join(self._workdir, "board-tune.config")
        self.kconf.write_config(path, header=HEADER)
        self.kconf.saved_to.clear()
        old_env = os.environ.get("KCONFIG_CONFIG")
        os.environ["KCONFIG_CONFIG"] = path
        try:
            actions = {}
            if self.target is not None:
                from board_tune.wifi_menu import WifiManager
                self.wifi_manager = WifiManager(self.target, allow_live=not self.dry_run)
                def open_wifi(ui):
                    previous = dict(self.wifi_manager.session.edits)
                    legacy = self.wifi_manager.show(ui)
                    if previous != self.wifi_manager.session.edits:
                        ui._conf_changed = True
                    return legacy
                actions["Wi-Fi"] = open_wifi
            with kernel_style(menuconfig, actions) as presentation:
                menuconfig.menuconfig(self.kconf)
        finally:
            if old_env is None:
                del os.environ["KCONFIG_CONFIG"]
            else:
                os.environ["KCONFIG_CONFIG"] = old_env

        # Quitting without saving changes discards them.
        if presentation.discarded:
            return False
        if path not in self.kconf.saved_to and getattr(menuconfig, "_conf_changed", True):
            return False
        self.kconf.load_config(path)
        self._seen_warnings = len(self.kconf.warnings)
        return True
