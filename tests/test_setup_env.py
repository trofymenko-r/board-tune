"""Source the developer environment in real Bash sessions without network use."""

import os
from pathlib import Path
import shutil
import subprocess
import venv

import pytest


SETUP = Path(__file__).resolve().parents[1] / 'setup.env'


@pytest.fixture
def checkout(tmp_path):
    root = tmp_path / 'checkout with spaces'
    root.mkdir()
    shutil.copy2(SETUP, root / 'setup.env')
    # Real virtualenv activation and isolated imports, with local fixture modules.
    venv.EnvBuilder(with_pip=False).create(root / '.venv')
    package = root / 'board_tune'
    package.mkdir()
    (package / '__init__.py').touch()
    (root / 'kconfiglib.py').touch()
    site = next((root / '.venv/lib').glob('python*/site-packages'))
    (site / 'checkout.pth').write_text(str(root) + '\n')
    entry = root / '.venv/bin/board-tune'
    entry.write_text('#!/bin/sh\nexit 0\n')
    entry.chmod(0o755)
    scripts = root / 'scripts'
    scripts.mkdir()
    shutil.copy2(entry, scripts / 'build-deb')
    return root


def bash(root, command):
    return subprocess.run(['bash', '--noprofile', '--norc', '-c', command,
                           'setup-test', str(root)], cwd=root.parent,
                          env=dict(os.environ, VIRTUAL_ENV='', PYTHONPATH=''),
                          capture_output=True, text=True)


def test_repeated_source_works_outside_checkout_without_path_duplicates(checkout):
    result = bash(checkout, '''
set -eu
before_pwd=$PWD
before_flags=$-
. "$1/setup.env"
. "$1/setup.env"
[[ $PWD == "$before_pwd" && $- == "$before_flags" ]]
[[ $VIRTUAL_ENV == "$1/.venv" ]]
[[ $(command -v board-tune) == "$1/.venv/bin/board-tune" ]]
[[ $(command -v build-deb) == "$1/scripts/build-deb" ]]
python -I -c 'import os, sys; paths=os.environ["PATH"].split(":"); assert paths.count(sys.argv[1]+"/scripts")==1; assert paths.count(sys.argv[1]+"/.venv/bin")==1' "$1"
! declare -F _board_tune_setup
''')
    assert result.returncode == 0, result.stderr
    assert not result.stdout  # no pip installation on repeated setup


def test_install_failure_leaves_callers_environment_unchanged(checkout):
    (checkout / '.venv/bin/board-tune').unlink()
    result = bash(checkout, '''
set -u
before_path=$PATH
before_flags=$-
if . "$1/setup.env"; then exit 20; fi
[[ $PATH == "$before_path" && $- == "$before_flags" && -z $VIRTUAL_ENV ]]
! declare -F _board_tune_setup
''')
    assert result.returncode == 0, result.stderr
    assert 'No module named pip' in result.stderr


def test_creation_failure_returns_before_activation(tmp_path):
    shutil.copy2(SETUP, tmp_path / 'setup.env')
    result = bash(tmp_path, '''
before_path=$PATH
python3() { return 17; }
if . "$1/setup.env"; then exit 20; fi
[[ $PATH == "$before_path" && -z $VIRTUAL_ENV ]]
! declare -F _board_tune_setup
''')
    assert result.returncode == 0, result.stderr
    assert 'Cannot create .venv' in result.stderr
    assert not (tmp_path / '.venv').exists()


@pytest.mark.parametrize('shell', ['bash', 'sh'])
def test_direct_execution_is_rejected_without_creating_environment(tmp_path, shell):
    script = tmp_path / 'setup.env'
    shutil.copy2(SETUP, script)
    result = subprocess.run([shell, str(script)], capture_output=True, text=True)
    assert result.returncode == 1
    assert '. ./setup.env' in result.stderr
    assert not (tmp_path / '.venv').exists()
