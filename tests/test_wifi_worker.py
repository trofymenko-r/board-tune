import base64
import json
import time

import pytest

from board_tune import wifi_worker as module

UUID = '379a7650-3111-4256-a42a-d0c1e270c817'
OLD = 'aebba988-278f-4779-b7de-559eddfc93ee'


@pytest.fixture
def worker(tmp_path, monkeypatch):
    root = tmp_path / 'profiles'
    root.mkdir()
    monkeypatch.setattr(module, 'ROOT', str(root) + '/')
    path = root / 'Lab.nmconnection'
    original = b'[connection]\nuuid=379a7650-3111-4256-a42a-d0c1e270c817\nautoconnect=true\n[wifi]\nssid=Old\n'
    path.write_bytes(original)
    path.chmod(0o640)
    directory = tmp_path / 'job'
    directory.mkdir()
    request = dict(action='connect', iface='wlan0', uuid=UUID, path=str(path),
                   original=base64.b64encode(original).decode(),
                   text=f'[connection]\nuuid={UUID}\nautoconnect=true\n[wifi]\nssid=New\n')
    (directory / 'request.json').write_text(json.dumps(request))
    w = module.Worker(directory)
    w.calls = []
    w.current = OLD
    def nm(*args, **kwargs):
        w.calls.append(args)
        if 'GENERAL.TYPE' in args:
            return 'wifi'
        if 'GENERAL.CON-UUID' in args:
            return w.current
        if 'GENERAL.DBUS-PATH' in args:
            return '/org/freedesktop/NetworkManager/Devices/3'
        if 'up' in args:
            w.current = UUID
        if args[:2] == ('connection', 'delete'):
            path.unlink(missing_ok=True)
            w.current = ''
        return 'GENERAL.STATE:100 (connected)'
    def call(method, *args):
        w.calls.append((method, *args))
        if method == 'CheckpointCreate':
            return ['/checkpoint/1']
        if method == 'CheckpointRollback':
            w.current = OLD
            return [{'/device/3': 0}]
    monkeypatch.setattr(w, 'nm', nm)
    monkeypatch.setattr(w, 'call', call)
    return w, path, original


def state(w):
    return json.loads((w.directory / 'status.json').read_text())['state']


def test_kept_profile_has_original_uuid_and_desired_autoconnect(worker):
    w, path, _ = worker
    (w.directory / 'decision').write_text('keep')
    w.prepare()
    w.activate()
    assert state(w) == 'kept' and path.read_text() == w.request['text']
    assert UUID in path.read_text() and 'autoconnect=true' in path.read_text()
    assert not any(c[0] == 'CheckpointRollback' for c in w.calls)


def test_revert_restores_profile_bytes_permissions_and_runtime(worker):
    w, path, original = worker
    (w.directory / 'decision').write_text('revert')
    w.prepare()
    w.activate()
    assert state(w) == 'reverted'
    assert path.read_bytes() == original and path.stat().st_mode & 0o777 == 0o640
    assert w.current == OLD


def test_new_profile_without_confirmation_is_removed(worker):
    w, path, _ = worker
    path.unlink()
    w.request['original'] = None
    w.prepare()
    w.deadline = time.monotonic() - 1
    w.activate()
    assert state(w) == 'reverted' and not path.exists()
    assert ('connection', 'delete', 'uuid', UUID) in w.calls
    assert not any('DELETE_NEW_CONNECTIONS' in c for c in w.calls)


def test_activation_failure_can_restore_persistent_file(worker, monkeypatch):
    w, path, original = worker
    nm = w.nm
    def fail(*args, **kwargs):
        if 'up' in args:
            assert 'autoconnect=false' in path.read_text()
            raise RuntimeError('Authentication failed')
        return nm(*args, **kwargs)
    monkeypatch.setattr(w, 'nm', fail)
    w.prepare()
    with pytest.raises(RuntimeError, match='Authentication'):
        w.activate()
    w.revert()
    assert state(w) == 'reverted' and path.read_bytes() == original


@pytest.mark.parametrize('decision, exists', [('keep', False), ('revert', True)])
def test_active_forget_keep_or_restore(worker, decision, exists):
    w, path, original = worker
    w.request['action'] = 'forget'
    w.request['text'] = None
    (w.directory / 'decision').write_text(decision)
    w.prepare()
    w.activate()
    assert path.exists() == exists
    if exists:
        assert path.read_bytes() == original
    assert ('connection', 'delete', 'uuid', UUID) in w.calls


def test_concurrent_edit_detected_before_checkpoint(worker):
    w, path, _ = worker
    path.write_text('external update')
    with pytest.raises(RuntimeError, match='changed outside'):
        w.prepare()
    assert w.cp is None and not w.changed
    assert path.read_text() == 'external update'


def test_missing_checkpoint_does_not_modify_profile(worker, monkeypatch):
    w, path, original = worker
    def fail(*args):
        raise RuntimeError('checkpoint unavailable')
    monkeypatch.setattr(w, 'call', fail)
    with pytest.raises(RuntimeError, match='checkpoint unavailable'):
        w.prepare()
    assert path.read_bytes() == original and not w.changed
