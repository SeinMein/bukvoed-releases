"""Execute the packaged worker and input sender without real OS input."""
import asyncio
from types import SimpleNamespace, FunctionType, CodeType
import sys
from PIL import Image
from test_restart import code_in_exe, find


class ReachedLobby(Exception):
    pass


class EndFrames(Exception):
    pass


def cell(value):
    return (lambda: value).__closure__[0]


async def startup(code):
    calls = []
    class Reader:
        def __init__(self):
            self.last = {'turn_icon': {'present': False}, 'card_row': []}
            self.reads = 0
        async def read(self, image, nickname, check_rejection=False):
            self.reads += 1
            if self.reads == 2:
                raise EndFrames()
            return None
        async def letter_choices(self, image):
            return []
        async def lobby_visible(self, image):
            calls.append('lobby')
            raise ReachedLobby()
    class Capture:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def grab(self, region):
            return SimpleNamespace(size=(2, 2), rgb=b'\0' * 12)
    stop = SimpleNamespace(is_set=lambda: False, wait=lambda t: False)
    app = SimpleNamespace(stopped=stop, match_clock=None, diagnostics=SimpleNamespace(event=lambda s: None),
                          pace_mode='turbo', play_mode='fast-loss', restart_mode='player',
                          extra_mode='off', notify=lambda text: None)
    cells = dict(bank=[], delay=0, learning=None, ledger=SimpleNamespace(), mode='',
                 new_ledger=lambda: None, nickname='', self=app, tracker=None)
    env = dict(__builtins__=__builtins__, Reader=Reader, TurnGate=lambda: SimpleNamespace(),
               TurnTimer=lambda p: None, BASE='', mss=SimpleNamespace(mss=Capture),
               time=SimpleNamespace(monotonic=lambda: 10.0), Image=Image,
               game_window=lambda: (123, {}), PACE_MODES=('fast', 'slow', 'turbo'),
               PLAY_MODES=('win', 'timed', 'fast-loss'), EXTRA_MODES=('off', 'bully', 'learn'),
               RESTART_MODES=('player', 'bot', 'off'), move_cursor_off_cards=lambda *a: None)
    # BASE participates in Path / filename before any capture.
    from pathlib import Path
    env['BASE'] = Path('.')
    loop = find(code, 'App.run.<locals>.loop')
    fn = FunctionType(loop, env, closure=tuple(cell(cells[n]) for n in loop.co_freevars))
    try:
        await fn()
    except (ReachedLobby, EndFrames):
        pass
    return calls


def keyboard(base, fixed):
    sender = find(fixed, 'press')
    original = next(c for c in sender.co_consts if isinstance(c, CodeType))
    assert original == find(base, 'press')
    for scan, vk in ((17, 0x57), (30, 0x41), (31, 0x53), (32, 0x44), (57, 0x20)):
        packets, logs = [], []
        clock = [0.0]
        def wait(seconds):
            clock[0] += seconds
            return False
        def send(count, packet, size):
            packets.append(packet.ki)
            return 1
        env = dict(__builtins__=__builtins__, can_type=lambda h, s: True,
                   INPUT=lambda **kw: SimpleNamespace(**kw), KEYBDINPUT=lambda *a: a,
                   C=SimpleNamespace(byref=lambda e: e, sizeof=lambda _: 40),
                   user32=SimpleNamespace(SendInput=send),
                   time=SimpleNamespace(monotonic=lambda: clock[0]),
                   logging=SimpleNamespace(info=lambda *a: logs.append(a)))
        fn = FunctionType(sender, env)
        env['press'] = fn
        fn._chair_input = True
        fn(scan, 0.12, SimpleNamespace(wait=wait), 123)
        assert packets == [(vk, scan, 0, 0, 0), (vk, scan, 2, 0, 0)], packets
        assert clock[0] >= 0.12 and logs


async def run(base_path, fixed_path):
    base, fixed = code_in_exe(base_path), code_in_exe(fixed_path)
    assert await startup(base) == [], 'Base must reproduce fresh-lobby deadlock'
    assert await startup(fixed) == ['lobby'], 'Worker must check lobby before first own turn'
    keyboard(base, fixed)
    print('PASS: real packaged worker starts lobby detection without a prior own turn; '
          'WASD/Space virtual-key down/hold/up verified; original typing sender preserved')


if __name__ == '__main__':
    asyncio.run(run(*sys.argv[1:]))
