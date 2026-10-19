"""Live targets reached over SSH."""

from __future__ import annotations

import shlex
import shutil
import subprocess
import tempfile

from board_tune.errors import Error

from .shell import _RUN, ShellTarget


class SSHTarget(ShellTarget):
    """A running board reached with the system ssh client.

    Using the ssh binary keeps ~/.ssh/config, agents and jump hosts
    working. A shared master connection makes the many small commands
    cheap and asks for a password at most once. Unless the remote user
    is root, commands run through passwordless sudo.
    """

    live = True

    def __init__(self, destination: str):
        self.description = f"ssh {destination}"
        self._dest = destination
        self._tmp = tempfile.mkdtemp(prefix="board-tune-ssh-")
        self._ssh = ["ssh", "-o", f"ControlPath={self._tmp}/master",
                     "-o", "ConnectTimeout=5", "-o", "ServerAliveInterval=5",
                     "-o", "ServerAliveCountMax=2"]
        self._sudo = False
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
        if self._remote(["sudo", "-n", "true"]).returncode != 0:
            raise Error(f"{self.description}: passwordless sudo is required "
                        "on the target (or connect as root)")
        self._sudo = True

    def _remote(self, argv: list[str],
                input: bytes = b"") -> subprocess.CompletedProcess:
        proc = subprocess.run(
            self._ssh + ["-o", "ControlMaster=no", self._dest, shlex.join(argv)],
            input=input, capture_output=True)
        if proc.returncode == 255:
            msg = proc.stderr.decode(errors="replace").strip()
            raise Error(f"{self.description}: connection failed: {msg}")
        return proc

    def _sh(self, script, *args, input=b""):
        argv = ["sh", "-c", script, "sh", *args]
        if self._sudo:
            argv = ["sudo", "-n", "--"] + argv
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
        subprocess.run(self._ssh + ["-O", "exit", self._dest],
                       capture_output=True)
        shutil.rmtree(self._tmp, ignore_errors=True)
