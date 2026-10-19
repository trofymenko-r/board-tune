"""Additional header UARTs; console and Bluetooth configuration stay separate."""

import re
from dataclasses import dataclass

from board_tune.errors import Error

from . import configtxt, overlaymap

# BCM GPIOs: TX, RX, CTS, RTS. UART numbers are controller IDs, not tty names.
PINS = {
    "RPI4": {2: (0, 1, 2, 3), 3: (4, 5, 6, 7),
             4: (8, 9, 10, 11), 5: (12, 13, 14, 15)},
    "RPI5": {0: (14, 15, 16, 17), 1: (0, 1, 2, 3), 2: (4, 5, 6, 7),
             3: (8, 9, 10, 11), 4: (12, 13, 14, 15)},
}
ON = {"1", "on", "yes", "true"}
OFF = {"0", "off", "no", "false"}


def block(port):
    return f"hardware_uart{port}"


def has_actions(ctx):
    return any(not ctx.config.enabled(f"HARDWARE_UART{port}_KEEP")
               for port in PINS[ctx.board.id])


@dataclass
class Instance:
    port: int
    managed: bool
    flow: bool = False
    certain: bool = True
    base: bool = False
    presence_flow: bool = False

    def parameters(self, text):
        for part in filter(None, text.split(",")):
            key, sep, value = part.strip().partition("=")
            value = value.lower() if sep else "on"
            if key == "ctsrts" and value in ON | OFF:
                # Pi 5's override appends a pinctrl phandle whenever present;
                # even ctsrts=off enables those pins. Pi 4 uses a boolean fragment.
                self.flow = self.presence_flow or value in ON
            else:
                self.certain = False


def scan(text, board, aliases=None):
    instances, disabled = [], set()
    model, conditional, includes = True, False, False
    managed, current = None, None
    for raw in text.splitlines():
        start = re.fullmatch(r"# >>> board-tune: hardware_uart([0-5]) >>>", raw.strip())
        if start:
            managed = int(start[1])
        elif raw.strip().startswith("# <<< board-tune:"):
            managed = None
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
            includes = True
            if current:
                current.certain = False
        elif model and line.startswith(("dtoverlay=", "device_tree_overlay=")):
            if conditional and current:
                current.certain = False
            current = None
            name, _, params = line.partition("=")[2].replace(":", ",", 1).partition(",")
            name = name.strip()
            if not name and managed is not None and not conditional:
                disabled.add(managed)
            match = re.fullmatch(r"uart([0-5])(-pi5)?", name)
            if match and int(match[1]) in PINS[board.id]:
                port = int(match[1])
                # Generic Pi 5 names are accepted only with a verified mapping
                # in this target's firmware; never infer it from host firmware.
                variant = (bool(match[2]) == (board.id == "RPI5")
                           or (aliases or {}).get(name) == f"uart{port}-pi5")
                current = Instance(port, managed == port, certain=variant and not conditional,
                                   presence_flow=board.id == "RPI5")
                current.parameters(params)
                instances.append(current)
        elif model and line.startswith(("dtparam=", "device_tree_param=")):
            parts = line.partition("=")[2].split(",")
            if current:
                current.certain &= not conditional
                # Known base-dtb UART controls fall back to the base tree;
                # they are not unsupported parameters of the current overlay.
                overlay_params = [part for part in parts if not re.fullmatch(
                    r"uart[0-5]|uart0_console|uart0_dma", part.strip().partition("=")[0])]
                current.parameters(",".join(overlay_params))
            for part in parts:
                key = part.strip().partition("=")[0]
                match = re.fullmatch(r"uart([0-5])", key)
                if match and int(match[1]) in PINS[board.id]:
                    # An external base-dtb control is not owned by our overlay.
                    instances.append(Instance(int(match[1]), False, certain=False, base=True))
    return instances, disabled, includes


def read(ctx, directory):
    aliases = overlaymap.uart_aliases(ctx.plan.read(f"{directory}/overlays/overlay_map.dtb"), ctx.board)
    instances, disabled, includes = scan(ctx.plan.read_text(f"{directory}/config.txt") or "", ctx.board, aliases)
    values = {}
    for port in PINS[ctx.board.id]:
        found = [item for item in instances if item.port == port]
        prefix = f"HARDWARE_UART{port}"
        if includes or len(found) > 1 or any(not item.certain for item in found):
            values[f"{prefix}_KEEP"] = "y"
            ctx.plan.note(f"UART{port} configuration is ambiguous/unsupported; stays at Keep")
        elif found:
            values[f"{prefix}_ENABLE"] = "y"
            values[f"{prefix}_CTSRTS"] = "y" if found[0].flow else "n"
            if not found[0].managed:
                ctx.plan.note(f"UART{port} uses an external overlay; changes require editing it")
        else:
            values[f"{prefix}_{'DISABLE' if port in disabled else 'KEEP'}"] = "y"
    return values


def console_pins(ctx, directory):
    """Protect the primary console; refuse unresolved tty-to-controller maps."""
    tokens = (ctx.plan.read_text(f"{directory}/cmdline.txt") or "").split()
    devices = [token.partition("=")[2].split(",")[0]
               for token in tokens if token.startswith("console=")]
    for base in ("/etc/systemd/system", "/run/systemd/generator", "/run/systemd/generator.late"):
        for directory_name in ctx.target.listdir(base):
            if not directory_name.endswith(".wants"):
                continue
            for name in ctx.target.listdir(f"{base}/{directory_name}"):
                if name.startswith("serial-getty@") and name.endswith(".service"):
                    devices.append(name[len("serial-getty@"):-len(".service")])
    pins = set()
    serial = ctx.platform.read_serial_console(ctx)
    header_alias = serial.get("RPI5_SERIAL_GPIO") == "y"
    # A getty may use serial0 even with no console= kernel argument. In that
    # case read_serial_console has no UART flags; reserve the header pins if
    # any uncommented firmware directive may move the alias there.
    for raw in (ctx.plan.read_text(f"{directory}/config.txt") or "").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line.startswith(("dtparam=", "device_tree_param=")):
            header_alias |= any(part.partition("=")[0] == "uart0_console"
                                for part in line.partition("=")[2].split(","))
    for device in devices:
        if device == "serial0":
            if ctx.board.id == "RPI4" or header_alias:
                pins.update((14, 15))
        elif device in ("ttyAMA0", "ttyS0", "serial1"):
            pins.update((14, 15))
        elif device == "ttyAMA10" and ctx.board.id == "RPI5":
            pass  # Dedicated debug connector.
        elif re.fullmatch(r"(?:ttyAMA|ttyS|serial)\d+", device):
            raise Error(f"UART: cannot resolve console/getty {device} to a controller; "
                        "keep UARTs unchanged or resolve the console configuration first")
    if any(token.startswith("earlycon=") for token in tokens):
        raise Error("UART: explicit earlycon address cannot be resolved; keep UARTs unchanged")
    return pins


def setup(ctx, directory):
    cfg, plan = ctx.config, ctx.plan
    path = f"{directory}/config.txt"
    text = plan.read_text(path)
    aliases = overlaymap.uart_aliases(plan.read(f"{directory}/overlays/overlay_map.dtb"), ctx.board)
    instances, _, includes = scan(text, ctx.board, aliases)
    changing = has_actions(ctx)
    if changing and includes:
        raise Error("UART: resolve config.txt includes before changing UARTs, or select Keep")
    if changing and any(not item.certain and not item.base for item in instances):
        raise Error("UART: resolve ambiguous/unsupported UART configuration or select Keep")
    protected = console_pins(ctx, directory) if changing else set()
    effective, preserved = {}, set()
    for port in PINS[ctx.board.id]:
        found = [item for item in instances if item.port == port]
        prefix = f"HARDWARE_UART{port}"
        if cfg.enabled(f"{prefix}_KEEP"):
            if len(found) == 1 and found[0].certain:
                effective[port] = found[0].flow
                preserved.add(port)
            continue
        if any(not item.certain for item in found):
            raise Error(f"UART{port}: external base-dtb settings are not managed; select Keep or edit them")
        if len(found) > 1:
            raise Error(f"UART{port}: multiple overlays; resolve existing configuration first")
        enabled = cfg.enabled(f"{prefix}_ENABLE")
        flow = cfg.enabled(f"{prefix}_CTSRTS") if enabled else False
        if enabled and found and found[0].flow == flow:
            preserved.add(port)
        if found and not found[0].managed:
            if enabled and found[0].flow == flow:
                effective[port] = flow
                continue
            raise Error(f"UART{port}: edit/remove the external overlay before changing it")
        if not enabled and f"# >>> board-tune: {block(port)} >>>" not in text:
            continue
        used = set(PINS[ctx.board.id][port][:4 if flow or any(item.flow for item in found) else 2])
        if port not in preserved and used & protected:
            raise Error(f"UART{port}: cannot change a UART/pins used by a serial console/getty")
        if enabled:
            name = f"uart{port}" + ("-pi5" if ctx.board.id == "RPI5" else "")
            overlay = ctx.target.stat(ctx.target.realpath(f"{directory}/overlays/{name}.dtbo"))
            if overlay is None or overlay.kind != "file":
                raise Error(f"UART{port}: {name}.dtbo is not installed in target boot overlays")
            # Omission is the portable disabled state: Pi 5 ignores a false
            # value when the presence-based ctsrts override is supplied.
            lines = [f"dtoverlay={name}" + (",ctsrts" if flow else ""), "dtoverlay="]
            effective[port] = flow
        else:
            lines = ["dtoverlay="]
        lines = ["[all]", f"[{ctx.board.filter}]", *lines, "[all]"]
        text = configtxt.set_block(text, block(port), lines, at_end=True)
        plan.write(path, text)
    if not changing and not effective:
        return
    # Validate the final projected configuration, including kept controllers.
    from .hardware import read as read_hardware

    boot = read_hardware(ctx, directory)
    busy = {}
    if boot.get("HARDWARE_I2C_ENABLE") == "y":
        busy.update({pin: "I2C" for pin in (2, 3)})
    if boot.get("HARDWARE_SPI_ENABLE") == "y":
        busy.update({pin: "SPI0" for pin in range(7, 12)})
    if boot.get("HARDWARE_ONEWIRE_ENABLE") == "y":
        busy[int(boot["HARDWARE_ONEWIRE_GPIO"])] = "1-Wire"
    for item in instances:
        if item.base:
            busy.update({pin: f"external UART{item.port}" for pin in PINS[ctx.board.id][item.port][:2]})
    for port, flow in effective.items():
        used = PINS[ctx.board.id][port][:4 if flow else 2]
        if port not in preserved and set(used) & protected:
            raise Error(f"UART{port}: cannot change pins used by a serial console/getty")
        for pin in used:
            if pin in busy:
                raise Error(f"UART{port} GPIO {pin} conflicts with {busy[pin]}")
            busy[pin] = f"UART{port}"
