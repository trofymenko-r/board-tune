"""NetworkManager inventory shared by readback and the interactive manager."""

from __future__ import annotations

from dataclasses import dataclass, field

from board_tune.errors import Error
from board_tune.hooks.network import _first, _sections

PROFILE_DIRS = ("/etc/NetworkManager/system-connections",
                "/run/NetworkManager/system-connections",
                "/usr/lib/NetworkManager/system-connections")

POWERSAVE = {'default': '0', 'ignore': '1', 'disable': '2', 'enable': '3'}
POWERSAVE_LABELS = {'keep': 'Keep existing setting', 'disable': 'Disabled', 'enable': 'Enabled',
                    'default': 'NetworkManager default', 'ignore': 'Leave driver state unchanged'}


def powersave_mode(section):
    value = _first(section, 'powersave')
    return next((mode for mode, number in POWERSAVE.items() if number == value), 'keep')


def power_status(target, iface):
    """Driver state is distinct from a saved profile's requested policy."""
    if not target.live:
        return 'unavailable (offline)'
    iw = next((p for p in ('/usr/sbin/iw', '/usr/bin/iw', '/sbin/iw') if target.exists(p)), None)
    if iw:
        try:
            out = target.run(['env', 'LC_ALL=C', 'timeout', '8', iw, 'dev', iface, 'get', 'power_save'])
            for line in out.splitlines():
                if line.strip() in ('Power save: on', 'Power save: off'):
                    return line.strip().split(': ', 1)[1]
        except Error:
            pass
    return 'unavailable (iw/driver support required)'


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
    valid_ssid: bool = True

    @property
    def editable(self):
        return (self.valid_ssid and self.path.startswith(PROFILE_DIRS[0] + "/")
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
            info = target.stat(path)
            if info is None or info.kind not in ('file', 'link'):
                continue
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
            ssid = _ssid(_first(wifi, 'ssid'))
            found[key] = Profile(identifier, _unescape(_first(connection, "id", filename)),
                                 ssid or "<non-UTF8 SSID>",
                                 _first(connection, "interface-name"), _first(security, "key-mgmt", "open"),
                                 path, text, active.get(identifier, ""), priority,
                                 _first(connection, "autoconnect", "true").lower() not in ("false", "no", "0"),
                                 _first(wifi, "mode", "infrastructure"), valid_ssid=ssid is not None)
    # Include daemon-only profiles too, without requesting any secrets.
    if available(target):
        try:
            rows = nmcli(target, "-f", "NAME,UUID,TYPE,DEVICE", "connection", "show")
            for line in rows.splitlines():
                row = fields(line)
                if len(row) != 4 or row[2] not in ("wifi", "802-11-wireless") or row[1] in found:
                    continue
                ssid = fields(nmcli(target, "-g", "802-11-wireless.ssid", "connection", "show", "uuid", row[1]).rstrip('\n'))[0]
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


def radio_status(target):
    if not target.live:
        return 'Offline: radio status unavailable'
    row = fields(nmcli(target, '-f', 'WIFI,WIFI-HW', 'general', 'status').strip())
    return f'Radio: {row[0]}; hardware: {row[1]}' if len(row) == 2 else 'Radio status unavailable'


@dataclass
class AccessPoint:
    ssid: str
    security: str
    signal: int
    freq: str
    bssid: str
    channel: str
    active: bool = False


def scan(target, iface, rescan=True):
    if not target.live:
        raise Error('Scanning is unavailable for an offline target')
    if rescan:
        radio = fields(nmcli(target, '-f', 'WIFI,WIFI-HW', 'general', 'status').strip())
        if len(radio) == 2:
            if radio[1] != 'enabled':
                raise Error('Wi-Fi is blocked by hardware; an empty scan does not mean there are no networks')
            if radio[0] != 'enabled':
                raise Error('The Wi-Fi radio is disabled in NetworkManager; enable it before scanning')
    output = nmcli(target, '-f', 'IN-USE,SSID,SIGNAL,SECURITY,FREQ,BSSID,CHAN',
                   'device', 'wifi', 'list', 'ifname', iface,
                   '--rescan', 'yes' if rescan else 'no', wait=15)
    result = []
    for line in output.splitlines():
        row = fields(line)
        if len(row) != 7:
            continue
        try:
            signal = int(row[2])
        except ValueError:
            continue
        result.append(AccessPoint(row[1], row[3], signal, row[4], row[5], row[6], row[0] == '*'))
    return sorted(result, key=lambda ap: (not ap.active, -ap.signal, ap.ssid, ap.bssid))


def groups(points):
    grouped = {}
    for point in points:
        grouped.setdefault((point.ssid, point.security), []).append(point)
    return list(grouped.values())


def matching(profiles, ap, iface):
    def compatible(auth):
        if not ap.security or ap.security == '--':
            return auth == 'open'
        if any(word in ap.security for word in ('802.1X', 'Enterprise')):
            return auth in ('wpa-eap', 'wpa-eap-suite-b-192', 'ieee8021x')
        return auth == 'wpa-psk' or auth == 'sae' and 'WPA3' in ap.security
    return [p for p in profiles if p.ssid == ap.ssid and (not p.iface or p.iface == iface) and compatible(p.auth)]


def band(freq):
    try:
        mhz = int(freq.split()[0])
        return '2.4 GHz' if mhz < 3000 else '5 GHz' if mhz < 5925 else '6 GHz'
    except (ValueError, IndexError):
        return freq


def update_keyfile(text, updates):
    """Update only explicitly edited keys, retaining comments/unknown settings."""
    remaining = {section: dict(values) for section, values in updates.items()}
    lines, section = [], ''
    def finish():
        for key, value in remaining.pop(section, {}).items():
            if value is not None:
                lines.append(f'{key}={value}')
    for raw in text.splitlines():
        stripped = raw.strip()
        if stripped.startswith('[') and stripped.endswith(']'):
            finish()
            section = stripped[1:-1]
        elif '=' in stripped and not stripped.startswith(('#', ';')):
            key = stripped.split('=', 1)[0].strip()
            if key in remaining.get(section, {}):
                continue
        lines.append(raw)
    finish()
    for section in list(remaining):
        lines += ['', f'[{section}]']
        finish()
    return '\n'.join(lines).rstrip() + '\n'


@dataclass
class Editor:
    name: str = ''
    ssid: str = ''
    iface: str = 'wlan0'
    auth: str = 'wpa-psk'
    password: str | None = field(default=None, repr=False)
    hidden: bool = False
    autoconnect: bool = True
    priority: int = 0
    method: str = 'auto'
    address: str = ''
    gateway: str = ''
    dns: str = ''
    powersave: str = 'keep'

    @classmethod
    def from_profile(cls, profile):
        from board_tune.hooks.wifi import _unescape
        data = _sections(profile.text)
        wireless = data.get('wifi', data.get('802-11-wireless', {}))
        ip = data.get('ipv4', {})
        address, _, gateway = _first(ip, 'address1').partition(',')
        if (_first(ip, 'method', 'auto') not in ('auto', 'manual')
                or _first(ip, 'method', 'auto') == 'auto' and address
                or any(k.startswith('address') and k != 'address1' for k in ip)):
            raise Error('This profile has complex IP settings; edit it with NetworkManager directly')
        return cls(profile.name, profile.ssid, profile.iface, profile.auth,
                   hidden=_first(wireless, 'hidden').lower() in ('true', 'yes', '1'),
                   autoconnect=profile.autoconnect, priority=profile.priority,
                   method=_first(ip, 'method', 'auto'), address=address,
                   gateway=_first(ip, 'gateway', gateway), dns=_first(ip, 'dns').replace(';', ' ').strip(),
                   powersave=powersave_mode(wireless))

    def render(self, profile=None):
        import ipaddress
        import re
        import uuid
        from board_tune.hooks.wifi import _escape

        if (not 1 <= len(self.ssid.encode()) <= 32
                or any(ord(c) < 32 or ord(c) == 127 for c in self.ssid)):
            raise Error('SSID must contain 1..32 UTF-8 bytes without control characters')
        if self.iface and not re.fullmatch(r'[A-Za-z0-9_.:-]{1,15}', self.iface):
            raise Error('Invalid interface name')
        if not self.name:
            self.name = self.ssid
        if self.auth not in ('open', 'wpa-psk', 'sae') or not -999 <= self.priority <= 999:
            raise Error('Unsupported security or priority outside -999..999')
        data = _sections(profile.text if profile else '')
        wsection = '802-11-wireless' if '802-11-wireless' in data else 'wifi'
        ssection = '802-11-wireless-security' if '802-11-wireless-security' in data else 'wifi-security'
        identifier = profile.uuid if profile and profile.uuid else str(uuid.uuid4())
        password = self.password
        if password is None and profile:
            from board_tune.hooks.wifi import _unescape
            password = _unescape(_first(data.get(ssection, {}), 'psk'))
        if self.auth != 'open':
            if not password:
                raise Error('Enter a password for this network')
            valid = (8 <= len(password) <= 63 and password.isascii()
                     or bool(re.fullmatch('[0-9a-fA-F]{64}', password))) if self.auth == 'wpa-psk' else len(password.encode()) <= 1024
            if not valid or any(ord(c) < 32 or ord(c) == 127 for c in password):
                raise Error('Password does not meet the selected security requirements')
            if profile and self.ssid != profile.ssid and self.password is None:
                raise Error('Enter a password when changing the SSID; stored keys are not copied between networks')
        ip = {'method': self.method, 'address1': None, 'gateway': None, 'dns': None}
        try:
            ip['dns'] = ';'.join(str(ipaddress.IPv4Address(d)) for d in self.dns.split()) + ';' if self.dns else None
        except ValueError:
            raise Error('Invalid IPv4 DNS server') from None
        if self.method == 'manual':
            try:
                if '/' not in self.address:
                    raise ValueError
                ip['address1'] = str(ipaddress.IPv4Interface(self.address))
                ip['gateway'] = str(ipaddress.IPv4Address(self.gateway)) if self.gateway else None
            except ValueError:
                raise Error('Invalid IPv4 address/prefix, gateway or DNS') from None
        elif self.method != 'auto':
            raise Error('Unsupported IPv4 mode')
        # NM's legacy decimal-byte syntax is ambiguous with some literal SSIDs.
        ssid = ''.join(f'{b};' for b in self.ssid.encode()) if re.fullmatch(r'(?:[0-9]+;)+', self.ssid) else _escape(self.ssid)
        updates = {
            'connection': {'id': _escape(self.name), 'uuid': identifier, 'type': 'wifi',
                           'interface-name': self.iface or None,
                           'autoconnect': 'true' if self.autoconnect else 'false',
                           'autoconnect-priority': str(self.priority)},
            wsection: {'ssid': ssid, 'mode': 'infrastructure', 'hidden': 'true' if self.hidden else 'false'},
            ssection: {'key-mgmt': self.auth if self.auth != 'open' else None,
                       'psk': _escape(password) if self.auth != 'open' else None,
                       'psk-flags': '0' if self.auth != 'open' else None,
                       'pmf': ('3' if self.auth == 'sae' else
                               (_first(data.get(ssection, {}), 'pmf') or None)
                               if profile and profile.auth == self.auth else None)},
            'ipv4': ip,
        }
        if self.powersave != 'keep':
            if self.powersave not in POWERSAVE:
                raise Error('Unsupported Wi-Fi power saving mode')
            updates[wsection]['powersave'] = POWERSAVE[self.powersave]
        if profile is None:
            updates['ipv6'] = {'method': 'auto'}
        text = profile.text if profile else ''
        if self.auth == 'open':
            # Even an empty security section makes NM reject an open profile.
            # A legacy wifi.security reference must be removed as well.
            updates.pop(ssection)
            updates[wsection]['security'] = None
            kept, keep = [], True
            for raw in text.splitlines():
                line = raw.strip()
                if line.startswith('[') and line.endswith(']'):
                    keep = line[1:-1] not in ('wifi-security', '802-11-wireless-security')
                if keep:
                    kept.append(raw)
            text = '\n'.join(kept)
        return identifier, update_keyfile(text, updates)


class Session:
    """Private Wi-Fi drafts; no target mutations until the normal apply step."""
    def __init__(self, target):
        self.target = target
        self.edits = {}
        self.originals = {}

    def stage(self, path, text):
        if path not in self.originals:
            info = self.target.stat(path)
            if info and info.kind != 'file':
                raise Error('Wi-Fi profile must be a regular file to edit or forget')
            self.originals[path] = self.target.read(path)
        self.edits[path] = text

    def save(self, editor, profile=None):
        if profile and not profile.editable:
            raise Error('This profile cannot be edited here; its security, mode or storage is unsupported')
        identifier, text = editor.render(profile)
        path = profile.path if profile else f'{PROFILE_DIRS[0]}/board-tune-{identifier}.nmconnection'
        self.stage(path, text)
        return Profile(identifier, editor.name, editor.ssid, editor.iface, editor.auth, path, text,
                       profile.active if profile else '', editor.priority, editor.autoconnect)

    def forget(self, profile):
        if not profile.path.startswith(PROFILE_DIRS[0] + '/'):
            raise Error('Only persistent administrator profiles can be forgotten here')
        if profile.active:
            raise Error('Active profiles must be forgotten with a protected live disconnection')
        self.stage(profile.path, None)

    def overlay(self, profiles):
        result = [p for p in profiles if p.path not in self.edits]
        for path, text in self.edits.items():
            if text is None:
                continue
            data = _sections(text)
            con = data['connection']
            from board_tune.hooks.wifi import _ssid, _unescape
            w = data.get('wifi', data.get('802-11-wireless', {}))
            s = data.get('wifi-security', data.get('802-11-wireless-security', {}))
            previous = next((p for p in profiles if p.path == path), None)
            result.append(Profile(_first(con, 'uuid'), _unescape(_first(con, 'id')), _ssid(_first(w, 'ssid')),
                                  _first(con, 'interface-name'), _first(s, 'key-mgmt', 'open'), path, text,
                                  previous.active if previous else '', int(_first(con, 'autoconnect-priority', '0')),
                                  _first(con, 'autoconnect') != 'false'))
        return sorted(result, key=lambda p: (not bool(p.active), p.name, p.uuid))

    def plan_into(self, plan):
        if not self.edits:
            return
        active = active_connections(self.target) if self.target.live else {}
        for path, text in self.edits.items():
            if self.target.read(path) != self.originals[path]:
                raise Error(f'Wi-Fi profile changed outside this session: {path}; reopen the manager')
            old = _sections((self.originals[path] or b'').decode())
            identifier = _first(old.get('connection', {}), 'uuid')
            if text is None:
                if identifier in active:
                    raise Error('Profile became active; use protected Forget now instead')
                plan.remove(path)
            else:
                plan.write(path, text, mode=0o600, owner=(0, 0), sensitive=True)
        if plan.changed(*self.edits):
            plan.command(['nmcli', 'connection', 'reload'])
            plan.note('Saved Wi-Fi profile changes do not activate a connection')


class LiveTransaction:
    """Controller of an independent board-side, 60-second Wi-Fi transaction."""
    TERMINAL = ('kept', 'reverted', 'failed', 'recovery_failed')

    def __init__(self, session, profile, iface, action='connect'):
        self.session, self.target = session, session.target
        self.profile, self.iface, self.action = profile, iface, action
        self.directory = None
        self.backup_id = None
        self.started_at = None

    def start(self):
        import base64
        import json
        from pathlib import Path
        import uuid
        from board_tune.backup import Backup

        t, p = self.target, self.profile
        if not t.live:
            raise Error('Connect now / Forget active network requires a running board')
        for path in ('/usr/bin/python3', '/usr/bin/systemd-run', '/usr/bin/busctl'):
            if not t.exists(path):
                raise Error('Guarded Wi-Fi changes require python3, systemd-run and busctl; use Save only')
        if p.iface and p.iface != self.iface:
            raise Error('Select the adapter to which this profile is bound')
        if p.active and p.active != self.iface:
            raise Error('This profile is active on another adapter; select that adapter first')
        if not p.uuid:
            raise Error('The selected profile has no UUID')
        if p.mode != 'infrastructure':
            raise Error('Only station profiles can be activated by this manager')
        text = self.session.edits.get(p.path)
        if self.action == 'connect' and p.path in self.session.edits and text is None:
            raise Error('The profile is pending deletion')
        original = t.read(p.path) if p.path else None
        if p.path in self.session.originals and original != self.session.originals[p.path]:
            raise Error('Profile changed outside this session; reopen the manager')
        if self.action == 'forget' and not p.path.startswith(PROFILE_DIRS[0] + '/'):
            raise Error('This profile cannot be forgotten from its current storage')
        backup = Backup.create(t, [p.path] if p.path and (text is not None or self.action == 'forget') else [])
        self.backup_id = backup.id
        identifier = uuid.uuid4().hex
        self.directory = f'/run/board-tune-wifi/{identifier}'
        t.mkdir(self.directory, 0o700, (0, 0))
        request = dict(action=self.action, iface=self.iface, uuid=p.uuid,
                       path=p.path if text is not None or self.action == 'forget' else '', text=text,
                       original=base64.b64encode(original).decode() if original is not None else None)
        t.write(self.directory + '/request.json', json.dumps(request).encode(), 0o600, (0, 0))
        t.write(self.directory + '/worker.py', Path(__file__).with_name('wifi_worker.py').read_bytes(), 0o600, (0, 0))
        import time
        self.started_at = time.monotonic()
        t.run(['systemd-run', '--quiet', '--collect', f'--unit=board-tune-wifi-{identifier}',
               '--property=RuntimeMaxSec=90s', '/usr/bin/python3', self.directory + '/worker.py', self.directory])

    def status(self):
        import json
        import time
        raw = self.target.read(self.directory + '/status.json')
        status = json.loads(raw) if raw else {'state': 'starting', 'remaining': 60}
        if self.started_at is not None:
            status['remaining'] = min(status['remaining'], max(0, int(60 - (time.monotonic() - self.started_at))))
        return status

    def decide(self, keep):
        self.target.write(self.directory + '/decision', b'keep' if keep else b'revert', 0o600, (0, 0))

    def finish(self, status):
        if status['state'] == 'kept':
            self.session.edits.pop(self.profile.path, None)
            self.session.originals.pop(self.profile.path, None)
        if status['state'] in self.TERMINAL:
            self.target.remove(self.directory + '/request.json')
            self.target.remove(self.directory + '/worker.py')
            self.target.remove(self.directory + '/decision')
            # Keep the small status file for auditing; it contains no credentials.
