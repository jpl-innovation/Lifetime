"""Rebuild assets/jpl_innovation_light.png from the dark-surface original, for the light theme.

The original has chrome-grey "JPL", cyan "INNOVATION" with a dark shadow, and a dark badge with a white monitor.
For a light surface: the grey lettering becomes one solid navy ink, the cyan is deepened for contrast, the shadow
is dropped (it only read as a halo), and the badge is kept exactly as drawn, so its white monitor and wrench
stay visible on its dark disc. Run: python render_logo_light.py
"""
import os
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PyQt6.QtGui import QColor, QGuiApplication, QImage

HERE = Path(__file__).resolve().parent
INK = (20, 33, 61)         # navy, the same family as the app's dark surface
CYAN = (4, 132, 166)       # the logo cyan, deepened to about 4:1 against the light paper (#F3F4F1)


def smooth(value, low, high):
    t = min(1.0, max(0.0, (value - low) / (high - low)))
    return t * t * (3 - 2 * t)


def badge_circle(image):
    """Centre and radius of the round badge: the opaque pixels right of the empty column that separates it from the text."""
    def column_has_ink(x):
        return any(image.pixelColor(x, y).alpha() > 40 for y in range(image.height()))
    x = image.width() - 1
    while x > 0 and not column_has_ink(x):
        x -= 1
    while x > 0 and column_has_ink(x):      # walk left across the badge until the gap before the lettering
        x -= 1
    left = x + 1
    xs, ys = [], []
    for y in range(image.height()):
        for x in range(left, image.width()):
            if image.pixelColor(x, y).alpha() > 40:
                xs.append(x)
                ys.append(y)
    right, top, bottom = max(xs), min(ys), max(ys)
    return (left + right) / 2, (top + bottom) / 2, (right - left) / 2


def main():
    QGuiApplication([])
    source = QImage(str(HERE/'assets/jpl_innovation.png')).convertToFormat(QImage.Format.Format_ARGB32)
    cx, cy, radius = badge_circle(source)
    out = QImage(source.size(), QImage.Format.Format_ARGB32)
    out.fill(QColor(0, 0, 0, 0))
    for y in range(source.height()):
        for x in range(source.width()):
            c = source.pixelColor(x, y)
            if not c.alpha():
                continue
            if (x - cx) ** 2 + (y - cy) ** 2 <= (radius + 3) ** 2:
                out.setPixelColor(x, y, c)          # the badge is kept as drawn
                continue
            luminance = 0.2126 * c.red() + 0.7152 * c.green() + 0.0722 * c.blue()
            ink = CYAN if c.saturation() >= 90 else INK
            alpha = c.alpha() * smooth(luminance, 60, 115) / 255
            if alpha > 0.01:
                out.setPixelColor(x, y, QColor(*ink, round(alpha * 255)))
    out.save(str(HERE/'assets/jpl_innovation_light.png'))
    print('saved assets/jpl_innovation_light.png', out.width(), out.height())


if __name__ == '__main__':
    main()
