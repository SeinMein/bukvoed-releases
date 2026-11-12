"""Reproducibly patch the shipped application without unavailable original sources.

Run with Python 3.13: release_patch.py BASE_EXE OUTPUT_EXE
Updates lobby detection, the player retry guard and version labels, and installs
the visual chair controller from chair_fix.py.
The bootloader and all other bundled archive entries are preserved.
"""
import hashlib
import marshal
from pathlib import Path
import struct
import subprocess
import sys
import types
import zlib

COOKIE = '!8sIIII64s'
ENTRY = '!IIIIBc'
MAGIC = b'MEI\014\013\012\013\016'
BASE_VERSION = '2026.11.08.14'
VERSION = '2026.11.12.5'
BASE_SHA256 = 'b953dc26d91f12865521c014ef911f1e6bcc660fab19a8fe794cd99ce8d09929'
MOVEMENT_COMMIT = '0cdeacde42f240054bed0fa1fc28badf4a69ace9'
MOVEMENT_SHA256 = '64f26320b85ea5ef6381a8b114b345815c4478ca4d3057cb64c4988b3be8e047'


def read_archive(path):
    return read_archive_bytes(Path(path).read_bytes())


def read_archive_bytes(data):
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


def movement_code():
    repository = Path(__file__).resolve().parent.parent
    exe = subprocess.check_output(['git', '-C', str(repository), 'show',
                                   f'{MOVEMENT_COMMIT}:app/BukvoedApp.exe'])
    assert hashlib.sha256(exe).hexdigest() == MOVEMENT_SHA256
    entry = next(e for e in read_archive_bytes(exe)[1] if e[0] == 'bot')
    return marshal.loads(zlib.decompress(entry[1]) if entry[3] else entry[1])


def patch_code(code):
    source = Path(__file__).with_name('restart_fix.py')
    replacement = compile(source.read_text(encoding='utf-8'), 'maintenance/restart_fix.py', 'exec')
    lobby = next(c for c in replacement.co_consts if isinstance(c, types.CodeType) and c.co_name == 'lobby_visible')
    sender = next(c for c in replacement.co_consts if isinstance(c, types.CodeType) and c.co_name == 'press')
    original_sender = next(c for c in code.co_consts if isinstance(c, types.CodeType) and c.co_name == 'press')
    sender = sender.replace(co_consts=tuple(original_sender if isinstance(c, types.CodeType) and c.co_name == 'original'
                                           else c for c in sender.co_consts))
    chair_source = Path(__file__).with_name('chair_fix.py')
    chair_code = compile(chair_source.read_text(encoding='utf-8'), 'maintenance/chair_fix.py', 'exec')
    chair_functions = {c.co_name:c for c in chair_code.co_consts if isinstance(c, types.CodeType)}
    interaction_source = Path(__file__).with_name('interaction_fix.py')
    interaction_code = compile(interaction_source.read_text(encoding='utf-8'), 'maintenance/interaction_fix.py', 'exec')
    interactions = {('Reader.'+c.co_name if c.co_name=='letter_choices' else c.co_name):c
                    for c in interaction_code.co_consts if isinstance(c, types.CodeType) and c.co_name!='App'}
    app_code = next(c for c in interaction_code.co_consts if isinstance(c, types.CodeType) and c.co_name=='App')
    interactions['App.run'] = next(c for c in app_code.co_consts if isinstance(c, types.CodeType) and c.co_name=='run')
    changes = []

    def walk(item):
        if item.co_qualname == 'press':
            changes.append('movement virtual keys')
            return sender
        if item.co_qualname == 'Reader.lobby_visible':
            changes.append('lobby detector')
            return lobby.replace(co_qualname='Reader.lobby_visible')
        if item.co_qualname in chair_functions:
            changes.append(item.co_qualname)
            return chair_functions[item.co_qualname]
        if item.co_qualname in interactions:
            changes.append(item.co_qualname)
            return interactions[item.co_qualname].replace(co_qualname=item.co_qualname)
        constants = tuple(walk(c) if isinstance(c, types.CodeType)
                          else c.replace('v12', 'v22') if isinstance(c, str) else c
                          for c in item.co_consts)
        return item.replace(co_consts=constants)

    result = walk(code)
    assert sorted(changes) == sorted([
        *chair_functions, *interactions, 'lobby detector', 'movement virtual keys']), changes
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
