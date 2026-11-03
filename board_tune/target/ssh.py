"""Live targets reached over SSH."""

from __future__ import annotations

import getpass
import os
import secrets
import selectors
import shlex
import shutil
import subprocess
import sys
import tempfile
import time

from board_tune.errors import Error

from .shell import _RUN, ShellTarget


class SSHTarget(ShellTarget):
    """A running board reached with the system ssh client.

    Using the ssh binary keeps ~/.ssh/config, agents and jump hosts
    working. A shared master connection makes the many small commands
    cheap. Non-root users authenticate sudo once before entering the menu;
    credentials remain in memory until the connection is closed.
    """

    live = True

    @property
    def sudo_uses_password(self):
        return self._sudo_password is not None

    def __init__(self, destination: str):
        self.description = f"ssh {destination}"
        self._dest = destination
        self._tmp = tempfile.mkdtemp(prefix="board-tune-ssh-")
        self._ssh = ["ssh", "-o", f"ControlPath={self._tmp}/master",
                     "-o", "ConnectTimeout=5", "-o", "ServerAliveInterval=5",
                     "-o", "ServerAliveCountMax=2"]
        self._sudo = False
        self._sudo_password = None
        try:
            self._connect()
        except BaseException:
            self.close()
            raise

    def _connect(self) -> None:
        # The master goes to the background once authenticated. It must
        # not inherit our pipes, or reading them would never finish.
        master = subprocess.run(self._ssh + ["-o", "ControlMaster=yes",
                                             "-f", "-N", self._dest])
        if master.returncode != 0:
            raise Error(f"cannot connect to {self._dest}")
        identity = self._remote(["id", "-u"])
        try:
            self.login_uid = int(identity.stdout.strip())
        except ValueError:
            raise Error(f"{self.description}: cannot determine SSH login UID") from None
        if self.login_uid == 0:
            return
        access = self._sudo_remote(["id", "-u"], authenticate=True)
        self._check(access, "sudo root access failed")
        if access.stdout.strip() != b"0":
            raise Error(f"{self.description}: sudo must allow commands as root")
        self._sudo = True

    def _argv(self, argv):
        return self._ssh + ["-o", "ControlMaster=no", self._dest, shlex.join(argv)]

    def _result(self, proc):
        if self._sudo_password:
            proc.stderr = proc.stderr.replace(bytes(self._sudo_password), b"[redacted]")
        if proc.returncode == 255:
            msg = proc.stderr.decode(errors="replace").strip()
            raise Error(f"{self.description}: connection failed: {msg}")
        return proc

    def _remote(self, argv: list[str],
                input: bytes = b"") -> subprocess.CompletedProcess:
        proc = subprocess.run(
            self._argv(argv),
            input=input, capture_output=True)
        return self._result(proc)

    def _sudo_remote(self, argv, input=b"", authenticate=False):
        """Send credentials only on a sudo challenge, then send command input.

        Sudo may use NOPASSWD or cached credentials and leave stdin untouched.
        Sending a password alongside file contents would then corrupt the file
        and could expose the password to sudo's command-input audit log.
        A privileged shell acknowledges startup before receiving any payload.
        """
        token = secrets.token_hex(16)
        prompt = f"board-tune-sudo-{token}:".encode()
        ready = f"board-tune-ready-{token}\n".encode()
        wrapper = 'printf "%s\\n" "$1"; shift; exec "$@"'
        command = self._argv(["sudo", "-S", "-p", prompt.decode(), "--", "sh", "-c",
                              wrapper, "board-tune-sudo", ready.decode().rstrip("\n"), *argv])
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE)
        out, err = bytearray(), bytearray()
        sent_password = False
        deadline = time.monotonic() + 30
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ, out)
                selector.register(process.stderr, selectors.EVENT_READ, err)
                while ready not in out and selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise Error(f"{self.description}: sudo authentication timed out")
                    for key, _ in selector.select(remaining):
                        data = os.read(key.fd, 4096)
                        if not data:
                            selector.unregister(key.fileobj)
                            continue
                        key.data.extend(data)
                    if prompt in err:
                        err[:] = err.replace(prompt, b"")
                        if sent_password:
                            raise Error(f"{self.description}: sudo authentication failed")
                        if self._sudo_password is None:
                            if not authenticate or not sys.stdin.isatty():
                                raise Error(f"{self.description}: sudo needs a password; open board-tune "
                                            "in a terminal to authenticate (unattended use requires "
                                            "passwordless sudo or root)")
                            try:
                                password = getpass.getpass(f"[sudo {self._dest}] Password: ")
                            except (EOFError, KeyboardInterrupt):
                                raise Error(f"{self.description}: sudo authentication cancelled") from None
                            self._sudo_password = bytearray(password.encode())
                            del password
                        process.stdin.write(self._sudo_password + b"\n")
                        process.stdin.flush()
                        sent_password = True
                        deadline = time.monotonic() + 30
            if ready in out:
                # A read can include both the marker and initial command output.
                out[:] = out.replace(ready, b"", 1)
                tail_out, tail_err = process.communicate(input=input)
            else:
                # Sudo failed before starting a privileged shell. Never send data.
                tail_out, tail_err = process.communicate()
            return self._result(subprocess.CompletedProcess(
                command, process.returncode, bytes(out) + tail_out, bytes(err) + tail_err))
        except BrokenPipeError:
            process.kill()
            process.communicate()
            raise Error(f"{self.description}: connection closed during sudo authentication") from None
        except BaseException:
            process.kill()
            process.communicate()
            raise

    def _sh(self, script, *args, input=b""):
        argv = ["sh", "-c", script, "sh", *args]
        if self._sudo:
            return self._sudo_remote(argv, input)
        return self._remote(argv, input)

    def run(self, argv, input=None):
        proc = self._sh(_RUN, *argv, input=input or b"")
        return self._check(proc, f"{shlex.join(argv)} failed").stdout.decode()

    def access_interface(self):
        """Find the board interface carrying this SSH session, when available."""
        import ipaddress
        import re

        connection = self._remote(["sh", "-c", 'printf "%s" "$SSH_CONNECTION"']).stdout.decode().split()
        if len(connection) != 4:
            return None
        try:
            client = str(ipaddress.ip_address(connection[0]))
            server = str(ipaddress.ip_address(connection[2]))
        except ValueError:
            return None
        route = self.run(["ip", "route", "get", client, "from", server])
        match = re.search(r"\bdev (\S+)", route)
        return match[1] if match else None

    def close(self):
        if self._sudo_password is not None:
            self._sudo_password[:] = b"\0" * len(self._sudo_password)
            self._sudo_password = None
        try:
            subprocess.run(self._ssh + ["-O", "exit", self._dest], capture_output=True)
        finally:
            shutil.rmtree(self._tmp, ignore_errors=True)
