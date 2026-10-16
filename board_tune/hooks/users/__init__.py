"""One local user, with native live operations and staged offline databases."""

from __future__ import annotations

import posixpath
import re
from datetime import datetime, timezone
from pathlib import Path

from board_tune import accounts
from board_tune.errors import Error
from board_tune.registry import Context, Hook

_NAME = re.compile(r"[a-z_][a-z0-9_-]{0,31}")
_HASH = re.compile(r"\$(?:6|5|y|2[aby])\$[A-Za-z0-9./$=,+-]+")
DATABASES = (("passwd", 7), ("group", 4), ("shadow", 9), ("gshadow", 4))


def _records(ctx, name, fields, required=True):
    text = ctx.plan.read_text(f"/etc/{name}")
    if text is None:
        if required:
            raise Error(f"/etc/{name} is required for local user management")
        return [], False
    rows = [line.split(":") for line in text.splitlines() if line]
    if any(len(row) != fields for row in rows) or len({row[0] for row in rows}) != len(rows):
        raise Error(f"/etc/{name}: malformed or duplicate entries")
    if name in ("passwd", "group"):
        try:
            for row in rows:
                if int(row[2]) < 0 or name == "passwd" and int(row[3]) < 0:
                    raise ValueError
        except ValueError:
            raise Error(f"/etc/{name}: invalid numeric identity") from None
    return rows, True


def _render(rows):
    return "".join(":".join(row) + "\n" for row in rows)


def _number_range(ctx, prefix):
    values = {}
    for line in (ctx.plan.read_text("/etc/login.defs") or "").splitlines():
        parts = line.split("#", 1)[0].split()
        if len(parts) == 2 and parts[1].isdigit():
            values[parts[0]] = int(parts[1])
    return values.get(prefix + "_MIN", 1000), values.get(prefix + "_MAX", 60000)


def _allocate(ctx, used, prefix, requested=0):
    low, high = _number_range(ctx, prefix)
    if requested:
        if requested in used:
            raise Error(f"{prefix} {requested} is already in use")
        return requested
    for value in range(max(1, low), high + 1):
        if value not in used:
            return value
    raise Error(f"no free {prefix} in range {low}..{high}")


def _password_hash(path):
    try:
        with Path(path).expanduser().open() as stream:
            value = stream.read(4097).strip()
    except OSError as error:
        raise Error(f"USERS_PASSWORD_HASH_FILE: {error.strerror}") from None
    if len(value) > 4096 or not _HASH.fullmatch(value):
        raise Error("USERS_PASSWORD_HASH_FILE must contain one supported encrypted password hash")
    return value


class Users(Hook):
    title = "Users"
    symbol = "USERS"
    order = 10  # Subsequent serial/SSH hooks can use the newly planned user.

    def read(self, ctx):
        if not ctx.plan.exists("/etc/group") or not ctx.plan.exists("/etc/shadow"):
            return {"USERS": "n"}
        low, high = _number_range(ctx, "UID")
        rows, _ = _records(ctx, "passwd", 7)
        people = [row for row in rows if row[2].isdigit() and low <= int(row[2]) <= high]
        if not people:
            return {"USERS": "n"}
        name = next((row[0] for row in people if row[0] == "pi"), people[0][0])
        ctx.plan.note(f"user actions start at Keep; existing local users: {', '.join(row[0] for row in people)}")
        # These are actions, not imported credentials or copied account fields:
        # changing the selected name must never clone another user's identity.
        return {"USERS": "y", "USERS_NAME": name}

    def plan(self, ctx: Context):
        cfg, plan = ctx.config, ctx.plan
        name = cfg.str("USERS_NAME")
        if not _NAME.fullmatch(name):
            raise Error("USERS_NAME: expected a local login name (letters, digits, _ or -)")
        tables = {}
        for database, fields in DATABASES:
            tables[database], exists = _records(ctx, database, fields, database != "gshadow")
            if database == "gshadow":
                has_gshadow = exists
        passwd, groups, shadow = (tables[key] for key in ("passwd", "group", "shadow"))
        account = next((row for row in passwd if row[0] == name), None)
        new = account is None
        if new and not cfg.enabled("USERS_CREATE"):
            raise Error(f"user {name} does not exist and USERS_CREATE is disabled")
        shell = cfg.str("USERS_SHELL")
        if shell and (not shell.startswith("/") or any(c in shell for c in ":\n\r\0")
                      or not plan.exists(shell)):
            raise Error("USERS_SHELL must name an installed absolute shell path")
        if shell:
            info = ctx.target.stat(ctx.target.realpath(shell))
            if info is None or info.kind != "file" or not info.mode & 0o111:
                raise Error("USERS_SHELL must be an executable regular file")
        if new:
            if name == "root":
                raise Error("refusing to create a missing root account")
            uid = _allocate(ctx, {int(row[2]) for row in passwd}, "UID", cfg.int("USERS_UID"))
            group = next((row for row in groups if row[0] == name), None)
            if group is None:
                gid = _allocate(ctx, {int(row[2]) for row in groups}, "GID")
                groups.append([name, "x", str(gid), ""])
                if has_gshadow:
                    tables["gshadow"].append([name, "!", "", ""])
                if ctx.target.live:
                    plan.command(["groupadd", "--gid", str(gid), "--", name], before_files=True)
            else:
                gid = int(group[2])
            home = cfg.str("USERS_HOME") or f"/home/{name}"
            if (not home.startswith("/") or home == "/" or posixpath.normpath(home) != home
                    or not home.startswith("/home/") or any(c in home for c in ":\n\r\0")):
                raise Error("USERS_HOME must be a normalized path below /home")
            if ctx.target.stat(home) is not None:
                raise Error(f"{home} already exists; refusing to take ownership of it")
            shell = shell or "/bin/bash"
            if not plan.exists(shell):
                raise Error(f"login shell {shell} is not installed")
            account = [name, "x", str(uid), str(gid), "", home, shell]
            passwd.append(account)
            days = str(int(datetime.now(timezone.utc).timestamp()) // 86400)
            shadow.append([name, "!", days, "0", "99999", "7", "", "", ""])
            if ctx.target.live:
                plan.command(["useradd", "--uid", str(uid), "--gid", str(gid),
                              "--home-dir", home, "--shell", shell, "--no-create-home",
                              "--no-log-init", "--key", "CREATE_MAIL_SPOOL=no",
                              "--key", "SUB_UID_COUNT=0", "--key", "SUB_GID_COUNT=0",
                              "--", name], before_files=True)
            plan.mkdir(home, 0o750, (uid, gid))
            if not ctx.target.chown:
                plan.note("unprivileged directory target: apply ownership as root before booting this filesystem")
        else:
            uid, gid, home = int(account[2]), int(account[3]), account[5]
            if cfg.int("USERS_UID") not in (0, uid):
                raise Error("existing user UIDs cannot be changed")
            if cfg.str("USERS_HOME") not in ("", home):
                raise Error("existing user home directories cannot be moved")
            if shell and shell != account[6]:
                if ctx.target.live and ctx.target.login_uid == uid:
                    raise Error("cannot change the connected SSH user's shell; connect as another administrator")
                account[6] = shell
                plan.command(["usermod", "--shell", shell, "--", name], before_files=True)
        shadow_entry = next((row for row in shadow if row[0] == name), None)
        if shadow_entry is None:
            raise Error(f"user {name} has no /etc/shadow entry")
        if cfg.enabled("USERS_PASSWORD_HASH"):
            value = _password_hash(cfg.str("USERS_PASSWORD_HASH_FILE"))
            if shadow_entry[1] != value:
                shadow_entry[1] = value
                shadow_entry[2] = str(int(datetime.now(timezone.utc).timestamp()) // 86400)
                plan.command(["chpasswd", "--encrypted"], input=f"{name}:{value}\n".encode())
        elif cfg.enabled("USERS_PASSWORD_LOCK") and not shadow_entry[1].startswith(("!", "*")):
            shadow_entry[1] = "!" + shadow_entry[1]
            plan.command(["usermod", "--lock", "--", name], before_files=True)

        if not cfg.enabled("USERS_GROUPS_KEEP"):
            wanted = set(filter(None, re.split(r"[\s,]+", cfg.str("USERS_GROUPS"))))
            known = {row[0] for row in groups}
            if any(not _NAME.fullmatch(group) for group in wanted) or not wanted <= known:
                raise Error("USERS_GROUPS contains an invalid or unknown group")
            old = {row[0] for row in groups if name in row[3].split(",")}
            desired = old | wanted if cfg.enabled("USERS_GROUPS_ADD") else wanted
            if ctx.target.live and uid != 0 and ctx.target.login_uid == uid and old - desired:
                raise Error("cannot remove groups from the connected SSH user; connect as another administrator")
            for group in groups:
                members = list(filter(None, group[3].split(",")))
                if group[0] in desired and name not in members:
                    members.append(name)
                if group[0] not in desired and name in members:
                    members.remove(name)
                group[3] = ",".join(members)
            for group in tables["gshadow"]:
                ordinary = next((row for row in groups if row[0] == group[0]), None)
                if ordinary is not None and group[3] != ordinary[3]:
                    group[3] = ordinary[3]
            if desired != old:
                plan.command(["usermod", "--groups", ",".join(sorted(desired)), "--", name], before_files=True)

        if ctx.target.live:
            # Native tools take the account-database locks. Project their
            # expected metadata only so subsequent hooks can resolve UID/home.
            plan.project("/etc/passwd", _render(passwd))
            plan.project("/etc/group", _render(groups))
            if new or any(plan.pre_commands) or cfg.enabled("USERS_PASSWORD_HASH"):
                plan.note("native account commands are not undone by file-backup restoration")
        else:
            for database, _ in DATABASES:
                if database == "gshadow" and not has_gshadow:
                    continue
                path = f"/etc/{database}"
                attrs = ctx.target.stat(path)
                plan.write(path, _render(tables[database]), mode=attrs.mode,
                           owner=(attrs.uid, attrs.gid), sensitive=database in ("shadow", "gshadow"))
        self._sudo(ctx, name)

    def _sudo(self, ctx, name):
        cfg, plan = ctx.config, ctx.plan
        path = f"/etc/sudoers.d/90-board-tune-{name}"
        if cfg.enabled("USERS_SUDO_KEEP"):
            return
        if cfg.enabled("USERS_SUDO_REMOVE"):
            account = accounts.lookup(plan, name)
            if (ctx.target.live and account.uid != 0 and ctx.target.login_uid == account.uid
                    and plan.exists(path)):
                raise Error("cannot remove the connected SSH user's sudo rule; connect as another administrator")
            plan.remove(path)
            return
        main = plan.read_text("/etc/sudoers") or ""
        if not re.search(r"(?m)^\s*(?:@|#)includedir\s+/etc/sudoers\.d\s*$", main):
            raise Error("/etc/sudoers does not include /etc/sudoers.d")
        tag = "NOPASSWD: " if cfg.enabled("USERS_SUDO_NOPASSWD") else ""
        text = f"# Generated by board-tune\n{name} ALL=(ALL:ALL) {tag}ALL\n"
        if ctx.target.live and plan.read_text(path) != text:
            binary = next((p for p in ("/usr/sbin/visudo", "/sbin/visudo") if plan.exists(p)), None)
            if binary is None:
                raise Error("visudo is required to validate sudo rules on the live target")
            plan.command([binary, "--check", "--file", "-"], input=text.encode(), before_files=True)
        plan.write(path, text, mode=0o440, owner=(0, 0))


HOOK = Users()
