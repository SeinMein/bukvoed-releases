"""Test the packaged visual return controller without sending OS input.

Usage: test_chair.py BASE_EXE FIXED_EXE [LOCAL_RECORDING]
The optional video is a local recording of the zoomed-out lobby failure.
"""
import asyncio
from pathlib import Path
from types import FunctionType, SimpleNamespace as NS
import sys
import cv2
import numpy as np
from PIL import Image
from test_restart import code_in_exe, find


class Stop:
    stopped = False

    def is_set(self):
        return self.stopped

    def wait(self, seconds):
        return self.stopped


class Capture:
    def __init__(self, values):
        self.values = iter(values)
        self.value = 0

    def grab(self, region):
        self.value = next(self.values, self.value)
        image = Image.new('RGB', (160, 90), (self.value,) * 3)
        return NS(size=image.size, rgb=image.tobytes())


class Reader:
    def __init__(self, answers=()):
        self.answers = iter(answers)

    async def lobby_visible(self, image):
        return next(self.answers, True)


async def run(base_path, fixed_path, recording=None):
    base, fixed = code_in_exe(base_path), code_in_exe(fixed_path)
    # Startup and virtual-key handling from v17 are exercised separately.
    for name in ('send_word', 'press'):
        assert find(base, name) == find(fixed, name), name

    async def scenario(values, answers=(), directions=((31, 17),), stop_after_key=False):
        window = (123, {'left': -1280, 'top': 40, 'width': 1280, 'height': 720})
        stopped = Stop()
        keys, camera, events = [], [], []
        direction_iter = iter(directions)

        def press(scan, hold, stop, hwnd):
            assert press._chair_input
            keys.append(scan)
            if stop_after_key:
                stop.stopped = True

        def move_camera(win, stop, dx, dy, zoom=0):
            camera.append((dx, dy, zoom))
            return not stop.stopped

        env = dict(np=np, Image=Image, __builtins__=__builtins__,
                   game_window=lambda: window, can_type=lambda h, s: not s.stopped,
                   press=press, move_chair_camera=move_camera,
                   chair_grass_direction=lambda image, excluded=(): next(direction_iter, None),
                   W=NS(POINT=lambda x, y: NS(x=x, y=y)),
                   C=NS(byref=lambda e: e, sizeof=lambda _: 40),
                   INPUT=lambda **kw: NS(**kw), MOUSEINPUT=lambda *a: a,
                   user32=NS(WindowFromPoint=lambda point: 123, GetAncestor=lambda *a: 123,
                             SetCursorPos=lambda *a: True, SendInput=lambda *a: 1))
        env['chair_view'] = FunctionType(find(fixed, 'chair_view'), env)
        env['attempt_chair_restart'] = FunctionType(find(fixed, 'attempt_chair_restart'), env)
        restart = FunctionType(find(fixed, 'restart_at_chair'), env)
        diagnostics = NS(event=events.append, folder=Path('.'))
        # Use a local scratch location for failure screenshots.
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            diagnostics.folder = Path(directory)
            outcome = await restart(Reader(answers), Capture(values), window, stopped,
                                    1, diagnostics, set())
        assert press._chair_input is False
        return outcome, keys, camera

    result, keys, camera = await scenario([0, 0, 0, 40, 0])
    assert result == 'returned' and keys == [31, 17]
    assert camera == [(0, 8, 0), (0, -8, 0)]

    result, keys, camera = await scenario([0, 0, 0, 40], (True, True, True, False))
    assert result == 'transition' and keys == [31]
    assert camera == [(0, 8, 0)]

    result, keys, camera = await scenario([0, 0, 0, 0, 40, 0])
    assert result == 'returned' and keys == [31, 57, 31, 17]

    result, keys, camera = await scenario([0], directions=(None,) * 20)
    assert result == 'grass_unconfirmed' and not keys
    assert camera == [(0, 8, 0), (6, 0, 0), (-6, 0, 0), (-6, 0, 0),
                      (6, 0, 0), (0, -6, 0), (0, 6, 0), (0, -8, 0)]

    result, keys, camera = await scenario([0], stop_after_key=True)
    assert result == 'interrupted' and keys == [31]

    grass = FunctionType(find(fixed, 'chair_grass_direction'),
                         dict(np=np, Image=Image, __builtins__=__builtins__), argdefs=((),))
    image = Image.new('RGB', (1280, 720), (40, 40, 40))
    image.paste((30, 180, 40), (600, 418, 680, 500))
    for size in ((1280, 720), (1920, 1080), (2560, 1440), (2560, 1600)):
        assert grass(image.resize(size)) == (31, 17)
    assert grass(image, {31}) is None

    if recording:
        video = cv2.VideoCapture(str(recording))
        assert video.isOpened(), f'Recording unavailable: {recording}'
        checks = 0
        for index in range(0, min(int(video.get(cv2.CAP_PROP_FRAME_COUNT)), 600), 60):
            video.set(cv2.CAP_PROP_POS_FRAMES, index)
            ok, frame = video.read()
            assert ok
            image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            directions = [grass(image.resize(size)) for size in
                          ((1280, 720), (1920, 1080), (2560, 1440), (2560, 1600))]
            assert directions[0] is not None, index
            assert all(direction == directions[0] for direction in directions), (index, directions)
            checks += len(directions)
        video.release()
        assert checks >= 24
        print(f'PASS: {checks} recorded lobby direction checks across four resolutions')

    for width, height in ((1280, 720), (2560, 1440)):
        events = []
        window = (123, {'left': -width, 'top': 40, 'width': width, 'height': height})
        env = dict(round=round, game_window=lambda: window, can_type=lambda *a: True,
                   W=NS(POINT=lambda x, y: NS(x=x, y=y)),
                   C=NS(byref=lambda e: e, sizeof=lambda _: 40),
                   INPUT=lambda **kw: NS(**kw), MOUSEINPUT=lambda *a: a,
                   user32=NS(WindowFromPoint=lambda p: 123, GetAncestor=lambda *a: 123,
                             SetCursorPos=lambda *a: True,
                             SendInput=lambda n, e, s: events.append(e.mi) or 1))
        move = FunctionType(find(fixed, 'move_chair_camera'), env, argdefs=(0, 0, 0))
        assert move(window, Stop(), 6, -6)
        assert events[0][3] == 8 and events[-1][3] == 16
        assert all(event[:2] == (round(6*width/1280), round(-6*height/720))
                   for event in events[1:-1])

    print('PASS: walking, conditional jumping, transition, camera rollback, Stop, '
          'virtual-input scope and viewport-scaled mouse movement')


if __name__ == '__main__':
    asyncio.run(run(*sys.argv[1:]))
