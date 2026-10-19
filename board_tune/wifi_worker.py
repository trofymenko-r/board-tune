"""Standalone root-side Wi-Fi transaction worker (stdlib only).

Copied to a private /run directory and launched by systemd-run. No secrets
are passed in argv or emitted into the journal. This worker owns file
recovery even if its SSH controller disappears; NM owns runtime rollback.
"""
from __future__ import annotations

import base64
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

BUS = ['busctl', '--json=short', 'call', 'org.freedesktop.NetworkManager',
       '/org/freedesktop/NetworkManager', 'org.freedesktop.NetworkManager']
ROOT = '/etc/NetworkManager/system-connections/'


def atomic(path, data, mode=0o600, owner=None):
    path = Path(path)
    temporary = path.with_name(path.name + '.board-tune-tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, mode)
    try:
        os.fchmod(fd, mode)
        if owner:
            os.fchown(fd, *owner)
        with os.fdopen(fd, 'wb', closefd=False) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(temporary, path)


class Worker:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.request = json.loads((self.directory / 'request.json').read_text())
        self.cp = None
        self.changed = False
        self.committed = False
        self.deadline = None
        self.trial = None
        self.original = None
        self.attrs = None

    def run(self, argv, timeout=10):
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                              env=dict(os.environ, LC_ALL='C'))
        if proc.returncode:
            if 'up' in argv:
                reason = proc.stderr.strip()[-400:]
                for source in (self.request.get('text') or '', (self.original or b'').decode(errors='replace')):
                    for key in re.findall(r'(?m)^psk=(.*)$', source):
                        if key:
                            reason = reason.replace(key, '[hidden]')
                            unescaped = re.sub(r'\\([\\snrt])', lambda m: {'\\': '\\', 's': ' ', 'n': '\n', 'r': '\r', 't': '\t'}[m[1]], key)
                            reason = reason.replace(unescaped, '[hidden]')
                raise RuntimeError('Activation failed: ' + reason)
            raise RuntimeError(f'{argv[0]} operation failed (exit {proc.returncode})')
        return proc.stdout

    def nm(self, *args, wait=8):
        return self.run(['nmcli', '-w', str(wait), *args], timeout=wait + 3)

    def call(self, method, *args):
        raw = self.run([*BUS, method, *args])
        return json.loads(raw)['data'] if raw.strip() else None

    def status(self, state, **extra):
        atomic(self.directory / 'status.json', json.dumps(dict(state=state,
               remaining=max(0, int(self.deadline - time.monotonic())) if self.deadline else 60,
               **extra)).encode())

    def selected_uuid(self):
        value = self.nm('-g', 'GENERAL.CON-UUID', 'device', 'show', self.request['iface']).strip()
        return '' if value == '--' else value

    def prepare(self):
        r = self.request
        if (r.get('action') not in ('connect', 'forget')
                or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,15}', r.get('iface', ''))
                or not re.fullmatch(r'[0-9a-fA-F-]{36}', r.get('uuid', ''))):
            raise RuntimeError('Invalid Wi-Fi transaction')
        if self.nm('-g', 'GENERAL.TYPE', 'device', 'show', r['iface']).strip() != 'wifi':
            raise RuntimeError('Selected device is not a Wi-Fi adapter')
        self.old_uuid = self.selected_uuid()
        self.path = Path(r['path']) if r.get('path') else None
        if self.path:
            if (str(self.path.parent) + '/' != ROOT or self.path.is_symlink()
                    or not self.path.name.endswith('.nmconnection')):
                raise RuntimeError('Unsupported profile storage for live edits')
            self.original = self.path.read_bytes() if self.path.exists() else None
            expected = base64.b64decode(r['original']) if r.get('original') is not None else None
            if self.original != expected:
                raise RuntimeError('Profile changed outside the menu; reopen the manager')
            if self.path.exists():
                st = self.path.stat()
                self.attrs = (st.st_mode & 0o777, (st.st_uid, st.st_gid))
        device = self.nm('-g', 'GENERAL.DBUS-PATH', 'device', 'show', r['iface']).strip()
        self.cp = self.call('CheckpointCreate', 'aouu', '1', device, '60', '0')[0]
        self.deadline = time.monotonic() + 60
        self.status('activating')

    def activate(self):
        r = self.request
        if r['action'] == 'forget':
            if not self.path or not self.path.exists():
                raise RuntimeError('Profile cannot be forgotten from its current storage')
            # Delete exactly this UUID. NM also disconnects it if active.
            self.changed = True
            self.nm('connection', 'delete', 'uuid', r['uuid'])
        else:
            if r.get('text') is not None:
                # Trial autoconnect must not become a preferred failed profile.
                text = r['text']
                section, lines = '', []
                for line in text.splitlines():
                    if line.startswith('['):
                        section = line
                    if section == '[connection]' and line.startswith('autoconnect='):
                        line = 'autoconnect=false'
                    lines.append(line)
                self.trial = ('\n'.join(lines) + '\n').encode()
                self.changed = True
                atomic(self.path, self.trial)
                self.nm('connection', 'load', str(self.path))
            self.nm('connection', 'up', 'uuid', r['uuid'], 'ifname', r['iface'], wait=25)
            if self.selected_uuid() != r['uuid']:
                raise RuntimeError('NetworkManager did not activate the requested profile')
        details = self.nm('-t', '-f', 'GENERAL.STATE,GENERAL.CONNECTION,IP4.ADDRESS,GENERAL.REASON',
                          'device', 'show', r['iface'])
        self.status('confirm', details=details)
        while time.monotonic() < self.deadline - 3:
            decision = self.directory / 'decision'
            if decision.exists():
                if decision.read_text().strip() == 'keep':
                    if r['action'] == 'connect' and self.selected_uuid() != r['uuid']:
                        raise RuntimeError('Connection changed before confirmation')
                    if r.get('text') is not None and r['action'] == 'connect':
                        atomic(self.path, r['text'].encode())
                        self.nm('connection', 'reload')
                    self.call('CheckpointDestroy', 'o', self.cp)
                    self.cp = None
                    self.committed = True
                    self.status('kept', details=details)
                    return
                break
            self.status('confirm', details=details)
            time.sleep(0.2)
        self.revert()

    def revert(self):
        failures = []
        if self.changed and self.path:
            try:
                if self.original is None:
                    self.path.unlink(missing_ok=True)
                    # Remove only our new UUID; never DELETE_NEW_CONNECTIONS globally.
                    try:
                        self.nm('connection', 'delete', 'uuid', self.request['uuid'])
                    except RuntimeError:
                        pass
                else:
                    mode, owner = self.attrs
                    atomic(self.path, self.original, mode, owner)
                self.nm('connection', 'reload')
            except Exception:
                failures.append('persistent profile recovery failed; use the recorded backup')
        if self.cp:
            try:
                result = self.call('CheckpointRollback', 'o', self.cp)
                if result and any(code != 0 for entry in result for code in entry.values()):
                    failures.append('NetworkManager could not restore the old device state')
            except Exception:
                # The board-side NM timeout may already have rolled back.
                try:
                    if self.selected_uuid() != self.old_uuid:
                        failures.append('old connection not restored')
                except Exception:
                    failures.append('cannot verify old connection')
            try:
                self.call('CheckpointDestroy', 'o', self.cp)
            except Exception:
                pass
            self.cp = None
        self.status('recovery_failed' if failures else 'reverted', error='; '.join(failures))

    def execute(self):
        if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,15}', self.request.get('iface', '')):
            self.status('failed', error='Invalid Wi-Fi adapter')
            (self.directory / 'request.json').unlink(missing_ok=True)
            (self.directory / 'worker.py').unlink(missing_ok=True)
            return
        lock = open('/run/board-tune-wifi-' + self.request['iface'] + '.lock', 'w')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.status('failed', error='Another Wi-Fi transaction is running on this adapter')
            (self.directory / 'request.json').unlink(missing_ok=True)
            (self.directory / 'worker.py').unlink(missing_ok=True)
            lock.close()
            return
        try:
            self.prepare()
            self.activate()
        except Exception as error:
            if self.cp or self.changed:
                self.revert()
                # Keep recovery errors prominent; append the original failure.
                state = json.loads((self.directory / 'status.json').read_text())
                self.status(state['state'], error=str(error) + '; ' + state.get('error', ''))
            else:
                self.status('failed', error=str(error))
        finally:
            # Only status/decision remain; remove transported credentials.
            (self.directory / 'request.json').unlink(missing_ok=True)
            (self.directory / 'worker.py').unlink(missing_ok=True)
            lock.close()


if __name__ == '__main__':
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(RuntimeError('Transaction interrupted')))
    Worker(sys.argv[1]).execute()
