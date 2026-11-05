# SagatVNC Viewer tests
# Copyright (C) 2026 SagatVNC Viewer contributors
# SPDX-License-Identifier: GPL-3.0-or-later

import random
import unittest

from sagatvnc.rfb.framebuffer import FrameBuffer
from sagatvnc.viewer.scaler import Scaler, fit_size


class FitTest(unittest.TestCase):
    def test_fit_size_keeps_aspect(self):
        self.assertEqual(fit_size(1920, 1080, 1280, 1024), (1280, 720))
        self.assertEqual(fit_size(1920, 1080, 3000, 1080), (1920, 1080))
        self.assertEqual(fit_size(1920, 1080, 3840, 4000), (3840, 2160))
        self.assertEqual(fit_size(100, 100, 0, 0), (100, 100))

    def test_to_source(self):
        s = Scaler()
        s.configure(1920, 1080, 960, 540)
        self.assertEqual(s.to_source(0, 0), (0, 0))
        self.assertEqual(s.to_source(480.5, 270.2), (961, 540))
        self.assertEqual(s.to_source(959.9, 539.9), (1919, 1079))
        self.assertEqual(s.to_source(-5, 9999), (0, 1079))   # clamped

    def test_identity_is_pixel_exact(self):
        fb = FrameBuffer(7, 5)
        fb.image_rect(0, 0, 7, 5, bytes(range(105)))
        s = Scaler()
        s.configure(7, 5, 7, 5)
        self.assertTrue(s.identity)
        self.assertEqual(s.dest_rect(2, 1, 3, 2), (2, 1, 3, 2))
        self.assertEqual(s.render(fb, 0, 0, 7, 5), bytes(range(105)))


@unittest.skipUnless(Scaler.available, "Pillow not installed")
class ResampleTest(unittest.TestCase):
    W, H = 151, 97

    def setUp(self):
        rnd = random.Random(7)
        self.fb = FrameBuffer(self.W, self.H)
        # Random noise is the worst case for seams: maximum overshoot
        self.fb.image_rect(0, 0, self.W, self.H,
                           bytes(rnd.randrange(256)
                                 for _ in range(self.W * self.H * 3)))
        self.rnd = rnd

    def check_seamless(self, dw, dh):
        s = Scaler()
        s.configure(self.W, self.H, dw, dh)
        full = s.render_full(self.fb)
        self.assertEqual(len(full), dw * dh * 3)
        worst = 0
        for _ in range(25):
            x = self.rnd.randrange(self.W)
            y = self.rnd.randrange(self.H)
            w = self.rnd.randint(1, self.W - x)
            h = self.rnd.randint(1, self.H - y)
            X, Y, Ww, Hh = s.dest_rect(x, y, w, h)
            if not Ww or not Hh:
                continue
            patch = s.render(self.fb, X, Y, Ww, Hh)
            for r in range(Hh):
                a = full[((Y + r) * dw + X) * 3:((Y + r) * dw + X + Ww) * 3]
                b = patch[r * Ww * 3:(r + 1) * Ww * 3]
                worst = max(worst, max(abs(i - j) for i, j in zip(a, b)))
        # +-1 from float rounding of sub-pixel boxes; seams would be >> 1
        self.assertLessEqual(worst, 1)

    def test_seamless_when_shrinking(self):
        self.check_seamless(100, 64)
        self.check_seamless(47, 30)

    def test_seamless_when_enlarging(self):
        self.check_seamless(320, 206)
        self.check_seamless(227, 146)

    def test_dest_rect_covers_filter_reach(self):
        # Changing one source pixel must only alter pixels inside dest_rect
        s = Scaler()
        for dw, dh in ((100, 64), (320, 206)):
            s.configure(self.W, self.H, dw, dh)
            before = s.render_full(self.fb)
            x, y = 60, 40
            old = self.fb.get_pixel(x, y)
            self.fb.fill_rect(x, y, 1, 1, bytes(255 - v for v in old))
            after = s.render_full(self.fb)
            self.fb.fill_rect(x, y, 1, 1, old)
            X, Y, Ww, Hh = s.dest_rect(x, y, 1, 1)
            for i in range(dw * dh):
                if before[i * 3:i * 3 + 3] != after[i * 3:i * 3 + 3]:
                    px, py = i % dw, i // dw
                    self.assertTrue(X <= px < X + Ww and Y <= py < Y + Hh,
                                    "pixel %d,%d changed outside %r" %
                                    (px, py, (X, Y, Ww, Hh)))

    def test_filters(self):
        s = Scaler()
        s.configure(self.W, self.H, 100, 64)
        self.assertEqual(s.support, 3.0)         # Lanczos-3 when shrinking
        s.configure(self.W, self.H, 300, 193)
        self.assertEqual(s.support, 2.0)         # bicubic when enlarging


if __name__ == "__main__":
    unittest.main()
