"""Exceptions reported to the user."""


class Error(Exception):
    """An error reported to the user without a traceback."""


class Unsupported(Error):
    """The target or platform does not support a requested feature."""
