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
    if (yellow > 0.24 and red > 0.14
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
