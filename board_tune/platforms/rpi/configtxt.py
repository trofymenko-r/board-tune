"""Managed blocks in the Raspberry Pi config.txt."""

from __future__ import annotations

from board_tune.errors import Error


def _markers(name: str) -> tuple[str, str]:
    return f"# >>> board-tune: {name} >>>", f"# <<< board-tune: {name} <<<"


def set_block(text: str, name: str, lines: list[str]) -> str:
    """Replace the block called name with lines; empty lines remove it.

    An existing block is replaced where it is, a new one is appended,
    so re-applying the same settings leaves the file untouched.
    """
    begin, end = _markers(name)
    out: list[str] = []
    position = None
    inside = False
    for line in text.splitlines():
        if line.strip() == begin:
            inside = True
            position = len(out)
        elif inside:
            if line.strip() == end:
                inside = False
        else:
            out.append(line)
    if inside:
        raise Error(f"config.txt: block '{name}' is not terminated by '{end}'")

    block = [begin, *lines, end] if lines else []
    if position is None:
        if not block:
            return text
        while out and not out[-1].strip():
            out.pop()
        out += [""] + block
    elif block:
        out[position:position] = block
    elif position == len(out):
        # Drop the separator added in front of a trailing block.
        while out and not out[-1].strip():
            out.pop()
    return "\n".join(out) + "\n"
