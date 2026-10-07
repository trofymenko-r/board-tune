"""Raspberry Pi 4 and 5 running Raspberry Pi OS or another Debian derivative."""

from __future__ import annotations

from dataclasses import dataclass

from board_tune.errors import Error, Unsupported
from board_tune.registry import Board, Context, Platform

from . import configtxt

BOOT_DIRS = ("/boot/firmware", "/boot")


@dataclass(frozen=True)
class RpiBoard(Board):
    # config.txt conditional filter selecting this board, e.g. [pi4].
    filter: str = ""
    # Prefixes of /proc/device-tree/model identifying the board.
    models: tuple[str, ...] = ()


class RaspberryPi(Platform):
    title = "Raspberry Pi"
    boards = (
        RpiBoard("RPI4", "Raspberry Pi 4", filter="pi4",
                 models=("Raspberry Pi 4", "Raspberry Pi 400",
                         "Raspberry Pi Compute Module 4")),
        RpiBoard("RPI5", "Raspberry Pi 5", filter="pi5",
                 models=("Raspberry Pi 5", "Raspberry Pi 500",
                         "Raspberry Pi Compute Module 5")),
    )

    def detect_board(self, target):
        data = target.read("/proc/device-tree/model")
        if not data:
            return None
        model = data.rstrip(b"\0").decode(errors="replace")
        for board in self.boards:
            if model.startswith(board.models):
                return board
        if model.startswith("Raspberry Pi"):
            raise Unsupported(f"{target.description}: {model} is not supported")
        return None

    def boot_dir(self, ctx: Context) -> str:
        """Where the boot partition is mounted: /boot/firmware since Bookworm."""
        for d in BOOT_DIRS:
            if ctx.plan.exists(f"{d}/config.txt"):
                return d
        raise Error(f"{ctx.target.description}: config.txt not found in "
                    f"{' or '.join(BOOT_DIRS)}")

    def config_txt(self, ctx: Context, name: str, lines: list[str]) -> None:
        """Stage the config.txt block called name, limited to this board.

        The block sits under the board's conditional filter, so a card
        moved to another board model does not pick up its settings.
        """
        path = f"{self.boot_dir(ctx)}/config.txt"
        text = ctx.plan.read_text(path)
        if lines:
            lines = [f"[{ctx.board.filter}]", *lines, "[all]"]
        new = configtxt.set_block(text, name, lines)
        if new != text:
            ctx.plan.write(path, new)

    def edit_kernel_cmdline(self, ctx, edit):
        path = f"{self.boot_dir(ctx)}/cmdline.txt"
        text = ctx.plan.read_text(path)
        if text is None:
            raise Error(f"{ctx.target.description}: {path} not found")
        tokens = text.split()
        new = edit(list(tokens))
        if new != tokens:
            # The firmware expects everything on a single line.
            ctx.plan.write(path, " ".join(new) + "\n")


PLATFORM = RaspberryPi()
