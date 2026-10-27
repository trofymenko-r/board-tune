"""Host identity, localisation, console keyboard and systemd time sync."""

from __future__ import annotations

import ipaddress
import re
import shlex

from board_tune import systemd
from board_tune.errors import Error
from board_tune.registry import Hook
from . import aliases as shell_aliases

NTP_UNIT = "systemd-timesyncd.service"
NTP_DROPIN = "/etc/systemd/timesyncd.conf.d/90-board-tune.conf"
LOCALE_UNIT = "board-tune-locale.service"
LOCALE_PENDING = "/var/lib/board-tune/locale-pending"
_HOST = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")


def _assignments(text):
    values = {}
    for raw in text.splitlines():
        key, sep, value = raw.partition("=")
        if not sep or not re.fullmatch(r"[A-Z_]+", key.strip()):
            continue
        try:
            parts = shlex.split(value, comments=True)
        except ValueError:
            continue
        if len(parts) == 1:
            values[key.strip()] = parts[0]
    return values


def _edit_assignments(text, values):
    out, done = [], set()
    for raw in text.splitlines():
        key = raw.partition("=")[0].strip()
        if key in values:
            if key not in done:
                out.append(f"{key}={shlex.quote(values[key])}")
                done.add(key)
        else:
            out.append(raw)
    out += [f"{key}={shlex.quote(value)}" for key, value in values.items() if key not in done]
    return "\n".join(out) + "\n"


def _ntp_servers(text):
    values = text.split()
    for server in values:
        try:
            ipaddress.ip_address(server)
        except ValueError:
            if len(server) > 253 or not all(_HOST.fullmatch(label) for label in server.rstrip(".").split(".")):
                raise Error("SYSTEM_NTP_SERVERS: invalid host name or IP address") from None
    return values


class System(Hook):
    title = "System"
    symbol = "SYSTEM"
    order = 50

    def read(self, ctx):
        values = {}
        hostname = (ctx.plan.read_text("/etc/hostname") or "").strip()
        if _HOST.fullmatch(hostname):
            values["SYSTEM_HOSTNAME"] = hostname
        localtime = ctx.target.realpath("/etc/localtime")
        prefix = "/usr/share/zoneinfo/"
        timezone = (localtime[len(prefix):] if localtime.startswith(prefix)
                    else (ctx.plan.read_text("/etc/timezone") or "").strip())
        if timezone and ctx.plan.exists(prefix + timezone):
            values["SYSTEM_TIMEZONE"] = timezone
        language = _assignments(ctx.plan.read_text("/etc/default/locale") or "").get("LANG")
        if language:
            values["SYSTEM_LOCALE"] = language
        keyboard = _assignments(ctx.plan.read_text("/etc/default/keyboard") or "")
        for key, suffix in (("XKBLAYOUT", "LAYOUT"), ("XKBVARIANT", "VARIANT"), ("XKBOPTIONS", "OPTIONS")):
            if keyboard.get(key):
                values["SYSTEM_KEYBOARD_" + suffix] = keyboard[key]
        if systemd.find_unit(ctx.plan, NTP_UNIT):
            enabled = systemd.is_enabled(ctx.plan, NTP_UNIT)
            if enabled:
                values["SYSTEM_NTP_ENABLE"] = "y"
            elif ctx.plan.readlink(f"{systemd.SYSTEM_DIR}/{NTP_UNIT}") == "/dev/null":
                values["SYSTEM_NTP_DISABLE"] = "y"
            if enabled:
                text = ctx.plan.read_text(NTP_DROPIN) or ""
                match = re.search(r"(?m)^NTP=(.*)$", text)
                if match:
                    values["SYSTEM_NTP_SERVERS"] = match.group(1)
        values.update(shell_aliases.read(ctx.plan))
        return {"SYSTEM": "y", **values} if values else {"SYSTEM": "n"}

    def plan(self, ctx):
        cfg, plan = ctx.config, ctx.plan
        hostname = cfg.str("SYSTEM_HOSTNAME")
        if hostname:
            if not _HOST.fullmatch(hostname):
                raise Error("SYSTEM_HOSTNAME: expected a lowercase hostname, at most 63 characters")
            plan.write("/etc/hostname", hostname + "\n", mode=0o644, owner=(0, 0))
            hosts, found = [], False
            for line in (plan.read_text("/etc/hosts") or "").splitlines():
                tokens = line.split("#", 1)[0].split()
                if tokens and tokens[0] == "127.0.1.1":
                    aliases = [name for name in tokens[2:] if name != hostname]
                    comment = " #" + line.partition("#")[2] if "#" in line else ""
                    line = "127.0.1.1\t" + " ".join([hostname, *aliases]) + comment
                    found = True
                hosts.append(line)
            if not found:
                hosts.append(f"127.0.1.1\t{hostname}")
            plan.write("/etc/hosts", "\n".join(hosts) + "\n", mode=0o644, owner=(0, 0))
            if plan.changed("/etc/hostname"):
                plan.command(["hostnamectl", "set-hostname", hostname])
        timezone = cfg.str("SYSTEM_TIMEZONE")
        if timezone:
            if (not re.fullmatch(r"[A-Za-z0-9_+/-]+", timezone) or ".." in timezone.split("/")
                    or timezone.startswith("/") or not plan.exists("/usr/share/zoneinfo/" + timezone)):
                raise Error("SYSTEM_TIMEZONE: zone must be installed under /usr/share/zoneinfo")
            plan.symlink("/etc/localtime", "/usr/share/zoneinfo/" + timezone)
            # Keep the legacy Debian text file in sync if it is present.
            if plan.exists("/etc/timezone"):
                plan.write("/etc/timezone", timezone + "\n", mode=0o644, owner=(0, 0))
        if cfg.str("SYSTEM_LOCALE"):
            self._locale(ctx)
        layout = cfg.str("SYSTEM_KEYBOARD_LAYOUT")
        if layout:
            values = {"XKBLAYOUT": layout, "XKBVARIANT": cfg.str("SYSTEM_KEYBOARD_VARIANT"),
                      "XKBOPTIONS": cfg.str("SYSTEM_KEYBOARD_OPTIONS")}
            if any(not re.fullmatch(r"[A-Za-z0-9_,:+-]*", value) for value in values.values()):
                raise Error("keyboard settings contain invalid characters")
            path = "/etc/default/keyboard"
            plan.write(path, _edit_assignments(plan.read_text(path) or "", values), mode=0o644, owner=(0, 0))
            if plan.changed(path):
                plan.note("keyboard settings take effect after a reboot or console reconfiguration")
        self._ntp(ctx)
        shell_aliases.configure(ctx)

    def _locale(self, ctx):
        cfg, plan = ctx.config, ctx.plan
        locale = re.sub(r"\.utf-?8", ".UTF-8", cfg.str("SYSTEM_LOCALE"), flags=re.IGNORECASE)
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.@-]{0,63}", locale):
            raise Error("SYSTEM_LOCALE: invalid locale name")
        path = "/etc/default/locale"
        plan.write(path, _edit_assignments(plan.read_text(path) or "", {"LANG": locale}), mode=0o644, owner=(0, 0))
        if locale in ("C", "C.UTF-8", "C.utf8", "POSIX"):
            return
        if ".UTF-8" not in locale:
            raise Error("SYSTEM_LOCALE: select a UTF-8 locale")
        original = plan.read_text("/etc/locale.gen")
        if original is None or not plan.exists("/usr/sbin/locale-gen"):
            raise Error("locale generation requires the locales package on the target")
        supported = plan.read_text("/usr/share/i18n/SUPPORTED") or original.replace("#", "")
        if locale not in {line.split()[0] for line in supported.splitlines() if line.split()}:
            raise Error(f"SYSTEM_LOCALE: {locale} is not supported by the target")
        lines, found = [], False
        for line in original.splitlines():
            parts = line.lstrip().lstrip("#").split()
            if parts and parts[0] == locale:
                line, found = f"{locale} UTF-8", True
            lines.append(line)
        if not found:
            lines.append(f"{locale} UTF-8")
        plan.write("/etc/locale.gen", "\n".join(lines) + "\n", mode=0o644, owner=(0, 0))
        if plan.changed("/etc/locale.gen"):
            if ctx.target.live:
                plan.command(["/usr/sbin/locale-gen"])
            else:
                unit_path = f"{systemd.SYSTEM_DIR}/{LOCALE_UNIT}"
                plan.write(unit_path, "# Generated by board-tune\n[Unit]\n"
                           "Description=Generate configured locales once\n"
                           f"ConditionPathExists={LOCALE_PENDING}\nAfter=local-fs.target\n"
                           "Before=systemd-user-sessions.service\n"
                           "[Service]\nType=oneshot\nExecStart=/usr/sbin/locale-gen\n"
                           f"ExecStartPost=/bin/rm -f {LOCALE_PENDING}\n"
                           "[Install]\nWantedBy=multi-user.target\n", mode=0o644, owner=(0, 0))
                plan.write(LOCALE_PENDING, locale + "\n", mode=0o600, owner=(0, 0))
                systemd.enable(plan, LOCALE_UNIT)
                plan.note("locale generation is scheduled for the next boot")
        if plan.changed(path):
            plan.note("the default language applies to subsequent logins")

    def _ntp(self, ctx):
        cfg, plan = ctx.config, ctx.plan
        if cfg.enabled("SYSTEM_NTP_KEEP"):
            return
        mask = f"{systemd.SYSTEM_DIR}/{NTP_UNIT}"
        if cfg.enabled("SYSTEM_NTP_ENABLE"):
            links = systemd.enable(plan, NTP_UNIT)
            servers = _ntp_servers(cfg.str("SYSTEM_NTP_SERVERS"))
            if servers:
                plan.write(NTP_DROPIN, "# Generated by board-tune\n[Time]\nNTP=" + " ".join(servers) + "\n", mode=0o644, owner=(0, 0))
            else:
                plan.remove(NTP_DROPIN)
            systemd.daemon_reload(plan, links)
            if plan.changed(*links, NTP_DROPIN):
                plan.command(["systemctl", "restart", NTP_UNIT])
        else:
            if not systemd.find_unit(plan, NTP_UNIT):
                raise Error("systemd-timesyncd is not installed on the target")
            links = systemd.disable(plan, NTP_UNIT)
            plan.symlink(mask, "/dev/null")
            systemd.daemon_reload(plan, [*links, mask])
            if plan.changed(*links, mask):
                plan.command(["systemctl", "stop", NTP_UNIT])


HOOK = System()
