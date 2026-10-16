"""Command line interface."""

from __future__ import annotations

import argparse
import os
import shlex
import sys
import tempfile
from contextlib import ExitStack, nullcontext

from board_tune import __version__
from board_tune.backup import Backup, DIRECTORY as BACKUP_DIRECTORY
from board_tune.errors import Error
from board_tune.kconfig import Config, Configuration
from board_tune.plan import Change, Plan
from board_tune.registry import Context, Registry
from board_tune.target import Target, open_target

EPILOG = """\
Without --load the configuration menu opens; --load switches to batch
mode, where invalid settings are errors. TARGET is [user@]host for a
running board, or a block device, disk image or root directory for
offline customization. With TARGET, the menu starts from its current
settings. Without TARGET the configuration is only
edited, validated or saved.
"""


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="board-tune", epilog=EPILOG,
        description="Customize a Linux board over SSH or through its boot media.")
    p.add_argument("target", nargs="?", metavar="TARGET",
                   help="SSH destination, block device, disk image or directory")
    p.add_argument("--load", action="append", default=[], metavar="FILE",
                   help="load a configuration file; repeat to merge several")
    p.add_argument("--set", action="append", default=[], metavar="SYMBOL=VALUE",
                   help="override one option; may be repeated")
    p.add_argument("--save", metavar="FILE",
                   help="save the minimal configuration to FILE")
    p.add_argument("--menu", action="store_true",
                   help="open the menu even when --load is given")
    p.add_argument("-n", "--dry-run", action="store_true",
                   help="show the changes without applying them")
    p.add_argument("-y", "--yes", action="store_true",
                   help="apply without asking for confirmation")
    recovery = p.add_mutually_exclusive_group()
    recovery.add_argument("--list-backups", action="store_true", help="list target file backups")
    recovery.add_argument("--restore", metavar="ID", help="restore files from a target backup")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p.parse_args(argv)


def _color(code: str, text: str) -> str:
    if sys.stdout.isatty() and "NO_COLOR" not in os.environ:
        return f"\033[{code}m{text}\033[0m"
    return text


def _warn(msg: str) -> None:
    print(f"board-tune: warning: {msg}", file=sys.stderr)


def _require_tty(what: str) -> None:
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        raise Error(f"{what} needs a terminal")


def build_plan(registry: Registry, config: Config, target: Target,
               skip_hooks: set[str] | None = None) -> Plan:
    platform, board = registry.board(config)
    if target.live:
        detected = platform.detect_board(target)
        if detected is not None and detected != board:
            raise Error(f"{target.description} is a {detected.title}, but the "
                        f"configuration is for a {board.title}")
    plan = Plan(target)
    ctx = Context(plan, config, platform, board)
    for hook in registry.hooks:
        if config.enabled(hook.symbol) and (not skip_hooks or hook.name not in skip_hooks):
            hook.plan(ctx)
    return plan


def show_plan(changes: list[Change], commands: list[list[str]]) -> None:
    for change in changes:
        line = f"  {change.action:<8} {change.path}"
        if change.action == "symlink":
            line += f" -> {change.detail}"
        elif change.action == "attrs":
            line += f" ({change.detail})"
        print(line)
        if change.action not in ("create", "modify"):
            continue
        for diff in change.detail.splitlines():
            if diff.startswith(("---", "+++")):
                continue
            if diff.startswith("+"):
                diff = _color("32", diff)
            elif diff.startswith("-"):
                diff = _color("31", diff)
            elif diff.startswith("@@"):
                diff = _color("36", diff)
            print(f"      {diff}")
    for argv in commands:
        print(f"  {'run':<8} {shlex.join(argv)}")


def _confirm(question: str) -> bool:
    _require_tty("confirmation (use --yes in scripts)")
    return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")


def apply(args: argparse.Namespace, registry: Registry, config: Config,
          target: Target | None = None, skip_hooks: set[str] | None = None) -> int:
    with (nullcontext(target) if target is not None else open_target(args.target)) as target:
        plan = build_plan(registry, config, target, skip_hooks)
        changes = plan.changes()
        _, board = registry.board(config)
        print(f"Target: {target.description}, {board.title}")
        if not changes and not plan.pre_commands and not plan.commands:
            print("Nothing to change.")
            return 0
        show_plan([], plan.pre_commands)
        show_plan(changes, plan.commands)
        for note in plan.notes:
            print(f"note: {note}")
        if args.dry_run:
            return 0
        if not args.yes and not _confirm(f"Apply {len(changes) + len(plan.pre_commands) + len(plan.commands)} change(s)?"):
            print("Aborted.")
            return 1
        plan.apply(changes)
        if plan.backup:
            print(f"Backup: {plan.backup.id}")
        print("Done.")
    return 0


def run(args: argparse.Namespace) -> int:
    if args.list_backups or args.restore:
        if not args.target:
            raise Error("backup operations require TARGET")
        if args.load or args.set or args.save or args.menu:
            raise Error("backup operations cannot be combined with configuration options")
        with open_target(args.target) as target:
            if args.list_backups:
                for name in target.listdir(BACKUP_DIRECTORY):
                    if name.endswith(".json"):
                        print(name[:-5])
                return 0
            backup = Backup.load(target, args.restore)
            print(f"Restore backup {backup.id} on {target.description}:")
            for path in backup.entries:
                print(f"  restore  {path}")
            if args.dry_run:
                return 0
            if not args.yes and not _confirm("Restore these files?"):
                return 1
            # A manual restore can itself be undone.
            safety = Backup.create(target, list(backup.entries))
            backup.restore()
            print(f"Restored. Pre-restore backup: {safety.id}")
            print("Reboot or reload affected services to activate restored settings.")
            return 0
    registry = Registry.load()
    interactive = args.menu or not args.load
    if interactive:
        _require_tty("the configuration menu (use --load for batch mode)")
    with tempfile.TemporaryDirectory(prefix="board-tune-") as workdir, ExitStack() as targets:
        conf = Configuration(registry, workdir)
        for path in args.load:
            conf.load(path)
        for assignment in args.set:
            conf.set(assignment)
        target = None
        baseline = {}
        if interactive and args.target:
            # For an ambiguous image, an explicit board choice determines
            # which conditional boot settings to read.
            _, requested_board = registry.board(conf.config)
            hint = (requested_board if conf.kconf.syms[f"BOARD_{requested_board.id}"].user_value == 2
                    else None)
            conf = Configuration(registry, workdir)
            target = targets.enter_context(open_target(args.target))
            print(f"Reading current settings from {target.description}...")
            for note in conf.read_target(registry, target, hint):
                _warn(note)
            baseline = {hook.name: conf.hook_values(registry, hook) for hook in registry.hooks}
            for path in args.load:
                conf.load(path)
            for assignment in args.set:
                conf.set(assignment)

        if interactive:
            for problem in conf.problems():
                _warn(problem)
            if not conf.menu():
                print("Configuration discarded.")
                return 1
        else:
            problems = conf.problems()
            if problems:
                raise Error("invalid configuration:\n  " + "\n  ".join(problems))

        if args.save:
            print(conf.save(args.save))
        if args.target:
            unchanged = {hook.name for hook in registry.hooks
                         if baseline.get(hook.name) == conf.hook_values(registry, hook)}
            return apply(args, registry, conf.config, target, unchanged)
        if not args.save:
            print("No target given, nothing applied." if interactive
                  else "Configuration is valid.")
        return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        return run(args)
    except Error as e:
        print(f"board-tune: error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print()
        return 130
