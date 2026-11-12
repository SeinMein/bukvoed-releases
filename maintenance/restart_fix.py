"""Restart fixes for the packaged 2026.11.08.14 application (Python 3.13)."""


async def lobby_visible(self, image):
    """Confirm the fixed lobby HUD independently of the world camera angle."""
    frame = np.asarray(image.resize((1280, 720), Image.Resampling.BILINEAR).convert('RGB'))

    def colors(x, y, w, h):
        r, g, b = frame[y:y+h, x:x+w].astype('int16').transpose(2, 0, 1)
        return (
            ((r > 155) & (g > 120) & (b < 100)).mean(),
            ((r > 155) & (r > g + 60) & (r > b + 45)).mean(),
            ((b > 135) & (b > r + 30)).mean(),
        )

    # The red close button belongs to a match/menu, not the lobby HUD.
    if colors(1221, 12, 43, 43)[1] > 0.55:
        return False

    yellow, red, _ = colors(20, 430, 93, 73)
    # Training, Inventory and Profile must all agree. The former 0.24 red
    # threshold rejected the actual Training icon (~0.19 in the recordings).
    if (yellow > 0.12 and red > 0.10
            and colors(600, 609, 80, 78)[2] > 0.34
            and colors(735, 609, 78, 78)[0] > 0.26):
        training_gray = cv2.cvtColor(frame[430:503, 20:113], cv2.COLOR_RGB2GRAY)
        if cv2.Laplacian(training_gray, cv2.CV_32F).var() > 120:
            return True

    # Include the entire Training caption; the old crop began below most
    # letters, so its OCR fallback could not recover a failed color check.
    training = await self.text(crop_relative(image, (0, 0.65, 0.11, 0.71), 3), self.ru)
    if 'тренировк' not in ''.join(c for c in training.casefold() if c.isalpha()):
        return False
    footer = await self.text(crop_relative(image, (0.3, 0.82, 0.66, 0.99), 2), self.ru)
    labels = ''.join(c for c in footer.casefold() if c.isalpha())
    return any(label in labels for label in ('инвентар', 'профиль', 'лавка'))


async def restart_at_chair(reader, capture, window, stopped, attempt, diagnostics, blocked):
    """Use the earlier jump/walk/return routine without reversing its camera."""
    # Release the previous game's TextBox before WASD/Space. A click in the
    # sky avoids world objects, player profiles and the fixed lobby buttons.
    hwnd, region = window
    if game_window() != window or not can_type(hwnd, stopped):
        return 'interrupted'
    shot = capture.grab(region)
    image = Image.frombytes('RGB', shot.size, shot.rgb)
    if not await reader.lobby_visible(image):
        return 'interrupted'
    point = W.POINT(region['left'] + int(region['width'] * 0.35),
                    region['top'] + int(region['height'] * 0.14))
    target = user32.WindowFromPoint(point)
    if not target or user32.GetAncestor(target, 2) != hwnd:
        return 'interrupted'
    if not user32.SetCursorPos(point.x, point.y):
        return 'interrupted'
    if game_window() != window or not can_type(hwnd, stopped):
        return 'interrupted'
    down = INPUT(type=0, mi=MOUSEINPUT(0, 0, 0, 2, 0, 0))
    up = INPUT(type=0, mi=MOUSEINPUT(0, 0, 0, 4, 0, 0))
    try:
        if user32.SendInput(1, C.byref(down), C.sizeof(INPUT)) != 1:
            return 'interrupted'
        if stopped.wait(0.05):
            return 'interrupted'
    finally:
        user32.SendInput(1, C.byref(up), C.sizeof(INPUT))
    if stopped.wait(0.12):
        return 'interrupted'
    diagnostics.event('Кресло: снял фокус ввода; начинаю прыжок и отход от стула.')
    press._chair_input = True
    try:
        return await attempt_chair_restart(reader, capture, window, stopped, attempt,
                                           diagnostics, blocked, [])
    finally:
        press._chair_input = False


async def attempt_chair_restart(reader, capture, window, stopped, attempt,
                               diagnostics, blocked, camera_changes):
    # release_patch.py installs the ORIGINAL routine from Git commit 0cdeacd.
    # Its input, timings and camera search are retained byte for byte.
    async def legacy(reader, capture, window, stopped, attempt, diagnostics,
                     steps, replay, reorient):
        raise RuntimeError('The release builder must install the pinned legacy routine')

    return await legacy(reader, capture, window, stopped, attempt, diagnostics,
                        [], None, attempt > 1)


def press(scan, hold, stopped, hwnd):
    # Keep the previous typing/Enter implementation intact. Movement uses
    # virtual-key events as well as a scan value for the game controls.
    def original(scan, hold, stopped, hwnd):
        raise RuntimeError('The release builder must install the original key sender')

    virtual = {17: 0x57, 30: 0x41, 31: 0x53, 32: 0x44, 57: 0x20}
    if scan not in virtual or not getattr(press, '_chair_input', False):
        return original(scan, hold, stopped, hwnd)
    if not can_type(hwnd, stopped):
        return
    key = virtual[scan]
    down = INPUT(type=1, ki=KEYBDINPUT(key, scan, 0, 0, 0))
    up = INPUT(type=1, ki=KEYBDINPUT(key, scan, 2, 0, 0))
    started = time.monotonic()
    try:
        if user32.SendInput(1, C.byref(down), C.sizeof(INPUT)) != 1:
            raise RuntimeError('Windows отклонила движение. Игра и бот должны работать с одинаковыми правами.')
        deadline = started + hold
        while can_type(hwnd, stopped):
            remaining = deadline - time.monotonic()
            if remaining <= 0 or stopped.wait(min(0.02, remaining)):
                break
    finally:
        released = user32.SendInput(1, C.byref(up), C.sizeof(INPUT))
    actual = time.monotonic() - started
    logging.info('MOVE_INPUT vk=%#x scan=%#x hold=%.3f actual=%.3f released=%s',
                 key, scan, hold, actual, released == 1)
    if released != 1:
        raise RuntimeError('Windows не подтвердила отпускание клавиши движения.')
