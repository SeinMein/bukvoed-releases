"""Exercise packaged click dispatch and loss Enter rate without real OS input."""
import asyncio
import ctypes as C
from pathlib import Path
from types import FunctionType, MethodType, SimpleNamespace as NS
import sys
import tempfile
import threading
import time
from unittest.mock import AsyncMock, patch
from PIL import Image
from test_restart import code_in_exe, find


async def run(executable, source_directory, own_recording=None, opponent_recording=None):
    sys.path.insert(0, str(source_directory))
    import bot
    code = code_in_exe(executable)
    env = dict(bot.__dict__)
    env['__builtins__'] = __builtins__
    image = Image.open(Path(source_directory).parents[1] / 'work/first-letter-choice.png').convert('RGB')
    # Each display size starts with an empty cache and a fresh OCR engine.
    for size in ((1280, 720), (1920, 1080), (2560, 1440)):
        fresh_reader = bot.Reader()
        fresh_reader.letter_choices = MethodType(FunctionType(
            find(code, 'Reader.letter_choices'), env, argdefs=(None, None)), fresh_reader)
        assert len(await fresh_reader.letter_choices(image.resize(size), row=())) == 4

    import cv2
    if own_recording:
        video = cv2.VideoCapture(str(own_recording))
        video.set(cv2.CAP_PROP_POS_FRAMES, 350)
        ok, frame = video.read()
        assert ok
        for size in ((1280, 720), (1920, 1080), (2560, 1440)):
            fresh_reader = bot.Reader()
            fresh_reader.letter_choices = MethodType(FunctionType(
                find(code, 'Reader.letter_choices'), env, argdefs=(None, None)), fresh_reader)
            sample = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)).resize(size)
            assert len(await fresh_reader.letter_choices(sample, row=())) == 4
        video.set(cv2.CAP_PROP_POS_FRAMES, 100)
        ok, frame = video.read()
        assert ok
        lobby = FunctionType(find(code, 'Reader.lobby_visible'), env)
        assert await lobby(bot.Reader(), Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)))
        video.release()
    if opponent_recording:
        video = cv2.VideoCapture(str(opponent_recording))
        video.set(cv2.CAP_PROP_POS_FRAMES, 24)
        ok, frame = video.read()
        assert ok
        fresh_reader = bot.Reader()
        fresh_reader.letter_choices = MethodType(FunctionType(
            find(code, 'Reader.letter_choices'), env, argdefs=(None, None)), fresh_reader)
        assert not await fresh_reader.letter_choices(
            Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)), row=())
        video.release()
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

    def stream(reason):
        pressed = []
        allowed = [True]
        focused = [True]
        active_ui = [True]
        notices = []
        stop = threading.Event()

        class Capture:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def grab(self, region):
                assert region['width'] < 100 and region['height'] < 100
                color = (0, 0, 240, 255) if active_ui[0] else (0, 0, 0, 255)
                return bot.np.full((30, 30, 4), color, dtype='uint8')

        current = dict(env)
        current.update(mss=NS(mss=Capture), game_window=lambda:window,
                       can_type=lambda *a: focused[0] and not stop.is_set(),
                       press=lambda *a:pressed.append(time.monotonic()))
        repeat = FunctionType(find(code, 'repeat_loss_enter'), current, argdefs=(False,))
        receiver = NS(loss_enter_allowed=lambda:allowed[0], loss_enter_notify=notices.append)
        repeat(receiver, None, window, stop, False)
        thread = receiver.loss_enter_sender['thread']
        # The OCR/main thread does nothing while the sender continues.
        time.sleep(.15)
        assert len(pressed) >= 6, len(pressed)
        assert receiver.loss_enter_sender['thread'] is thread
        if reason == 'focus': focused[0] = False
        elif reason == 'mode': allowed[0] = False
        elif reason == 'ui': active_ui[0] = False
        else: stop.set()
        thread.join(.3)
        assert not thread.is_alive()
        count = len(pressed)
        time.sleep(.03)
        assert len(pressed) == count and notices
    for reason in ('focus', 'mode', 'ui', 'stop'):
        stream(reason)
    print('PASS: fresh OCR at three resolutions, own/opponent choice, hover lobby, '
          'atomic click/release and independent Enter sender with four stop conditions')


if __name__ == '__main__':
    asyncio.run(run(*sys.argv[1:]))
