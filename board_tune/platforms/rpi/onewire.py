"""One GPIO 1-Wire master, with conservative handling of external overlays."""

import re
from dataclasses import dataclass

from board_tune.errors import Error

from . import configtxt

BLOCK = "hardware_onewire"
SUPPORTED = {"w1-gpio", "w1-gpio-pi5"}


@dataclass
class Master:
    managed: bool
    pin: int = 4
    certain: bool = True

    def parameters(self, text):
        for part in filter(None, text.split(",")):
            key, _, value = part.strip().partition("=")
            if key == "gpiopin":
                try:
                    self.pin = int(value, 16 if value.lower().startswith("0x") else 10)
                    self.certain &= 2 <= self.pin <= 27
                except ValueError:
                    self.certain = False
            elif key != "pullup":  # Obsolete/ignored by the standard overlay.
                self.certain = False


def scan(text, board):
    """Return possible masters and whether unexamined includes may add more.

    Follow inline and subsequent dtparam parameters until the next overlay.
    Runtime conditions, multi-master setups and other w1-gpio variants remain
    ambiguous; adding another overlay cannot override an earlier master.
    """
    masters = []
    model, conditional, managed, unknown = True, False, False, False
    managed_reset = False
    current = None
    for raw in text.splitlines():
        if raw.strip() == f"# >>> board-tune: {BLOCK} >>>":
            managed = True
        elif raw.strip() == f"# <<< board-tune: {BLOCK} <<<":
            managed = False
        line = raw.split("#", 1)[0].strip()
        if line == "[all]":
            model, conditional = True, False
        elif line == "[none]":
            model = False
        elif re.fullmatch(r"\[pi[0-5]\]", line):
            model = line == f"[{board.filter}]"
        elif line.startswith("["):
            conditional = True
        elif model and re.match(r"include\s", line):
            unknown = True
            if current:
                current.certain = False
        elif model and line.startswith(("dtoverlay=", "device_tree_overlay=")):
            name, _, params = line.partition("=")[2].replace(":", ",", 1).partition(",")
            name = name.strip()
            if managed and not conditional and not name:
                managed_reset = True
            if conditional and current:
                # This overlay may not load, so later dtparams may still target
                # the preceding master. Do not claim to know its final pin.
                current.certain = False
            current = None
            if name.startswith("w1-gpio"):
                supported = name in SUPPORTED and (not name.endswith("-pi5")
                                                   or board.filter == "pi5")
                current = Master(managed, certain=not conditional and supported)
                current.parameters(params)
                masters.append(current)
        elif model and current and line.startswith(("dtparam=", "device_tree_param=")):
            if conditional:
                current.certain = False
            current.parameters(line.partition("=")[2])
    return masters, unknown, managed_reset


def read(ctx, directory):
    masters, unknown, disabled = scan(ctx.plan.read_text(f"{directory}/config.txt") or "", ctx.board)
    values = {"HARDWARE_ONEWIRE_KEEP": "y"}
    if masters or unknown:
        if len(masters) == 1 and masters[0].certain and not unknown:
            values = {"HARDWARE_ONEWIRE_ENABLE": "y",
                      "HARDWARE_ONEWIRE_GPIO": str(masters[0].pin)}
            if not masters[0].managed:
                ctx.plan.note("1-Wire uses an external overlay; GPIO changes/disable "
                              "require editing that configuration")
        else:
            ctx.plan.note("1-Wire overlays/includes are ambiguous or unsupported; "
                          "1-Wire stays at Keep")
    elif disabled:
        values = {"HARDWARE_ONEWIRE_DISABLE": "y"}
    return values


def setup(ctx, directory, busy):
    cfg, plan = ctx.config, ctx.plan
    path = f"{directory}/config.txt"
    text = plan.read_text(path)
    masters, unknown, _ = scan(text, ctx.board)
    if unknown or len(masters) > 1 or any(not master.certain for master in masters):
        raise Error("1-Wire: cannot safely change ambiguous overlays/includes; "
                    "resolve existing configuration or select Keep")
    enabled = cfg.enabled("HARDWARE_ONEWIRE_ENABLE")
    pin = cfg.int("HARDWARE_ONEWIRE_GPIO") if enabled else None
    if enabled and pin in busy:
        raise Error(f"1-Wire GPIO {pin} conflicts with an enabled primary bus or serial console")
    if masters and not masters[0].managed:
        if enabled and masters[0].pin == pin:
            return  # Existing matching configuration needs no duplicate master.
        raise Error("1-Wire: an external overlay already configures this bus; "
                    "edit/remove it before changing GPIO or disabling it")
    if enabled:
        overlay = ctx.target.stat(ctx.target.realpath(f"{directory}/overlays/w1-gpio.dtbo"))
        if overlay is None or overlay.kind != "file":
            raise Error("1-Wire: w1-gpio.dtbo is not installed in the target boot overlays")
        lines = ["[all]", f"[{ctx.board.filter}]",
                 f"dtoverlay=w1-gpio,gpiopin={pin}", "dtoverlay=", "[all]"]
    else:
        if f"# >>> board-tune: {BLOCK} >>>" not in text:
            return
        # Keep the filter/parameter-scope boundary for following site lines.
        # Only the master overlay is removed; an empty managed block remains.
        lines = ["[all]", f"[{ctx.board.filter}]", "dtoverlay=", "[all]"]
    updated = configtxt.set_block(text, BLOCK, lines, at_end=True)
    if updated != text:
        plan.write(path, updated)
