"""Privilege negotiation must not mix sudo credentials with command stdin."""

import hashlib
import shlex
import subprocess
import sys
from types import SimpleNamespace

import pytest

from board_tune.errors import Error
from board_tune.target import ssh

PASSWORD = 'sudo-test-only-$ ; private'


@pytest.fixture
def remote(tmp_path, monkeypatch):
    target = ssh.SSHTarget.__new__(ssh.SSHTarget)
    target.description = 'ssh pi@test-board'
    target._dest = 'pi@test-board'
    target._ssh = ['ssh']
    target._tmp = str(tmp_path / 'master')
    (tmp_path / 'master').mkdir()
    target._sudo = True
    target._sudo_password = None
    target.login_uid = 1000
    prompts, commands, processes = [], [], []
    state = SimpleNamespace(mode='challenge', terminal=True)
    helper = tmp_path / 'sudo.py'
    helper.write_text('''
import hashlib, os, sys, time
prompt = sys.argv[1].encode()
mode = os.environ['BT_TEST_MODE']
if mode == 'challenge':
    os.write(2, b'site notice\\n' + prompt[:8])
    time.sleep(0.01)
    os.write(2, prompt[8:])
    password = sys.stdin.buffer.readline().removesuffix(b'\\n')
    if hashlib.sha256(password).hexdigest() != os.environ['BT_TEST_PASSWORD_HASH']:
        os.write(2, b'Sorry, try again.\\n' + prompt)
        sys.stdin.buffer.read()
        sys.exit(1)
elif mode == 'denied':
    os.write(2, b'user is not allowed to run sudo\\n')
    sys.exit(1)
elif mode == 'hang':
    sys.stdin.buffer.read()
    sys.exit(1)
root = sys.argv[2:]
ready = root[4].encode() + b'\\n'
os.write(1, ready[:9])
time.sleep(0.01)
os.write(1, ready[9:])
os.execvp(root[5], root[5:])
''')
    real_popen = subprocess.Popen

    def popen(argv, **kwargs):
        import os
        commands.append(argv)
        args = shlex.split(argv[-1])
        assert PASSWORD not in shlex.join(argv)
        prompt = args[args.index('-p') + 1]
        root = args[args.index('--') + 1:]
        proc = real_popen([sys.executable, str(helper), prompt, *root],
                          env={**os.environ, 'BT_TEST_MODE': state.mode,
                               'BT_TEST_PASSWORD_HASH': hashlib.sha256(PASSWORD.encode()).hexdigest()},
                          **kwargs)
        processes.append(proc)
        return proc

    def password(prompt):
        prompts.append(prompt)
        return PASSWORD

    monkeypatch.setattr(ssh, 'sys', SimpleNamespace(stdin=SimpleNamespace(isatty=lambda: state.terminal)))
    monkeypatch.setattr(ssh.getpass, 'getpass', password)
    monkeypatch.setattr(ssh, 'subprocess', SimpleNamespace(
        Popen=popen, PIPE=subprocess.PIPE, CompletedProcess=subprocess.CompletedProcess,
        run=lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, b'', b'')))
    yield target, state, prompts, commands, processes
    target.close()
    assert all(proc.poll() is not None for proc in processes)


def test_password_is_requested_once_and_binary_stdin_survives(remote):
    target, state, prompts, commands, _ = remote
    assert target._sudo_remote(['true'], authenticate=True).returncode == 0
    payload = b'\x00\xff\nprivate file data\r\n' * 20000
    proc = target._sh('cat', input=payload)
    assert proc.returncode == 0
    assert proc.stdout == payload
    assert proc.stderr == b'site notice\n'
    assert len(prompts) == 1
    assert PASSWORD not in repr(commands)


@pytest.mark.parametrize('cached_password', [False, True])
def test_nopasswd_or_cached_sudo_never_receives_password_as_command_input(remote, cached_password):
    target, state, prompts, _, _ = remote
    state.mode = 'cached'
    if cached_password:
        target._sudo_password = bytearray(PASSWORD.encode())
    payload = b'first line\n\x00\xfflast line'
    proc = target._sudo_remote(['cat'], input=payload, authenticate=True)
    assert proc.returncode == 0 and proc.stdout == payload
    assert prompts == []


def test_bad_password_stops_before_sending_file_contents(remote):
    target, _, prompts, _, _ = remote
    target._sudo_password = bytearray(b'wrong test password')
    with pytest.raises(Error, match='sudo authentication failed'):
        target._sudo_remote(['cat'], input=b'never send this payload', authenticate=True)
    assert prompts == []


def test_missing_terminal_fails_without_reading_stdin(remote):
    target, state, prompts, _, _ = remote
    state.terminal = False
    with pytest.raises(Error, match='open board-tune in a terminal'):
        target._sudo_remote(['cat'], input=b'never consume input as a password', authenticate=True)
    assert prompts == []


@pytest.mark.parametrize('exception', [EOFError, KeyboardInterrupt])
def test_cancelled_prompt_cleans_up_child(remote, monkeypatch, exception):
    target = remote[0]

    def cancel(prompt):
        raise exception

    monkeypatch.setattr(ssh.getpass, 'getpass', cancel)
    with pytest.raises(Error, match='authentication cancelled'):
        target._sudo_remote(['true'], authenticate=True)


def test_sudo_denial_preserves_diagnostic_without_sending_payload(remote):
    target, state, prompts, _, _ = remote
    state.mode = 'denied'
    proc = target._sudo_remote(['cat'], input=b'not a password', authenticate=True)
    assert proc.returncode == 1
    assert b'not allowed' in proc.stderr
    assert proc.stdout == b'' and not prompts


def test_handshake_timeout_terminates_child(remote, monkeypatch):
    target, state, _, _, _ = remote
    state.mode = 'hang'
    times = iter([0, 31])
    monkeypatch.setattr(ssh.time, 'monotonic', lambda: next(times))
    with pytest.raises(Error, match='authentication timed out'):
        target._sudo_remote(['true'], authenticate=True)


def test_sudo_does_not_prompt_inside_menu_if_policy_changes(remote):
    target = remote[0]
    with pytest.raises(Error, match='sudo needs a password'):
        target._sudo_remote(['true'])
    assert not remote[2]


def test_root_login_does_not_use_sudo(remote, monkeypatch):
    target = remote[0]
    target._sudo = False
    monkeypatch.setattr(target, '_remote', lambda argv: subprocess.CompletedProcess(argv, 0, b'0\n', b''))
    monkeypatch.setattr(target, '_sudo_remote', lambda *a, **kw: pytest.fail('root must not use sudo'))
    target._connect()
    assert target.login_uid == 0 and not target._sudo


def test_non_root_connect_checks_effective_root_identity(remote, monkeypatch):
    target = remote[0]
    target._sudo = False
    monkeypatch.setattr(target, '_remote', lambda argv: subprocess.CompletedProcess(argv, 0, b'1000\n', b''))
    monkeypatch.setattr(target, '_sudo_remote', lambda argv, authenticate: subprocess.CompletedProcess(argv, 0, b'0\n', b''))
    target._connect()
    assert target.login_uid == 1000 and target._sudo


def test_close_discards_password_and_connection_files(remote):
    from pathlib import Path
    target = remote[0]
    secret = target._sudo_password = bytearray(PASSWORD.encode())
    target.close()
    assert target._sudo_password is None and not any(secret)
    assert not Path(target._tmp).exists()


def test_connection_errors_redact_cached_password(remote):
    target = remote[0]
    target._sudo_password = bytearray(PASSWORD.encode())
    with pytest.raises(Error) as error:
        target._result(subprocess.CompletedProcess([], 255, b'', PASSWORD.encode()))
    assert PASSWORD not in str(error.value)
    assert '[redacted]' in str(error.value)
