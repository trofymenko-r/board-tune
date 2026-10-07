"""Hooks, platforms and their discovery."""

from __future__ import annotations

import importlib
import importlib.util
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from board_tune.errors import Error, Unsupported
from board_tune.plan import Plan
from board_tune.target import Target

if TYPE_CHECKING:
    from board_tune.kconfig import Config

BUILTIN_PATH = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Board:
    id: str  # Kconfig symbol suffix: BOARD_<id>
    title: str


@dataclass
class Context:
    """Everything a hook needs while planning its changes."""

    plan: Plan
    config: Config
    platform: Platform
    board: Board

    @property
    def target(self) -> Target:
        return self.plan.target


class Platform:
    """A family of boards and the knowledge of how they are set up.

    Hooks describe what they want in generic terms and call into the
    platform for board-specific parts. Services a platform does not
    provide raise Unsupported. Platforms live in platforms/<name>/ and
    export an instance as PLATFORM. An optional Kconfig there adds
    platform options; hooks/<hook>.Kconfig adds options to that hook's
    menu.
    """

    title = ""
    boards: tuple[Board, ...] = ()
    # Set by the loader.
    name = ""
    path: Path | None = None

    @property
    def id(self) -> str:
        return self.name.upper()

    def kconfig(self) -> Path | None:
        path = self.path / "Kconfig"
        return path if path.is_file() else None

    def hook_kconfig(self, hook: str) -> Path | None:
        path = self.path / "hooks" / f"{hook}.Kconfig"
        return path if path.is_file() else None

    def detect_board(self, target: Target) -> Board | None:
        """Identify the board of a live target, if possible."""
        return None

    def edit_kernel_cmdline(self, ctx: Context,
                            edit: Callable[[list[str]], list[str]]) -> None:
        """Stage an edit of the kernel command line tokens."""
        raise Unsupported(f"{self.title}: editing the kernel command line is not supported")

    def setup_serial_console(self, ctx: Context) -> str:
        """Enable the serial port; return its console= device name."""
        raise Unsupported(f"{self.title}: serial console setup is not supported")


class Hook:
    """A feature that customizes the target.

    Hooks live in hooks/<name>/ and export an instance as HOOK, next to
    a Kconfig file with their options. The hook runs when its symbol is
    enabled; hooks run in ascending order.
    """

    title = ""
    symbol = ""
    order = 50
    # Set by the loader.
    name = ""
    path: Path | None = None

    def kconfig(self) -> Path | None:
        path = self.path / "Kconfig"
        return path if path.is_file() else None

    def plan(self, ctx: Context) -> None:
        raise NotImplementedError


def search_path() -> list[Path]:
    """Directories with hooks/ and platforms/ subdirectories.

    Built-in ones come first, followed by the BOARD_TUNE_PATH entries.
    """
    extra = os.environ.get("BOARD_TUNE_PATH", "")
    return [BUILTIN_PATH] + [Path(p) for p in extra.split(os.pathsep) if p]


def _import(kind: str, directory: Path, builtin: bool):
    if builtin:
        return importlib.import_module(f"board_tune.{kind}.{directory.name}")
    name = f"board_tune_ext.{kind}.{directory.name}"
    spec = importlib.util.spec_from_file_location(
        name, directory / "__init__.py", submodule_search_locations=[str(directory)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load(kind: str, attr: str) -> list:
    found: dict = {}
    for base in search_path():
        kind_dir = base / kind
        if not kind_dir.is_dir():
            continue
        for directory in sorted(kind_dir.iterdir()):
            if not (directory / "__init__.py").is_file():
                continue
            if directory.name in found:
                raise Error(f"{directory}: duplicate {kind[:-1]} "
                            f"(also in {found[directory.name].path})")
            module = _import(kind, directory, base == BUILTIN_PATH)
            obj = getattr(module, attr, None)
            if obj is None:
                raise Error(f"{directory}: does not define {attr}")
            obj.name = directory.name
            obj.path = directory
            found[directory.name] = obj
    return list(found.values())


class Registry:
    def __init__(self, platforms: list[Platform], hooks: list[Hook]):
        if not platforms:
            raise Error("no platforms found")
        self.platforms = platforms
        self.hooks = sorted(hooks, key=lambda h: (h.order, h.name))

    @classmethod
    def load(cls) -> Registry:
        return cls(_load("platforms", "PLATFORM"), _load("hooks", "HOOK"))

    def board(self, config: Config) -> tuple[Platform, Board]:
        """The platform and board selected in the configuration."""
        for platform in self.platforms:
            for board in platform.boards:
                if config.enabled(f"BOARD_{board.id}"):
                    return platform, board
        raise Error("no board selected")
