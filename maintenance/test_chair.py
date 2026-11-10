"""Exercise the packaged chair routine with simulated capture and keyboard.

No real keyboard/mouse events are sent. Validates jump, walking away,
walking back, camera search, match transition and interrupted input.
Usage: Python 3.13 test_chair.py BASE_EXE FIXED_EXE
"""
import asyncio
import marshal
from pathlib import Path
from types import FunctionType, SimpleNamespace, CodeType
import sys
import numpy as np
from PIL import Image
from release_patch import movement_code
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
        return SimpleNamespace(size=image.size, rgb=image.tobytes())


class Reader:
    def __init__(self, answers=()):
        self.answers = iter(answers)
    async def lobby_visible(self, image):
        return next(self.answers, True)


async def run(base_path, fixed_path):
    base, fixed = code_in_exe(base_path), code_in_exe(fixed_path)
    previous = movement_code()
    adapter = find(fixed, 'attempt_chair_restart')
    legacy = next(c for c in adapter.co_consts if isinstance(c, CodeType))
    # The restored procedure and direction detector are the actual earlier
    # implementation, not a new approximation of the former route.
    assert legacy == find(previous, 'restart_at_chair')
    assert find(fixed, 'chair_grass_direction') == find(previous, 'chair_grass_direction')
    for name in ('press', 'move_chair_camera', 'chair_view', 'send_word'):
        assert find(base, name) == find(fixed, name), name

    async def scenario(values, answers=(), directions=((31, 17),), attempt=1,
                       stop_after_key=False):
        window = (123, {'left': 0, 'top': 0, 'width': 160, 'height': 90})
        stopped = Stop()
        keys, camera, events = [], [], []
        direction_iter = iter(directions)
        def press(scan, hold, stop, hwnd):
            keys.append((scan, hold))
            if stop_after_key:
                stop.stopped = True
        def move_camera(win, stop, dx, dy, zoom=0):
            camera.append((dx, dy, zoom))
            return not stop.stopped
        env = dict(np=np, Image=Image, __builtins__=__builtins__,
                   game_window=lambda: window, can_type=lambda h, s: not s.stopped,
                   press=press, move_chair_camera=move_camera,
                   chair_grass_direction=lambda image: next(direction_iter, (31, 17)))
        env['chair_view'] = FunctionType(find(base, 'chair_view'), env)
        env['attempt_chair_restart'] = FunctionType(adapter, env)
        restart = FunctionType(find(fixed, 'restart_at_chair'), env)
        diagnostics = SimpleNamespace(event=events.append, folder=Path('.'))
        outcome = await restart(Reader(answers), Capture(values), window, stopped,
                                attempt, diagnostics, set())
        return outcome, keys, camera

    result, keys, camera = await scenario([0, 40, 0])
    assert result == 'returned', result
    assert [k for k, _ in keys] == [57, 31, 17], keys
    assert [round(h, 2) for _, h in keys] == [0.10, 0.42, 0.46], keys
    assert camera == []

    result, keys, camera = await scenario([0, 40, 0], (True, True, False))
    assert result == 'transition'
    assert [k for k, _ in keys] == [57, 31, 17]

    result, keys, camera = await scenario([0, 0, 40, 0],
                                         directions=(None, (31, 17)))
    assert result == 'returned'
    assert camera == [(0, 12, 0)], camera
    assert [k for k, _ in keys] == [57, 31, 17]

    result, keys, camera = await scenario([0, 0, 0], attempt=2)
    assert result == 'no_motion'
    assert camera == [(8, 0, 0)], camera
    assert [k for k, _ in keys] == [57, 31]

    result, keys, camera = await scenario([0], (False,))
    assert result == 'interrupted' and not keys and not camera
    result, keys, camera = await scenario([0], stop_after_key=True)
    assert result == 'interrupted'
    assert [k for k, _ in keys] == [57], keys
    print('PASS: original Git movement/direction restored byte-for-byte; '
          'keyboard/camera input code unchanged; jump/walk/back, transition, '
          'camera search without reverse, no motion and Stop scenarios')


if __name__ == '__main__':
    asyncio.run(run(*sys.argv[1:]))
