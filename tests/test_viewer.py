# SagatVNC Viewer tests
# Copyright (C) 2026 SagatVNC Viewer contributors
# SPDX-License-Identifier: GPL-2.0-or-later

import unittest
from types import SimpleNamespace

from sagatvnc.viewer.app import ViewerApp
from sagatvnc.viewer.parameters import parse_args


class FakeVar:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value


class RemoteDpiTest(unittest.TestCase):
    def pos(self, scale, x, y):
        app = SimpleNamespace(remote_dpi_var=FakeVar(scale))
        return ViewerApp.remote_pointer_pos(app, x, y)

    def test_off_is_identity(self):
        self.assertEqual(self.pos(100, 2559, 1599), (2559, 1599))

    def test_cancels_non_dpi_aware_server(self):
        # Model of winvnc on a 2560x1600 desktop at 150%: it normalises the
        # pointer by the logical size 1707x1067 (GetSystemMetrics in a
        # DPI-unaware process), so the cursor lands at pos * 2560 / 1707.
        logical_w, logical_h = round(2560 / 1.5), round(1600 / 1.5)
        for x, y in ((0, 0), (1280, 800), (2000, 1500), (2559, 1599)):
            sx, sy = self.pos(150, x, y)
            self.assertLess(sx, logical_w)      # reachable: no clamping
            self.assertLess(sy, logical_h)
            landed = (sx * 65535 // (logical_w - 1) * 2560 // 65536,
                      sy * 65535 // (logical_h - 1) * 1600 // 65536)
            self.assertLessEqual(abs(landed[0] - x), 2)
            self.assertLessEqual(abs(landed[1] - y), 2)

    def test_parameters(self):
        o = parse_args(["-RemoteDpiScale=150"])
        self.assertEqual(o.remotedpiscale, 150)
        self.assertFalse(o.localinputmethod)
        self.assertTrue(parse_args(["-LocalInputMethod"]).localinputmethod)


if __name__ == "__main__":
    unittest.main()
