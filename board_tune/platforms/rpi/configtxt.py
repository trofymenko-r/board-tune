"""Managed blocks in the Raspberry Pi config.txt."""

from __future__ import annotations

from board_tune.errors import Error


def _markers(name: str) -> tuple[str, str]:
    return f"# >>> board-tune: {name} >>>", f"# <<< board-tune: {name} <<<"


def _site_settings_after(text: str, end: str) -> bool:
    """Other managed blocks do not require shuffling blocks on every apply."""
    inside = False
    for raw in text.partition(end)[2].splitlines():
        line = raw.strip()
        if line.startswith("# >>> board-tune:"):
            inside = True
        elif line.startswith("# <<< board-tune:"):
            inside = False
        elif not inside and line and not line.startswith("#"):
            return True
    return False


def set_block(text: str, name: str, lines: list[str], *, at_end: bool = False) -> str:
    """Replace the block called name with lines; empty lines remove it.

    An existing block is replaced where it is, a new one is appended,
    so re-applying the same settings leaves the file untouched. With at_end,
    move the block after site directives so they cannot override its values.
    """
    begin, end = _markers(name)
    move = at_end and bool(lines) and _site_settings_after(text, end)
    out: list[str] = []
    position = None
    inside = False
    last = ""
    for line in text.splitlines():
        if line.strip() == begin:
            inside = True
            last = ""
            if move and out and not out[-1].strip():
                out.pop()
            position = len(out)
        elif inside:
            if line.strip() == end:
                inside = False
                if move and last.strip() == "[all]":
                    # Site lines originally followed the block's scope reset.
                    # Moving it must not put them under an earlier filter.
                    out.append("[all]")
            else:
                last = line
        else:
            out.append(line)
    if inside:
        raise Error(f"config.txt: block '{name}' is not terminated by '{end}'")

    block = [begin, *lines, end] if lines else []
    if position is None or move:
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
