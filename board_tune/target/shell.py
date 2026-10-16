"""Targets manipulated with small POSIX shell scripts."""

from __future__ import annotations

import stat
import subprocess

from board_tune.errors import Error

from .base import Stat, Target

# Each script gets its arguments as $1, $2, ... so paths never need
# quoting. Exit status 3 means "does not exist".
_READ = """
if [ -f "$1" ]; then exec cat -- "$1"; fi
if [ -e "$1" ]; then echo "not a regular file" >&2; exit 2; fi
exit 3
"""
_LISTDIR = r"""
[ -e "$1" ] || exit 3
[ -d "$1" ] || { echo "not a directory" >&2; exit 2; }
[ -r "$1" ] && [ -x "$1" ] || { echo "directory is not readable" >&2; exit 2; }
for p in "$1"/* "$1"/.[!.]* "$1"/..?*; do
    [ -e "$p" ] || [ -L "$p" ] || continue
    printf '%s\0' "${p##*/}"
done
"""
_STAT = """
[ -e "$1" ] || [ -L "$1" ] || exit 3
exec stat -c '%a %u %g %f' -- "$1"
"""
_EXISTS = '[ -e "$1" ]'
_READLINK = '[ -L "$1" ] || exit 3; exec readlink -- "$1"'
_WRITE_INPLACE = """
set -e
mkdir -p -- "$(dirname -- "$1")"
cat > "$1"
"""
_WRITE_ATOMIC = """
set -e
mkdir -p -- "$(dirname -- "$1")"
t="$1.board-tune.$$"
trap 'rm -f -- "$t"' EXIT
cat > "$t"
chmod "$2" "$t"
[ -z "$3" ] || chown "$3" "$t"
mv -f -- "$t" "$1"
trap - EXIT
"""
_MKDIR = """
set -e
mkdir -p -- "$1"
chmod "$2" "$1"
[ -z "$3" ] || chown "$3" "$1"
"""
_SYMLINK = """
set -e
mkdir -p -- "$(dirname -- "$1")"
ln -sfn -- "$2" "$1"
"""
_REMOVE = 'rm -f -- "$1"'
_RUN = 'exec "$@"'


class ShellTarget(Target):
    """Target whose files are manipulated with POSIX shell commands.

    Every operation runs one short `sh -c` script, so the same code
    serves local mounts (optionally through sudo) and remote systems
    over SSH, and the board needs nothing beyond a POSIX shell and
    coreutils or busybox.
    """

    def _sh(self, script: str, *args: str,
            input: bytes = b"") -> subprocess.CompletedProcess:
        """Run script with args and return the completed process."""
        raise NotImplementedError

    def _path(self, path: str, follow: bool = True) -> str:
        """Map a target path to the path the shell operates on."""
        return path

    def _check(self, proc: subprocess.CompletedProcess,
               what: str) -> subprocess.CompletedProcess:
        if proc.returncode != 0:
            msg = proc.stderr.decode(errors="replace").strip()
            raise Error(f"{self.description}: {what}: "
                        f"{msg or f'exit status {proc.returncode}'}")
        return proc

    def _owner(self, owner: tuple[int, int] | None) -> str:
        if owner is None or not self.chown:
            return ""
        return f"{owner[0]}:{owner[1]}"

    def read(self, path):
        proc = self._sh(_READ, self._path(path))
        if proc.returncode == 3:
            return None
        return self._check(proc, f"cannot read {path}").stdout

    def listdir(self, path):
        proc = self._sh(_LISTDIR, self._path(path))
        if proc.returncode == 3:
            return []
        data = self._check(proc, f"cannot list {path}").stdout
        return sorted(name.decode() for name in data.split(b"\0") if name)

    def stat(self, path):
        proc = self._sh(_STAT, self._path(path, follow=False))
        if proc.returncode == 3:
            return None
        mode, uid, gid, raw = self._check(proc, f"cannot stat {path}").stdout.split()
        bits = int(raw, 16)
        kind = ("dir" if stat.S_ISDIR(bits) else "link" if stat.S_ISLNK(bits)
                else "file" if stat.S_ISREG(bits) else "special")
        return Stat(int(mode, 8), int(uid), int(gid), kind)

    def exists(self, path):
        return self._sh(_EXISTS, self._path(path)).returncode == 0

    def readlink(self, path):
        proc = self._sh(_READLINK, self._path(path, follow=False))
        if proc.returncode == 3:
            return None
        out = self._check(proc, f"cannot read link {path}").stdout
        return out.decode().rstrip("\n")

    def write(self, path, data, mode=None, owner=None):
        if mode is None:
            proc = self._sh(_WRITE_INPLACE, self._path(path), input=data)
        else:
            proc = self._sh(_WRITE_ATOMIC, self._path(path, follow=False),
                            f"{mode:o}", self._owner(owner), input=data)
        self._check(proc, f"cannot write {path}")

    def mkdir(self, path, mode=0o755, owner=None):
        proc = self._sh(_MKDIR, self._path(path), f"{mode:o}",
                        self._owner(owner))
        self._check(proc, f"cannot create directory {path}")

    def symlink(self, path, dest):
        proc = self._sh(_SYMLINK, self._path(path, follow=False), dest)
        self._check(proc, f"cannot create symlink {path}")

    def remove(self, path):
        proc = self._sh(_REMOVE, self._path(path, follow=False))
        self._check(proc, f"cannot remove {path}")

    def realpath(self, path):
        proc = self._sh('exec readlink -m -- "$1"', self._path(path))
        return self._check(proc, f"cannot resolve {path}").stdout.decode().rstrip("\n")

    def set_attrs(self, path, mode, owner):
        script = 'set -e; [ -L "$1" ] || chmod "$2" "$1"; [ -z "$3" ] || chown -h "$3" "$1"'
        proc = self._sh(script, self._path(path, follow=False), f"{mode:o}", self._owner(owner))
        self._check(proc, f"cannot restore attributes of {path}")

    def rmdir(self, path):
        proc = self._sh('rmdir -- "$1"', self._path(path, follow=False))
        self._check(proc, f"cannot remove directory {path}")
