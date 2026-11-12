"""Exercise packaged click dispatch and loss Enter rate without real OS input."""
import asyncio
import ctypes as C
from pathlib import Path
from types import FunctionType, MethodType, SimpleNamespace as NS
import sys
import tempfile
from unittest.mock import AsyncMock, patch
from PIL import Image
from test_restart import code_in_exe, find


async def run(executable, source_directory):
    sys.path.insert(0, str(source_directory))
    import bot
    code = code_in_exe(executable)
    env = dict(bot.__dict__)
    env['__builtins__'] = __builtins__
    image = Image.open(Path(source_directory).parents[1] / 'work/first-letter-choice.png').convert('RGB')
    reader = bot.Reader()
    reader.letter_choices = MethodType(FunctionType(find(code, 'Reader.letter_choices'), env,
                                                   argdefs=(None, None)), reader)
    cards = await reader.letter_choices(image, row=())
    assert len(cards) == 4
    reader.text = AsyncMock(return_value='')
    # Stable opening geometry/title must survive an OCR miss on the next frame.
    assert await reader.letter_choices(image, row=()) == cards
    changed = image.copy()
    changed.paste((0, 0, 0), (350, 60, 930, 130))
    assert not await reader.letter_choices(changed, row=())

    hwnd = 123
    region = dict(left=-1280, top=40, width=1280, height=720)
    window = (hwnd, region)
    packets = []
    pointer = [0, 0]
    clock = [0.]
    stopped = NS(is_set=lambda: False, wait=lambda t: clock.__setitem__(0, clock[0]+t) or False)

    def get_cursor(ptr):
        point = C.cast(ptr, C.POINTER(bot.W.POINT)).contents
        point.x, point.y = pointer
        return True

    def send(count, ptr, size):
        event = C.cast(ptr, C.POINTER(bot.INPUT)).contents.mi
        packets.append((event.dx, event.dy, event.dwFlags))
        # Reproduce the game putting the cursor back in the window centre.
        pointer[:] = [-640, 400]
        return 1

    fake = NS(WindowFromPoint=lambda p: hwnd, GetAncestor=lambda *a: hwnd,
              GetForegroundWindow=lambda: hwnd, GetCursorPos=get_cursor,
              GetSystemMetrics={76:-1280, 77:0, 78:3200, 79:1200}.get,
              SetCursorPos=lambda *a: True, SendInput=send)
    env.update(user32=fake, game_window=lambda: window, move_cursor_off_cards=lambda *a: None)
    capture = NS(grab=lambda _: NS(size=image.size, rgb=image.tobytes()))
    verify = AsyncMock(return_value=True)
    click = FunctionType(find(code, 'click_game_target'), env)
    assert await click(capture, hwnd, region, cards[0], stopped, verify)
    assert packets[-2][2] == 0xC003 and packets[-1][2] == 0xC005
    assert packets[-2][:2] == packets[-1][:2]
    # Losing focus during button hold must still release the button.
    packets.clear()
    def wait_and_blur(t):
        if t == .11:
            env['game_window'] = lambda: None
        return False
    stopped.wait = wait_and_blur
    assert await click(capture, hwnd, region, cards[0], stopped, verify)
    assert packets[-1] == (0, 0, 4)

    def burst(turbo, disappear=99, focus_until=99):
        clock = [0.]
        keys = []
        captures = []
        def wait(t):
            clock[0] += t
            return False
        def press(scan, hold, stop, window):
            keys.append(scan)
            clock[0] += hold
        def grab(region):
            captures.append(clock[0])
            return NS(size=(1, 1), rgb=bytes(3))
        current = dict(env)
        current.update(time=NS(monotonic=lambda:clock[0]), game_window=lambda:window,
                       can_type=lambda *a:clock[0]<focus_until, press=press)
        repeat = FunctionType(find(code, 'repeat_loss_enter'), current, argdefs=(False,))
        result = repeat(NS(turn_indicator=lambda im:dict(present=clock[0]<disappear)),
                        NS(grab=grab), window, NS(wait=wait), turbo)
        assert result == len(keys) and all(k == 28 for k in keys)
        assert len(captures) <= 4
        return len(keys), clock[0]
    for turbo in (False, True):
        count, elapsed = burst(turbo)
        assert count >= 20 and .25 <= elapsed < .27, (count, elapsed)
        count, elapsed = burst(turbo, disappear=.07)
        assert count < 12 and elapsed < .10
        count, elapsed = burst(turbo, focus_until=.04)
        assert elapsed < .06
    print('PASS: OCR-miss continuity, recentered cursor, atomic click coordinates, '
          'release after blur, >=20 Enter pulses per 250 ms, loss of turn/focus')


if __name__ == '__main__':
    asyncio.run(run(*sys.argv[1:]))
