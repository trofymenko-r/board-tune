"""NetworkManager inventory shared by readback and the interactive manager."""

from __future__ import annotations

from dataclasses import dataclass, field

from board_tune.errors import Error
from board_tune.hooks.network import _first, _sections

PROFILE_DIRS = ("/etc/NetworkManager/system-connections",
                "/run/NetworkManager/system-connections",
                "/usr/lib/NetworkManager/system-connections")


def fields(line):
    """Split nmcli's escaped terse fields, including escaped colons/backslashes."""
    out, current, escaped = [], [], False
    for char in line:
        if escaped:
            current.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == ":":
            out.append("".join(current))
            current = []
        else:
            current.append(char)
    if escaped:
        current.append("\\")
    return out + ["".join(current)]


def nmcli(target, *args, wait=10):
    return target.run(["env", "LC_ALL=C", "timeout", str(wait + 3),
                       "nmcli", "-w", str(wait), "-t", "-e", "yes", *args])


def available(target):
    return target.live and any(target.exists(p) for p in ("/usr/bin/nmcli", "/bin/nmcli"))


def active_connections(target):
    if not available(target):
        return {}
    out = nmcli(target, "-f", "UUID,TYPE,DEVICE", "connection", "show", "--active")
    return {row[0]: row[2] for line in out.splitlines()
            if len(row := fields(line)) == 3 and row[1] in ("wifi", "802-11-wireless")}


@dataclass
class Profile:
    uuid: str
    name: str
    ssid: str
    iface: str
    auth: str
    path: str = ""
    text: str = field(default="", repr=False)
    active: str = ""
    priority: int = 0
    autoconnect: bool = True
    mode: str = "infrastructure"

    @property
    def editable(self):
        return (self.path.startswith(PROFILE_DIRS[0] + "/")
                and self.mode == "infrastructure" and self.auth in ("open", "wpa-psk", "sae"))


def inventory(target, notes=None):
    from board_tune.hooks.wifi import _ssid, _unescape

    notes = notes if notes is not None else []
    active = {}
    try:
        active = active_connections(target)
    except Error:
        notes.append("NetworkManager active Wi-Fi state unavailable; showing saved profiles")
    found = {}
    for directory in PROFILE_DIRS:
        for filename in target.listdir(directory):
            if filename.startswith(".") or filename.endswith(("~", ".bak")):
                continue
            path = f"{directory}/{filename}"
            text = (target.read(path) or b"").decode(errors="replace")
            data = _sections(text)
            connection = data.get("connection", {})
            if _first(connection, "type") not in ("wifi", "802-11-wireless"):
                continue
            wifi = data.get("wifi", data.get("802-11-wireless", {}))
            security = data.get("wifi-security", data.get("802-11-wireless-security", {}))
            identifier = _first(connection, "uuid")
            # UUID-less offline keyfiles still have a stable filesystem identity.
            key = identifier or path
            if key in found:
                continue
            try:
                priority = int(_first(connection, "autoconnect-priority", "0"))
            except ValueError:
                priority = 0
            found[key] = Profile(identifier, _unescape(_first(connection, "id", filename)),
                                 _ssid(_first(wifi, "ssid")) or "<non-UTF8 SSID>",
                                 _first(connection, "interface-name"), _first(security, "key-mgmt", "open"),
                                 path, text, active.get(identifier, ""), priority,
                                 _first(connection, "autoconnect", "true").lower() not in ("false", "no", "0"),
                                 _first(wifi, "mode", "infrastructure"))
    # Include daemon-only profiles too, without requesting any secrets.
    if available(target):
        try:
            rows = nmcli(target, "-f", "NAME,UUID,TYPE,DEVICE", "connection", "show")
            for line in rows.splitlines():
                row = fields(line)
                if len(row) != 4 or row[2] not in ("wifi", "802-11-wireless") or row[1] in found:
                    continue
                ssid = nmcli(target, "-g", "802-11-wireless.ssid", "connection", "show", "uuid", row[1]).strip()
                found[row[1]] = Profile(row[1], row[0], ssid, "", "unknown", active=active.get(row[1], ""))
        except Error:
            notes.append("NetworkManager profile inventory incomplete; saved files remain visible")
    return sorted(found.values(), key=lambda p: (not bool(p.active), -p.priority, p.name, p.uuid, p.path))


def device_status(target, iface):
    if not target.live:
        return ["Offline target: connection status and scanning unavailable"]
    output = nmcli(target, "-f", "GENERAL.STATE,GENERAL.CONNECTION,IP4.ADDRESS,IP4.GATEWAY,IP4.DNS",
                   "device", "show", iface)
    return ["  ".join(fields(line)) for line in output.splitlines() if line]
