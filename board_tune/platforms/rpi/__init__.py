"""Raspberry Pi 4 and 5 running Raspberry Pi OS or another Debian derivative."""

from __future__ import annotations

import re
from dataclasses import dataclass

from board_tune import cmdline, systemd
from board_tune.errors import Error, Unsupported
from board_tune.registry import Board, Context, Platform

from . import configtxt, hardware

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

    def detect_offline_board(self, target):
        # A stock image supports several models. Only our explicit, managed
        # board filters identify the model it was customized for.
        filters = set()
        starts = {f"# >>> board-tune: {name} >>>"
                  for name in ("serial_console", "hardware_i2c", "hardware_spi")}
        for directory in BOOT_DIRS:
            data = target.read(f"{directory}/config.txt")
            if data is None:
                continue
            inside = False
            for line in data.decode().splitlines():
                if line.strip() in starts:
                    inside = True
                elif line.strip().startswith("# <<< board-tune:"):
                    inside = False
                elif inside and line.strip() in ("[pi4]", "[pi5]"):
                    filters.add(line.strip()[1:-1])
        if len(filters) == 1:
            return next(b for b in self.boards if b.filter in filters)
        return None

    def read_hardware(self, ctx):
        directory = next((d for d in BOOT_DIRS if ctx.plan.exists(f"{d}/config.txt")), None)
        return hardware.read(ctx, directory) if directory else {"HARDWARE": "n"}

    def setup_hardware(self, ctx):
        if all(ctx.config.enabled(f"HARDWARE_{bus}_KEEP") for bus in ("I2C", "SPI")):
            return
        hardware.setup(ctx, self.boot_dir(ctx))

    def read_serial_console(self, ctx):
        directory = next((d for d in BOOT_DIRS if ctx.plan.exists(f"{d}/config.txt")), None)
        if directory is None:
            return {}
        tokens = (ctx.plan.read_text(f"{directory}/cmdline.txt") or "").split()
        consoles = [cmdline.value(t) for t in tokens if cmdline.key(t) == "console"]
        serials = [c for c in consoles
                   if re.fullmatch(r"(?:serial|ttyAMA|ttyS)\d+", c.split(",")[0])]
        if not serials:
            return {"SERIAL_CONSOLE": "n"}
        serial = serials[-1]
        values = {"SERIAL_CONSOLE": "y",
                  "SERIAL_CONSOLE_PRIMARY": "y" if consoles[-1] == serial else "n",
                  "SERIAL_CONSOLE_VERBOSE": "n" if {"quiet", "splash"} & set(tokens) else "y"}
        baud = re.match(r"\d+", serial.split(",", 1)[1]) if "," in serial else None
        if baud:
            values["SERIAL_CONSOLE_BAUD"] = baud.group()
        if values["SERIAL_CONSOLE_VERBOSE"] == "y":
            levels = [cmdline.value(t) for t in tokens if cmdline.key(t) == "loglevel"]
            if levels and levels[-1].isdigit() and 1 <= int(levels[-1]) <= 8:
                values["SERIAL_CONSOLE_LOGLEVEL"] = levels[-1]

        active = True
        lines = []
        for raw in (ctx.plan.read_text(f"{directory}/config.txt") or "").splitlines():
            line = raw.split("#", 1)[0].strip()
            if line.startswith("["):
                active = line in ("[all]", f"[{ctx.board.filter}]")
            elif active and line:
                lines.append(line)
        params = dict(line.split("=", 1) for line in lines if "=" in line)
        values["RPI_SERIAL_FIRMWARE_LOG"] = "y" if params.get("uart_2ndstage") == "1" else "n"
        if ctx.board.id == "RPI4":
            pl011 = any(line.split(",")[0] == "dtoverlay=disable-bt" for line in lines)
            values["RPI4_SERIAL_PL011" if pl011 else "RPI4_SERIAL_MINI_UART"] = "y"
        else:
            gpio = any(any(param.split("=", 1)[0] == "uart0_console"
                           and param.split("=", 1)[-1] not in ("off", "0", "false")
                           for param in line.split("=", 1)[-1].split(","))
                       for line in lines if line.startswith("dtparam="))
            gpio = gpio or serial.split(",")[0] == "ttyAMA0"
            values["RPI5_SERIAL_GPIO" if gpio else "RPI5_SERIAL_DEBUG"] = "y"
        if any(line.startswith("include ") for line in lines):
            ctx.plan.note("UART settings in config.txt include files are not imported")
        if len(serials) > 1:
            ctx.plan.note("multiple serial consoles found; the menu shows the last one")
        return values

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

    def setup_serial_console(self, ctx):
        cfg = ctx.config
        lines = ["enable_uart=1"]
        if ctx.board.id == "RPI4" and cfg.enabled("RPI4_SERIAL_PL011"):
            lines.append("dtoverlay=disable-bt")
            systemd.daemon_reload(ctx.plan, systemd.disable(ctx.plan, "hciuart.service"))
        if ctx.board.id == "RPI5" and cfg.enabled("RPI5_SERIAL_GPIO"):
            lines += ["dtparam=uart0=on", "dtparam=uart0_console"]
        if cfg.enabled("RPI_SERIAL_FIRMWARE_LOG"):
            lines.append("uart_2ndstage=1")
        self.config_txt(ctx, "serial_console", lines)
        # The firmware replaces serial0 with the UART chosen above.
        return "serial0"


PLATFORM = RaspberryPi()
