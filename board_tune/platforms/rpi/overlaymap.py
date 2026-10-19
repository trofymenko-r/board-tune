"""Read firmware overlay aliases from the target's FDT v17 overlay_map.dtb."""

import struct


def properties(data):
    """Return (node path, property) values, or None for malformed/unknown FDT.

    Only reads the blob; no host dependency or target executable is needed.
    Bounds, node nesting and duplicate properties are checked before use.
    """
    try:
        magic, total, tree, strings, reserve, version, compat, _, ns, nt = struct.unpack_from(">10I", data)
        if (magic != 0xD00DFEED or version < 17 or compat > 17 or total > len(data)
                or tree < 40 or tree % 4 or strings < 40 or reserve < 40
                or tree + nt > total or strings + ns > total
                or not (tree + nt <= strings or strings + ns <= tree)):
            return None
        blob, names = data[tree:tree + nt], data[strings:strings + ns]
        offset, stack, result, root_seen, nodes = 0, [], {}, False, set()
        while offset < len(blob):
            token, = struct.unpack_from(">I", blob, offset)
            offset += 4
            if token == 1:
                end = blob.index(b"\0", offset)
                name = blob[offset:end].decode("ascii")
                if "/" in name or stack and not name or not stack and (root_seen or name):
                    return None
                if not stack:
                    root_seen = True
                stack.append(name)
                path = "/".join(stack) or "/"
                if path in nodes:
                    return None
                nodes.add(path)
                offset = (end + 4) & ~3
            elif token == 2:
                stack.pop()
            elif token == 3:
                length, nameoff = struct.unpack_from(">2I", blob, offset)
                offset += 8
                if not stack or offset + length > len(blob):
                    return None
                end = names.index(b"\0", nameoff)
                name = names[nameoff:end].decode("ascii")
                key = ("/".join(stack) or "/", name)
                if not name or key in result:
                    return None
                result[key] = blob[offset:offset + length]
                offset = (offset + length + 3) & ~3
            elif token == 4:
                pass
            elif token == 9:
                return result if root_seen and not stack and offset == len(blob) else None
            else:
                return None
    except (struct.error, ValueError, IndexError, UnicodeError):
        return None
    return None


def uart_aliases(data, board):
    props = properties(data or b"")
    if props is None or board.id != "RPI5":
        return {}
    # Require the exact CPU-family mapping, not a string found anywhere in FDT.
    return {f"uart{port}": f"uart{port}-pi5" for port in range(5)
            if props.get((f"/uart{port}", "bcm2712")) == f"uart{port}-pi5\0".encode()}
