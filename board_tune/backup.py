"""Private file snapshots for recovery; never execute anything from a backup."""

from __future__ import annotations

import base64
import json
import posixpath
import re
import uuid
from datetime import datetime, timezone

from board_tune.errors import Error

DIRECTORY = "/var/backups/board-tune"
_ID = re.compile(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}")


def _path(path):
    if (not isinstance(path, str) or not path.startswith("/") or path == "/"
            or posixpath.normpath(path) != path or "\0" in path
            or path == DIRECTORY or path.startswith(DIRECTORY + "/")):
        raise Error("invalid or reserved backup path")
    return path


class Backup:
    def __init__(self, target, identifier, entries):
        self.target, self.id, self.entries = target, identifier, entries

    @classmethod
    def create(cls, target, paths, follow_links=()):
        entries = {}

        def capture(path):
            path = _path(path)
            if path in entries:
                return
            info = target.stat(path)
            if info is None:
                entries[path] = {"kind": "absent"}
                return
            entry = {"kind": info.kind, "mode": info.mode, "uid": info.uid, "gid": info.gid}
            entries[path] = entry
            if info.kind == "file":
                entry["data"] = base64.b64encode(target.read(path)).decode("ascii")
            elif info.kind == "link":
                entry["dest"] = target.readlink(path)
                # In-place writes follow a final link. Preserve its destination
                # as well, so firmware/config and other aliases are recoverable.
                if path in follow_links:
                    capture(target.realpath(path))
            elif info.kind != "dir":
                raise Error(f"{path}: cannot back up a special file")

        for path in paths:
            capture(path)
        identifier = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:12]
        backup = cls(target, identifier, entries)
        target.mkdir(DIRECTORY, 0o700, (0, 0))
        data = json.dumps({"version": 1, "entries": entries}, indent=2).encode() + b"\n"
        target.write(f"{DIRECTORY}/{identifier}.json", data, 0o600, (0, 0))
        return backup

    @classmethod
    def load(cls, target, identifier):
        if not _ID.fullmatch(identifier):
            raise Error("invalid backup ID")
        raw = target.read(f"{DIRECTORY}/{identifier}.json")
        if raw is None:
            raise Error(f"backup {identifier} not found")
        try:
            data = json.loads(raw)
            if data["version"] != 1 or not isinstance(data["entries"], dict):
                raise ValueError
            for path, entry in data["entries"].items():
                _path(path)
                if entry["kind"] not in ("absent", "file", "link", "dir"):
                    raise ValueError
                if entry["kind"] != "absent":
                    if not all(type(entry[key]) is int and entry[key] >= 0 for key in ("mode", "uid", "gid")):
                        raise ValueError
                if entry["kind"] == "file":
                    base64.b64decode(entry["data"], validate=True)
                if entry["kind"] == "link" and not isinstance(entry["dest"], str):
                    raise ValueError
        except (ValueError, KeyError, TypeError):
            raise Error(f"backup {identifier} is invalid") from None
        return cls(target, identifier, data["entries"])

    def restore(self):
        failures = []
        for path, entry in reversed(list(self.entries.items())):
            try:
                current = self.target.stat(path)
                kind = entry["kind"]
                if current and (kind == "absent" or current.kind != kind):
                    (self.target.rmdir if current.kind == "dir" else self.target.remove)(path)
                if kind == "absent":
                    continue
                if kind == "file":
                    self.target.write(path, base64.b64decode(entry["data"]))
                elif kind == "link":
                    self.target.symlink(path, entry["dest"])
                else:
                    self.target.mkdir(path, entry["mode"], (entry["uid"], entry["gid"]))
                restored = self.target.stat(path)
                if (restored.mode != entry["mode"] or
                        self.target.chown and (restored.uid, restored.gid) != (entry["uid"], entry["gid"])):
                    self.target.set_attrs(path, entry["mode"], (entry["uid"], entry["gid"]))
            except (Error, OSError) as error:
                failures.append(f"{path}: {error}")
        if failures:
            raise Error("some backup entries could not be restored:\n  " + "\n  ".join(failures))
