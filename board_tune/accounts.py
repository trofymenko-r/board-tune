"""User accounts of the target."""

from __future__ import annotations

from dataclasses import dataclass

from board_tune.plan import Plan


@dataclass(frozen=True)
class Account:
    name: str
    uid: int
    gid: int
    home: str
    shell: str


def lookup(plan: Plan, name: str) -> Account | None:
    """Find a user in the target's /etc/passwd."""
    for line in (plan.read_text("/etc/passwd") or "").splitlines():
        fields = line.split(":")
        if len(fields) >= 7 and fields[0] == name:
            return Account(name, int(fields[2]), int(fields[3]), fields[5], fields[6])
    return None
