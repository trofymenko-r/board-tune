"""The downloaded installer works alone; preview never invokes host commands."""

import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('flag', ['--dry-run', '--help', '-h', '--unknown'])
def test_standalone_preview_and_usage_need_no_commands_or_checkout(tmp_path, flag):
    script = tmp_path / 'install-apt'
    shutil.copy2(ROOT / 'scripts/install-apt', script)
    result = subprocess.run(['/bin/sh', str(script), flag], cwd=tmp_path,
                            env=dict(os.environ, PATH='/no-external-commands'),
                            capture_output=True, text=True)
    assert result.returncode == (2 if flag == '--unknown' else 0), result.stderr
    assert ('Dry run: no' if flag == '--dry-run' else 'Usage:') in result.stdout + result.stderr
    assert list(tmp_path.iterdir()) == [script]


def test_embedded_key_matches_the_archive_key():
    script = (ROOT / 'scripts/install-apt').read_text()
    key = script.split("<<'BOARD_TUNE_KEY'\n", 1)[1].split('\nBOARD_TUNE_KEY\n', 1)[0] + '\n'
    assert key == (ROOT / 'packaging/board-tune-archive-key.asc').read_text()


def test_unsupported_architecture_stops_before_filesystem_or_apt_changes(tmp_path):
    calls = tmp_path / 'calls'
    for name in ('apt-get', 'dpkg', 'id', 'mktemp', 'cmp', 'install'):
        command = tmp_path / name
        if name == 'id':
            body = 'echo 0'
        elif name == 'dpkg':
            body = 'echo riscv64'
        else:
            body = 'echo unexpected >>"$CALLS"; exit 99'
        command.write_text('#!/bin/sh\n' + body + '\n')
        command.chmod(0o755)
    result = subprocess.run(['/bin/sh', str(ROOT / 'scripts/install-apt')],
                            env=dict(os.environ, PATH=str(tmp_path), CALLS=str(calls)),
                            capture_output=True, text=True)
    assert result.returncode == 1 and 'Supported APT architectures' in result.stderr
    assert not calls.exists()
