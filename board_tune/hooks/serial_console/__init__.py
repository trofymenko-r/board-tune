"""Serial console: kernel console, boot log verbosity and autologin."""

from __future__ import annotations

import re
import shlex

from board_tune import accounts, cmdline, systemd
from board_tune.errors import Error
from board_tune.registry import Context, Hook

AUTOLOGIN_DROPIN = "/etc/systemd/system/serial-getty@.service.d/autologin.conf"
# Rates agetty tries on serial lines, as in the stock serial-getty unit.
KEEP_BAUD = (115200, 57600, 38400, 9600)
_VIRTUAL_TERMINAL = re.compile(r"tty\d+$")
_UNIT_DIRS = (systemd.SYSTEM_DIR, "/run/systemd/system",
              "/usr/local/lib/systemd/system", *systemd.VENDOR_DIRS)


def _is_vt_console(token: str) -> bool:
    return bool(_VIRTUAL_TERMINAL.match(cmdline.value(token).split(",")[0]))


def _getty_files(ctx: Context) -> list[str]:
    """Serial getty fragment and drop-ins, in systemd's application order."""
    template = "serial-getty@.service"
    names = [template]
    device = ctx.platform.serial_console_device(ctx)
    if device:
        resolved = ctx.target.realpath(f"/dev/{device}").removeprefix("/dev/")
        if re.fullmatch(r"tty(?:AMA|S)\d+", resolved):
            names.insert(0, f"serial-getty@{resolved}.service")
        elif device.startswith("serial"):
            ctx.plan.note(f"/dev/{device} cannot be resolved; instance-specific "
                          "serial getty settings could not be imported")

    fragment = next((f"{directory}/{name}" for name in names
                     for directory in _UNIT_DIRS
                     if ctx.plan.exists(f"{directory}/{name}")), None)
    if fragment and (ctx.plan.readlink(fragment) == "/dev/null"
                     or ctx.plan.read(fragment) == b""):
        return []
    dropin_names = [*names, "serial-.service"]
    if len(names) > 1:
        dropin_names += [f"serial-@{resolved}.service", "serial-@.service"]
    dropin_names.append("service")
    # Same-name drop-ins: /etc wins over /run and vendor directories;
    # within each directory the instance wins over the template/prefix.
    # Different filenames are then applied in lexical order across all dirs.
    dropins = {}
    for directory in _UNIT_DIRS:
        for name in dropin_names:
            path = f"{directory}/{name}.d"
            for filename in ctx.target.listdir(path):
                if filename.endswith(".conf"):
                    dropins.setdefault(filename, f"{path}/{filename}")
    return ([fragment] if fragment else []) + [dropins[n] for n in sorted(dropins)]


class SerialConsole(Hook):
    title = "Serial console"
    symbol = "SERIAL_CONSOLE"
    order = 20

    def read(self, ctx: Context) -> dict[str, str]:
        values = ctx.platform.read_serial_console(ctx)
        if values.get("SERIAL_CONSOLE") != "y":
            return values
        # Follow serial0 -> ttyAMA10, for example, to inspect the getty
        # instance used by systemd-getty-generator, without running agetty.
        start = ""
        for path in _getty_files(ctx):
            section = ""
            for raw in (ctx.plan.read_text(path) or "").splitlines():
                line = raw.strip()
                if line.startswith("["):
                    section = line
                elif section == "[Service]" and line.startswith("ExecStart="):
                    start = line.partition("=")[2]
        values["SERIAL_CONSOLE_AUTOLOGIN"] = "n"
        try:
            argv = shlex.split(start)
        except ValueError:
            ctx.plan.note("serial getty command could not be imported")
            return values
        for i, arg in enumerate(argv):
            if arg.startswith("--autologin="):
                user = arg.partition("=")[2]
            elif arg in ("--autologin", "-a") and i + 1 < len(argv):
                user = argv[i + 1]
            else:
                continue
            values.update(SERIAL_CONSOLE_AUTOLOGIN="y", SERIAL_CONSOLE_AUTOLOGIN_USER=user)
        return values

    def plan(self, ctx: Context) -> None:
        cfg = ctx.config
        snapshot = ctx.plan.snapshot()
        baud = cfg.int("SERIAL_CONSOLE_BAUD")
        device = ctx.platform.setup_serial_console(ctx)
        serial = f"console={device},{baud}"
        primary = cfg.enabled("SERIAL_CONSOLE_PRIMARY")
        verbose = cfg.enabled("SERIAL_CONSOLE_VERBOSE")

        def edit(tokens: list[str]) -> list[str]:
            # Keep display consoles, replace serial ones with ours.
            vts = [t for t in tokens if cmdline.key(t) == "console" and _is_vt_console(t)]
            rest = cmdline.without(tokens, "console")
            consoles = vts + [serial] if primary else [serial] + vts
            if verbose:
                rest = cmdline.without(rest, "quiet", "splash")
                rest = cmdline.set_param(rest, "loglevel",
                                         cfg.int("SERIAL_CONSOLE_LOGLEVEL"))
            return consoles + rest

        ctx.platform.edit_kernel_cmdline(ctx, edit)
        self._autologin(ctx, baud)
        if ctx.plan.changed_since(snapshot):
            ctx.plan.note("serial console settings take effect after a reboot")

    def _autologin(self, ctx: Context, baud: int) -> None:
        if not ctx.config.enabled("SERIAL_CONSOLE_AUTOLOGIN"):
            ctx.plan.remove(AUTOLOGIN_DROPIN)
            systemd.daemon_reload(ctx.plan, [AUTOLOGIN_DROPIN])
            return
        user = ctx.config.str("SERIAL_CONSOLE_AUTOLOGIN_USER")
        if not user:
            raise Error("SERIAL_CONSOLE_AUTOLOGIN_USER must not be empty")
        if accounts.lookup(ctx.plan, user) is None:
            # Raspberry Pi OS creates its first user on first boot.
            ctx.plan.note(f"user {user} does not exist yet; serial autologin "
                          "fails until it is created")
        bauds = ",".join(str(b) for b in dict.fromkeys((baud, *KEEP_BAUD)))
        # The template drop-in covers whichever UART the console ends up on.
        ctx.plan.write(AUTOLOGIN_DROPIN,
                       "# Generated by board-tune\n"
                       "[Service]\n"
                       "ExecStart=\n"
                       f"ExecStart=-/sbin/agetty --autologin {user} "
                       f"--keep-baud {bauds} - $TERM\n",
                       mode=0o644, owner=(0, 0))
        systemd.daemon_reload(ctx.plan, [AUTOLOGIN_DROPIN])


HOOK = SerialConsole()
