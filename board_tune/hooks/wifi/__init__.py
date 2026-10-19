"""NetworkManager station profiles with private credentials and deferred activation."""

from __future__ import annotations

import hashlib
import re
import uuid
from pathlib import Path

from board_tune import systemd
from board_tune.errors import Error
from board_tune.hooks.network import NM_DIR, UUID_NAMESPACE, _first, _network_values, _sections, settings
from board_tune.registry import Hook

COUNTRY_UNIT = "board-tune-wifi.service"
RADIO_UNIT = "board-tune-wifi-enable.service"


def _escape(text):
    return (text.replace("\\", "\\\\").replace(" ", "\\s")
            .replace("\t", "\\t").replace("\n", "\\n").replace("\r", "\\r"))


def _unescape(text):
    return re.sub(r"\\([\\snrt])", lambda match: {"\\": "\\", "s": " ", "n": "\n", "r": "\r", "t": "\t"}[match[1]], text)


def _ssid(value):
    # Read the older decimal-byte representation as well as current strings.
    if re.fullmatch(r"(?:[0-9]+;)+", value):
        try:
            return bytes(int(item) for item in value.rstrip(";").split(";")).decode()
        except (ValueError, UnicodeDecodeError):
            return None
    return _unescape(value)


def profiles(ctx):
    found = []
    for directory in (f"{NM_DIR}/system-connections", "/run/NetworkManager/system-connections"):
        for name in ctx.target.listdir(directory):
            if not name.endswith(".nmconnection"):
                continue
            data = _sections(ctx.plan.read_text(f"{directory}/{name}") or "")
            connection = data.get("connection", {})
            if _first(connection, "type") not in ("wifi", "802-11-wireless"):
                continue
            wifi = data.get("wifi", data.get("802-11-wireless", {}))
            security = data.get("wifi-security", data.get("802-11-wireless-security", {}))
            mode = _first(wifi, "mode", "infrastructure")
            auth = _first(security, "key-mgmt", "open")
            if mode != "infrastructure" or auth not in ("open", "wpa-psk", "sae"):
                ctx.plan.note(f"Wi-Fi profile {name} uses a mode/security type not supported by this menu")
                continue
            ssid = _ssid(_first(wifi, "ssid"))
            if not ssid:
                continue
            iface = _first(connection, "interface-name", "wlan0")
            try:
                priority = int(_first(connection, "autoconnect-priority", "0"))
            except ValueError:
                priority = 0
            found.append((name, ssid, iface, auth, data, priority))
    return found


def _credential(ctx, source, matching, auth):
    if source:
        try:
            with Path(source).expanduser().open() as stream:
                password = stream.read(4097).rstrip("\r\n")
        except OSError as error:
            raise Error(f"WIFI_PASSWORD_FILE: {error.strerror}") from None
    else:
        password = ""
        for profile in matching:
            data = profile[4]
            section = data.get("wifi-security", data.get("802-11-wireless-security", {}))
            stored = _first(section, "psk")
            if stored:
                password = _unescape(stored)
                break
    if not password or any(ord(char) < 32 or ord(char) == 127 for char in password):
        raise Error("WIFI_PASSWORD_FILE is required when no usable key is stored for this SSID/interface")
    if auth == "wpa-psk":
        valid = (8 <= len(password) <= 63 and all(ord(char) < 127 for char in password)
                 or bool(re.fullmatch(r"[0-9a-fA-F]{64}", password)))
    else:
        valid = len(password.encode()) <= 1024
    if not valid:
        raise Error("WIFI_PASSWORD_FILE: password does not meet the selected security mode requirements")
    return password


class Wifi(Hook):
    title = "Wi-Fi"
    symbol = "WIFI"
    order = 35

    def read(self, ctx):
        from board_tune.wifi import active_connections

        try:
            active = active_connections(ctx.target)
        except Error:
            active = {}
            ctx.plan.note("active Wi-Fi connection could not be queried; showing a saved profile")
        candidates = []
        for profile in profiles(ctx):
            name, ssid, iface, auth, data, priority = profile
            ipv4 = data.get("ipv4", {})
            values = _network_values(iface, _first(ipv4, "method"), _first(ipv4, "address1"),
                                     _first(ipv4, "gateway"), _first(ipv4, "dns"))
            if values is None or not -999 <= priority <= 999:
                ctx.plan.note(f"Wi-Fi profile {name} has settings not representable in the menu")
                continue
            values = {key.replace("NETWORK", "WIFI", 1): value for key, value in values.items()}
            wifi = data.get("wifi", data.get("802-11-wireless", {}))
            connection = data.get("connection", {})
            values.update(WIFI_SSID=ssid, WIFI_PRIORITY=str(priority),
                          WIFI_HIDDEN="y" if _first(wifi, "hidden").lower() in ("true", "yes", "1") else "n",
                          WIFI_AUTOCONNECT="n" if _first(connection, "autoconnect").lower() in ("false", "no", "0") else "y")
            values[{"open": "WIFI_AUTH_OPEN", "wpa-psk": "WIFI_AUTH_PSK", "sae": "WIFI_AUTH_SAE"}[auth]] = "y"
            identifier = _first(connection, "uuid")
            candidates.append(((identifier not in active, iface != "wlan0", -priority, name), values))
        if not candidates:
            return {"WIFI": "n"}
        values = min(candidates, key=lambda item: item[0])[1]
        unit = ctx.plan.read_text(f"{systemd.SYSTEM_DIR}/{COUNTRY_UNIT}") or ""
        match = re.search(r"(?m)^ExecStartPre=\S+ reg set ([A-Z]{2})$", unit)
        if match:
            values["WIFI_COUNTRY"] = match.group(1)
        if len(candidates) > 1:
            ctx.plan.note("multiple Wi-Fi profiles found; use the Wi-Fi manager to view all profiles")
        return values

    def plan(self, ctx):
        cfg, plan = ctx.config, ctx.plan
        if not plan.exists(NM_DIR):
            raise Error("Wi-Fi configuration requires NetworkManager on the target")
        ip = settings(cfg, "WIFI")
        ssid = cfg.str("WIFI_SSID")
        if not 1 <= len(ssid.encode()) <= 32 or any(ord(char) < 32 or ord(char) == 127 for char in ssid):
            raise Error("WIFI_SSID must be 1..32 UTF-8 bytes without control characters")
        auth = "open" if cfg.enabled("WIFI_AUTH_OPEN") else "sae" if cfg.enabled("WIFI_AUTH_SAE") else "wpa-psk"
        matching = sorted((p for p in profiles(ctx) if p[1] == ssid and p[2] == ip.iface),
                          key=lambda profile: (-profile[5], profile[0]))
        password = _credential(ctx, cfg.str("WIFI_PASSWORD_FILE"), matching, auth) if auth != "open" else None
        key = f"{ip.iface}:{ssid}"
        name = f"board-tune-wifi-{ip.iface}-{hashlib.sha256(ssid.encode()).hexdigest()[:12]}"
        lines = ["# Generated by board-tune", "[connection]", f"id={name}",
                 f"uuid={uuid.uuid5(UUID_NAMESPACE, key)}", "type=wifi", f"interface-name={ip.iface}",
                 f"autoconnect={'true' if cfg.enabled('WIFI_AUTOCONNECT') else 'false'}",
                 f"autoconnect-priority={cfg.int('WIFI_PRIORITY')}", "", "[wifi]", "mode=infrastructure"]
        # Strings are preferred; use byte syntax only to disambiguate an SSID
        # which itself looks exactly like an old decimal-byte list.
        encoded = ("".join(f"{byte};" for byte in ssid.encode())
                   if re.fullmatch(r"(?:[0-9]+;)+", ssid) else _escape(ssid))
        lines += [f"ssid={encoded}", f"hidden={'true' if cfg.enabled('WIFI_HIDDEN') else 'false'}"]
        if password is not None:
            lines += ["", "[wifi-security]", f"key-mgmt={auth}", "psk-flags=0", f"psk={_escape(password)}"]
            if auth == "sae":
                lines.append("pmf=3")
        lines += ["", "[ipv4]", "method=auto" if ip.address is None else "method=manual"]
        if ip.address is not None:
            lines.append(f"address1={ip.address}")
            if ip.gateway:
                lines.append(f"gateway={ip.gateway}")
            if ip.dns:
                lines.append("dns=" + ";".join(map(str, ip.dns)) + ";")
        lines += ["", "[ipv6]", "method=auto"]
        path = f"{NM_DIR}/system-connections/{name}.nmconnection"
        plan.write(path, "\n".join(lines) + "\n", mode=0o600, owner=(0, 0), sensitive=password is not None)
        self._radio(ctx)
        if plan.changed(path):
            plan.command(["nmcli", "connection", "reload"])
            plan.note(f"Wi-Fi profile for {ssid!r} on {ip.iface} takes effect after a reboot")

    def _radio(self, ctx):
        cfg, plan = ctx.config, ctx.plan
        rfkill = next((path for path in ("/usr/sbin/rfkill", "/usr/bin/rfkill", "/sbin/rfkill") if plan.exists(path)), None)
        if rfkill is None:
            raise Error("rfkill must be installed on the target for Wi-Fi boot configuration")
        country = cfg.str("WIFI_COUNTRY")
        pre = ""
        if country:
            countries = plan.read_text("/usr/share/zoneinfo/iso3166.tab") or ""
            allowed = {line.split()[0] for line in countries.splitlines() if line and not line.startswith("#")}
            if not re.fullmatch("[A-Z]{2}", country) or country not in allowed:
                raise Error("WIFI_COUNTRY must be an ISO country listed in the target's iso3166.tab")
            iw = next((path for path in ("/usr/sbin/iw", "/usr/bin/iw", "/sbin/iw") if plan.exists(path)), None)
            if iw is None:
                raise Error("iw must be installed on the target to set the Wi-Fi country")
            pre = f"ExecStartPre={iw} reg set {country}\n"
        unit_path = f"{systemd.SYSTEM_DIR}/{COUNTRY_UNIT}"
        if not country:
            # Empty means keep a previously managed country, not reset it.
            old = plan.read_text(unit_path) or ""
            match = re.search(r"(?m)^ExecStartPre=\S+ reg set [A-Z]{2}$", old)
            if match:
                pre = match.group() + "\n"
        plan.write(unit_path, "# Generated by board-tune\n[Unit]\n"
                   "Description=Configure Wi-Fi radio before NetworkManager\n"
                   "After=systemd-rfkill.service\nBefore=NetworkManager.service wpa_supplicant.service\n"
                   "[Service]\nType=oneshot\nRemainAfterExit=yes\n" + pre +
                   f"ExecStart={rfkill} unblock wifi\n[Install]\nWantedBy=multi-user.target\n",
                   mode=0o644, owner=(0, 0))
        links = systemd.enable(plan, COUNTRY_UNIT)
        nmcli = next((path for path in ("/usr/bin/nmcli", "/bin/nmcli") if plan.exists(path)), None)
        if nmcli is None:
            raise Error("nmcli must be installed on the target")
        nm_links = systemd.enable(plan, "NetworkManager.service")
        radio_path = f"{systemd.SYSTEM_DIR}/{RADIO_UNIT}"
        plan.write(radio_path, "# Generated by board-tune\n[Unit]\n"
                   "Description=Enable NetworkManager Wi-Fi radio at boot\n"
                   "Requires=NetworkManager.service\nAfter=NetworkManager.service\n"
                   "[Service]\nType=oneshot\nRemainAfterExit=yes\n"
                   f"ExecStart={nmcli} radio wifi on\n"
                   "[Install]\nWantedBy=multi-user.target\n", mode=0o644, owner=(0, 0))
        radio_links = systemd.enable(plan, RADIO_UNIT)
        # Re-enable saved rfkill state on next boot, without touching the
        # currently running radio or activating/restarting NetworkManager.
        for name in ctx.target.listdir("/var/lib/systemd/rfkill"):
            if name.endswith(":wlan"):
                plan.write(f"/var/lib/systemd/rfkill/{name}", "0\n", mode=0o600, owner=(0, 0))
        systemd.daemon_reload(plan, [unit_path, radio_path, *links, *radio_links, *nm_links])
        if plan.changed(unit_path):
            plan.note("Wi-Fi radio/country configuration takes effect after a reboot")


HOOK = Wifi()
