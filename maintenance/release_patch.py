"""Reproducibly patch the shipped application without unavailable original sources.

Run with Python 3.13: release_patch.py BASE_EXE OUTPUT_EXE
Only Reader.lobby_visible, the player retry guard, and version labels change.
The bootloader and all other bundled archive entries are preserved.
"""
import dis
import hashlib
import marshal
from pathlib import Path
import struct
import sys
import types
import zlib

COOKIE = '!8sIIII64s'
ENTRY = '!IIIIBc'
MAGIC = b'MEI\014\013\012\013\016'
BASE_VERSION = '2026.11.08.14'
VERSION = '2026.11.10.1'
BASE_SHA256 = 'b953dc26d91f12865521c014ef911f1e6bcc660fab19a8fe794cd99ce8d09929'


def read_archive(path):
    data = Path(path).read_bytes()
    pos = data.rfind(MAGIC)
    magic, length, toc_offset, toc_length, pyver, library = struct.unpack_from(COOKIE, data, pos)
    assert pos + struct.calcsize(COOKIE) == len(data), 'Unexpected executable trailer'
    assert magic == MAGIC and pyver == 313, 'Expected Python 3.13 application'
    start = len(data) - length
    entries = []
    offset = start + toc_offset
    end = offset + toc_length
    while offset < end:
        size, blob_offset, compressed_size, size_raw, compressed, kind = struct.unpack_from(ENTRY, data, offset)
        name = data[offset + struct.calcsize(ENTRY):offset + size].rstrip(b'\0').decode('utf-8')
        blob = data[start + blob_offset:start + blob_offset + compressed_size]
        entries.append((name, blob, size_raw, compressed, kind))
        offset += size
    assert offset == end
    return data[:start], entries, pyver, library


def patch_code(code):
    source = Path(__file__).with_name('restart_fix.py')
    replacement = compile(source.read_text(encoding='utf-8'), 'maintenance/restart_fix.py', 'exec')
    lobby = next(c for c in replacement.co_consts if isinstance(c, types.CodeType) and c.co_name == 'lobby_visible')
    changes = []

    def walk(item):
        if item.co_qualname == 'Reader.lobby_visible':
            changes.append('lobby detector')
            return lobby.replace(co_qualname='Reader.lobby_visible')
        constants = tuple(walk(c) if isinstance(c, types.CodeType)
                          else c.replace('v12', 'v15') if isinstance(c, str) else c
                          for c in item.co_consts)
        if item.co_qualname == 'App.run.<locals>.loop':
            instructions = list(dis.get_instructions(item))
            # Preserve the existing retry delays, three-direction cycle and
            # Stop/focus guards; remove only the permanent player-mode cutoff.
            guards = [i for i in instructions if i.opname == 'LOAD_CONST'
                      and i.argval == 3 and i.positions.lineno == 1555]
            assert len(guards) == 1, 'Retry guard differs from base release'
            index = len(constants)
            assert index < 256 and guards[0].arg < 256
            bytecode = bytearray(item.co_code)
            bytecode[guards[0].offset + 1] = index
            constants += (float('inf'),)
            item = item.replace(co_code=bytes(bytecode))
            changes.append('continuous player retries')
        return item.replace(co_consts=constants)

    result = walk(code)
    assert sorted(changes) == ['continuous player retries', 'lobby detector'], changes
    return result


def write_archive(path, prefix, entries, pyver, library):
    blobs = bytearray()
    table = bytearray()
    for name, blob, size_raw, compressed, kind in entries:
        encoded = name.encode('utf-8') + b'\0'
        entry_size = struct.calcsize(ENTRY) + len(encoded)
        entry_size = (entry_size + 15) // 16 * 16
        table.extend(struct.pack(ENTRY, entry_size, len(blobs), len(blob), size_raw, compressed, kind))
        table.extend(encoded.ljust(entry_size - struct.calcsize(ENTRY), b'\0'))
        blobs.extend(blob)
    cookie = struct.pack(COOKIE, MAGIC, len(blobs) + len(table) + struct.calcsize(COOKIE),
                         len(blobs), len(table), pyver, library)
    Path(path).write_bytes(prefix + blobs + table + cookie)


def build(base, output):
    assert sys.version_info[:2] == (3, 13), 'Use Python 3.13'
    assert hashlib.sha256(Path(base).read_bytes()).hexdigest() == BASE_SHA256, 'Base executable differs from release 2026.11.08.14'
    prefix, entries, pyver, library = read_archive(base)
    changed = []
    for name, blob, size_raw, compressed, kind in entries:
        if name == 'bot':
            raw = zlib.decompress(blob) if compressed else blob
            raw = marshal.dumps(patch_code(marshal.loads(raw)))
            blob, size_raw = (zlib.compress(raw, 9) if compressed else raw), len(raw)
        changed.append((name, blob, size_raw, compressed, kind))
    write_archive(output, prefix, changed, pyver, library)
    _, actual, _, _ = read_archive(output)
    assert actual == changed, 'Archive round-trip failed'
    assert all(a == b for a, b in zip(entries, actual) if a[0] != 'bot')
    print(f'{VERSION}: {hashlib.sha256(Path(output).read_bytes()).hexdigest()}')


if __name__ == '__main__':
    build(*sys.argv[1:])
