"""Rebuild assets/jpl_innovation_light.png from the dark-surface original, for the light theme.

The original is drawn for a dark surface: chrome-grey "JPL", cyan "INNOVATION" with a dark shadow, and a dark disc
holding a white monitor. For a light surface the whole mark is re-inked rather than patched:
  - everything that is white or grey (the "JPL" lettering and the monitor, waves and wrench) becomes one navy ink,
  - the cyan (the "INNOVATION" lettering, the ring, the accents) is deepened for contrast on paper,
  - the dark fill and the shadow become transparent, so the badge is light line art that sits on any light surface.
Edges keep their anti-aliasing because each pixel's opacity follows how far it is from the dark background.
Run: python render_logo_light.py
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


def main():
    QGuiApplication([])
    source = QImage(str(HERE/'assets/jpl_innovation.png')).convertToFormat(QImage.Format.Format_ARGB32)
    out = QImage(source.size(), QImage.Format.Format_ARGB32)
    out.fill(QColor(0, 0, 0, 0))
    for y in range(source.height()):
        for x in range(source.width()):
            c = source.pixelColor(x, y)
            if not c.alpha():
                continue
            luminance = 0.2126 * c.red() + 0.7152 * c.green() + 0.0722 * c.blue()
            cyan = c.saturation() >= 90 and luminance > 50
            # Cyan strokes are mid-bright on the dark fill, white strokes are bright: each reaches full ink at its own level.
            alpha = c.alpha() * smooth(luminance, 55, 115 if cyan else 190) / 255
            if alpha > 0.01:
                out.setPixelColor(x, y, QColor(*(CYAN if cyan else INK), round(alpha * 255)))
    out.save(str(HERE/'assets/jpl_innovation_light.png'))
    print('saved assets/jpl_innovation_light.png', out.width(), out.height())


if __name__ == '__main__':
    main()
