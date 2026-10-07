"""Helpers for kernel command line tokens."""

from __future__ import annotations


def key(token: str) -> str:
    """The parameter name of a token: "console" for "console=tty1"."""
    return token.split("=", 1)[0]


def value(token: str) -> str:
    return token.split("=", 1)[1] if "=" in token else ""


def without(tokens: list[str], *keys: str) -> list[str]:
    return [t for t in tokens if key(t) not in keys]


def set_param(tokens: list[str], name: str, val: object = None) -> list[str]:
    """Set a parameter, replacing all existing occurrences in place."""
    token = name if val is None else f"{name}={val}"
    out: list[str] = []
    for t in tokens:
        if key(t) != name:
            out.append(t)
        elif token not in out:
            out.append(token)
    if token not in out:
        out.append(token)
    return out
