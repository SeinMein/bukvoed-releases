"""Regression checks against the code actually embedded in the executable.

python test_restart.py BASE_EXE FIXED_EXE [RECORDING_FRAME_DIRECTORY]
Requires the application's Python 3.13 image dependencies. Frame fixtures
are local diagnostics and are deliberately not published to the repository.
"""
import asyncio
import dis
import marshal
from pathlib import Path
import sys
import types
import zlib
import numpy as np
import cv2
from PIL import Image
from release_patch import read_archive


def code_in_exe(path):
    _, entries, _, _ = read_archive(path)
    _, blob, _, compressed, _ = next(e for e in entries if e[0] == 'bot')
    return marshal.loads(zlib.decompress(blob) if compressed else blob)


def find(code, qualname):
    if code.co_qualname == qualname:
        return code
    for const in code.co_consts:
        if isinstance(const, types.CodeType):
            result = find(const, qualname)
            if result is not None:
                return result


class OCR:
    ru = None
    def __init__(self, answers=()):
        self.answers = iter(answers)
        self.images = []
    async def text(self, image, language):
        self.images.append(image)
        return next(self.answers, '')


async def run(base_path, fixed_path, frame_dir=None):
    base, fixed = code_in_exe(base_path), code_in_exe(fixed_path)
    env = dict(np=np, Image=Image, cv2=cv2, __builtins__=__builtins__)
    env['crop_relative'] = types.FunctionType(find(base, 'crop_relative'), env)
    before = types.FunctionType(find(base, 'Reader.lobby_visible'), env)
    after = types.FunctionType(find(fixed, 'Reader.lobby_visible'), env)
    blank = Image.new('RGB', (1280, 720))
    assert not await after(OCR(), blank)
    assert not await after(OCR(('Тренировка', 'неизвестно')), blank)
    ocr = OCR(('ТРЕНИРОВКА', 'Инвентарь Профиль'))
    assert await after(ocr, blank)
    assert ocr.images[0].size == (423, 129), ocr.images[0].size
    red_close = blank.copy()
    red_close.paste((240, 30, 30), (1221, 12, 1264, 55))
    assert not await after(OCR(('Тренировка', 'Инвентарь')), red_close)

    from test_startup_input import startup
    assert await startup(base) == [], 'Base must reproduce fresh-lobby deadlock'
    assert await startup(fixed) == ['lobby'], 'Worker must check lobby before first own turn'

    count, reproduced = 0, 0
    if frame_dir:
        for path in Path(frame_dir).glob('*.jpg'):
            stem, seconds = path.stem.rsplit('_', 1)
            seconds = int(seconds)
            if '104020' in stem:
                expected_lobby = seconds == 0
            elif '104033' in stem:
                expected_lobby = seconds in (12, 15, 18, 30, 42, 45, 48)
            elif '104231' in stem:
                expected_lobby = seconds in (3, 6)
            else:
                raise AssertionError(f'Unlabelled fixture: {path.name}')
            image = Image.open(path)
            old = await before(OCR(), image)
            reproduced += int(expected_lobby and not old)
            for size in ((1280, 720), (1920, 1080), (2560, 1440)):
                ocr = OCR()
                actual = await after(ocr, image.resize(size))
                assert actual == expected_lobby, (path.name, size, actual, expected_lobby)
                if expected_lobby:
                    assert not ocr.images, 'Recorded lobby should not require OCR'
                count += 1
        assert reproduced >= 10, 'The corpus must reproduce the reported failure'
    print(f'PASS: lobby/OCR/close-menu checks; retry guard and archive code; '
          f'{count} labelled image checks; {reproduced} original lobby failures reproduced')


if __name__ == '__main__':
    asyncio.run(run(*sys.argv[1:]))
