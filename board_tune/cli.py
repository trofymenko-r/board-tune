"""Command line interface."""

from __future__ import annotations

import argparse
import os
import shlex
import sys
import tempfile

from board_tune import __version__
from board_tune.errors import Error
from board_tune.kconfig import Config, Configuration
from board_tune.plan import Change, Plan
from board_tune.registry import Context, Registry
from board_tune.target import Target, open_target

EPILOG = """\
Without --load the configuration menu opens; --load switches to batch
mode, where invalid settings are errors. TARGET is [user@]host for a
running board, or a block device, disk image or root directory for
offline customization. Without TARGET the configuration is only
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


def build_plan(registry: Registry, config: Config, target: Target) -> Plan:
    platform, board = registry.board(config)
    if target.live:
        detected = platform.detect_board(target)
        if detected is not None and detected != board:
            raise Error(f"{target.description} is a {detected.title}, but the "
                        f"configuration is for a {board.title}")
    plan = Plan(target)
    ctx = Context(plan, config, platform, board)
    for hook in registry.hooks:
        if config.enabled(hook.symbol):
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


def apply(args: argparse.Namespace, registry: Registry, config: Config) -> int:
    with open_target(args.target) as target:
        plan = build_plan(registry, config, target)
        changes = plan.changes()
        _, board = registry.board(config)
        print(f"Target: {target.description}, {board.title}")
        if not changes:
            print("Nothing to change.")
            return 0
        show_plan(changes, plan.commands)
        for note in plan.notes:
            print(f"note: {note}")
        if args.dry_run:
            return 0
        if not args.yes and not _confirm(f"Apply {len(changes)} change(s)?"):
            print("Aborted.")
            return 1
        plan.apply(changes)
        print("Done.")
    return 0


def run(args: argparse.Namespace) -> int:
    registry = Registry.load()
    with tempfile.TemporaryDirectory(prefix="board-tune-") as workdir:
        conf = Configuration(registry, workdir)
        for path in args.load:
            conf.load(path)
        for assignment in args.set:
            conf.set(assignment)

        interactive = args.menu or not args.load
        if interactive:
            _require_tty("the configuration menu (use --load for batch mode)")
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
            return apply(args, registry, conf.config)
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
