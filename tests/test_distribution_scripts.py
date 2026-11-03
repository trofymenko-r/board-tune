"""Release commands remain local in dry-run mode and preserve earlier builds."""

import importlib.util
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest


@pytest.fixture
def distribution(tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[1] / 'tools/distribution.py'
    spec = importlib.util.spec_from_file_location('distribution_tools', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    files = {'pyproject.toml': 'version = "0.1.0"\nother = "keep"\n',
             'board_tune/__init__.py': '__version__ = "0.1.0"\n',
             'debian/changelog': 'board-tune (0.1.0-1) unstable; urgency=medium\n\nold entry\n',
             'debian/board-tune.1': '.TH BOARD-TUNE "board-tune 0.1.0"\n'}
    for name, text in files.items():
        file = tmp_path / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(text)
    return module


@pytest.mark.parametrize('command', [
    ['publish', 'v0.1.1', '--dry-run'],
])
def test_dry_run_never_authenticates_runs_commands_or_contacts_network(distribution, monkeypatch, capsys, command):
    def forbidden(*args, **kwargs):
        pytest.fail('dry run must not execute commands or contact services')
    monkeypatch.setattr(distribution, 'run', forbidden)
    monkeypatch.setattr(distribution, 'capture', forbidden)
    monkeypatch.setattr(distribution, 'signing_dir', forbidden)
    distribution.main(command)
    assert 'Dry run: no' in capsys.readouterr().out


@pytest.mark.parametrize('flags', [[], ['--dry-run']])
def test_install_uses_the_standalone_script(distribution, monkeypatch, flags):
    calls = []
    monkeypatch.setattr(distribution, 'run', lambda args: calls.append(args))
    distribution.main(['install', *flags])
    assert calls == [['sh', distribution.ROOT / 'scripts/install-apt', *flags]]


def test_bump_updates_all_version_files_and_preserves_history(distribution, monkeypatch):
    monkeypatch.setattr(distribution, 'capture', lambda args: 'Ruslan' if args[-1] == 'user.name' else 'owner@example.org')
    distribution.main(['version', '0.1.1'])
    root = distribution.ROOT
    assert 'version = "0.1.1"\nother = "keep"' in (root / 'pyproject.toml').read_text()
    assert '__version__ = "0.1.1"' in (root / 'board_tune/__init__.py').read_text()
    assert 'board-tune 0.1.1' in (root / 'debian/board-tune.1').read_text()
    changelog = (root / 'debian/changelog').read_text()
    assert changelog.startswith('board-tune (0.1.1-1)')
    assert 'Ruslan <owner@example.org>' in changelog and changelog.endswith('old entry\n')


@pytest.mark.parametrize('version', ['0.1.0', '0.0.9', 'bad', '1.0\nextra'])
def test_invalid_version_changes_nothing(distribution, version):
    before = {p: p.read_bytes() for p in distribution.ROOT.rglob('*') if p.is_file()}
    with pytest.raises(SystemExit):
        distribution.main(['version', version])
    assert all(p.read_bytes() == text for p, text in before.items())


def test_unexpected_version_field_changes_nothing(distribution):
    (distribution.ROOT / 'debian/board-tune.1').write_text('no version field here')
    before = (distribution.ROOT / 'pyproject.toml').read_bytes()
    with pytest.raises(SystemExit, match='no files changed'):
        distribution.main(['version', '0.2.0'])
    assert (distribution.ROOT / 'pyproject.toml').read_bytes() == before


def test_repeated_local_builds_keep_previous_artifacts(distribution, monkeypatch):
    monkeypatch.setattr(distribution, 'image', lambda: None)
    monkeypatch.setattr(distribution, 'capture', lambda args: 'amd64')

    def logged(args, name):
        if name == 'build-deb':
            output = Path(next(arg[:-5] for arg in args if isinstance(arg, str) and arg.endswith(':/out')))
            (output / 'board-tune.deb').write_text(output.name)
            (output / 'board-tune.dsc').touch()

    monkeypatch.setattr(distribution, 'logged', logged)
    distribution.main(['build'])
    first = (distribution.ROOT / 'dist/latest-build').resolve()
    distribution.main(['build'])
    second = (distribution.ROOT / 'dist/latest-build').resolve()
    assert first != second
    assert (first / 'board-tune.deb').read_text() == first.name
    assert (second / 'board-tune.deb').read_text() == second.name


def test_failed_build_does_not_replace_latest(distribution, monkeypatch):
    base = distribution.ROOT / 'dist'
    base.mkdir()
    old = base / 'original'
    old.mkdir()
    (base / 'latest-build').symlink_to('original')
    monkeypatch.setattr(distribution, 'image', lambda: None)
    monkeypatch.setattr(distribution, 'capture', lambda args: 'amd64')
    def fail(*args):
        raise SystemExit('build failed')
    monkeypatch.setattr(distribution, 'logged', fail)
    with pytest.raises(SystemExit):
        distribution.main(['build'])
    assert (base / 'latest-build').resolve() == old


def test_publish_refuses_dirty_tree_before_github(distribution, monkeypatch):
    monkeypatch.setattr(distribution, 'capture', lambda args: ' M edited')
    monkeypatch.setattr(distribution, 'github', lambda: pytest.fail('must not contact GitHub'))
    with pytest.raises(SystemExit, match='Commit or discard'):
        distribution.main(['publish', 'v0.1.0'])


def test_publish_refuses_moving_an_existing_tag(distribution, monkeypatch):
    def capture(args):
        if 'status' in args:
            return ''
        return 'old-commit' if args[-1] == 'v0.1.0^{}' else 'current-commit'
    monkeypatch.setattr(distribution, 'capture', capture)
    monkeypatch.setattr(distribution.subprocess, 'run', lambda *a, **kw: subprocess.CompletedProcess(a, 0))
    monkeypatch.setattr(distribution, 'github', lambda: pytest.fail('must not contact GitHub'))
    with pytest.raises(SystemExit, match='another commit'):
        distribution.main(['publish', 'v0.1.0'])


def test_publish_sends_signing_secrets_only_through_stdin(distribution, monkeypatch):
    key = distribution.ROOT / 'private-key'
    key.mkdir()
    (key / 'archive-private.asc').write_bytes(b'private-key-fixture')
    (key / 'passphrase').write_bytes(b'passphrase-fixture')
    monkeypatch.setattr(distribution, 'signing_dir', lambda: key)
    monkeypatch.setattr(distribution, 'github', lambda: 'gh')
    lists = []
    def capture(args):
        if args[0] == 'git':
            return ''
        if args[1:3] == ['api', 'user']:
            return 'trofymenko-r'
        if args[1:3] == ['run', 'list']:
            lists.append(None)
            return '[]' if len(lists) == 1 else '[{"databaseId": 123}]'
        pytest.fail(repr(args))
    monkeypatch.setattr(distribution, 'capture', capture)
    monkeypatch.setattr(distribution.subprocess, 'run', lambda *a, **kw:
                        SimpleNamespace(returncode=1, stderr='HTTP 404', stdout=''))
    calls = []
    monkeypatch.setattr(distribution, 'run', lambda args, **kw: calls.append((args, kw)))
    distribution.main(['publish', 'v0.1.0'])
    secrets = [(args, kwargs['input']) for args, kwargs in calls if args[:3] == ['gh', 'secret', 'set']]
    assert [value for args, value in secrets] == [b'private-key-fixture', b'passphrase-fixture']
    assert all('private-key-fixture' not in repr(args) and 'passphrase-fixture' not in repr(args) for args, kw in calls)
    assert not any('--force' in args for args, kw in calls)
