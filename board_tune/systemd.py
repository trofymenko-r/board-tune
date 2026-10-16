"""Enable and disable systemd units by managing their symlinks.

This mirrors what `systemctl enable` does, but works the same on live
and offline targets without running anything on the board.
"""

from __future__ import annotations

from board_tune.errors import Error
from board_tune.plan import Plan

SYSTEM_DIR = "/etc/systemd/system"
VENDOR_DIRS = ("/usr/lib/systemd/system", "/lib/systemd/system")


def find_unit(plan: Plan, unit: str) -> str | None:
    """Path of a unit file, or None if the unit is not installed."""
    path = f"{SYSTEM_DIR}/{unit}"
    # A symlink in /etc is an alias or a mask, not the unit itself.
    if plan.readlink(path) is None and plan.exists(path):
        return path
    for d in VENDOR_DIRS:
        path = f"{d}/{unit}"
        if plan.exists(path):
            return path
    return None


def _install_links(plan: Plan, unit: str, path: str) -> list[str]:
    links = []
    section = None
    for raw in (plan.read_text(path) or "").splitlines():
        line = raw.strip()
        if not line or line[0] in "#;":
            continue
        if line.startswith("["):
            section = line
            continue
        if section != "[Install]" or "=" not in line:
            continue
        key, value = (s.strip() for s in line.split("=", 1))
        for item in value.split():
            if key == "WantedBy":
                links.append(f"{SYSTEM_DIR}/{item}.wants/{unit}")
            elif key == "RequiredBy":
                links.append(f"{SYSTEM_DIR}/{item}.requires/{unit}")
            elif key == "Alias":
                links.append(f"{SYSTEM_DIR}/{item}")
    return links


def is_enabled(plan: Plan, unit: str) -> bool:
    """Check installed enablement links without running systemctl."""
    path = find_unit(plan, unit)
    if path is None or plan.readlink(f"{SYSTEM_DIR}/{unit}") == "/dev/null":
        return False
    return any(plan.readlink(link) is not None for link in _install_links(plan, unit, path))


def enable(plan: Plan, unit: str) -> list[str]:
    """Stage enabling unit; return the symlinks involved."""
    path = find_unit(plan, unit)
    if path is None:
        raise Error(f"systemd unit {unit} is not installed on the target")
    mask = f"{SYSTEM_DIR}/{unit}"
    if plan.readlink(mask) == "/dev/null":
        plan.remove(mask)
    links = _install_links(plan, unit, path)
    if not links:
        raise Error(f"systemd unit {unit} cannot be enabled: no [Install] section")
    vendor_paths = {f"{d}/{unit}" for d in VENDOR_DIRS}
    for link in links:
        # /lib and /usr/lib are the same directory on merged-/usr systems.
        if path in vendor_paths and plan.readlink(link) in vendor_paths:
            continue
        plan.symlink(link, path)
    return links + [mask]


def disable(plan: Plan, unit: str) -> list[str]:
    """Stage disabling unit; return the symlinks involved."""
    path = find_unit(plan, unit)
    if path is None:
        return []
    links = _install_links(plan, unit, path)
    for link in links:
        if plan.readlink(link) is not None:
            plan.remove(link)
    return links


def daemon_reload(plan: Plan, paths: list[str]) -> None:
    """Make a live systemd notice changed unit files or links."""
    if plan.changed(*paths):
        plan.command(["systemctl", "daemon-reload"])
