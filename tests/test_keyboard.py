# SagatVNC Viewer tests
# Copyright (C) 2026 SagatVNC Viewer contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Keyboard translation ported from TigerVNC KeyboardWin32.cxx."""

import unittest
from types import SimpleNamespace

from sagatvnc.viewer import keyboard as kbd
from sagatvnc.viewer.parameters import parse_server

# MapVirtualKey(vk, MAPVK_VK_TO_VSC) for a US layout
US_SCAN = {0x41: 0x1E, 0x25: 0x4B, 0x2E: 0x53, 0x0D: 0x1C, 0x5B: 0x5B,
           0x90: 0x45, 0x6F: 0x35, 0x70: 0x3B}


def scan(vk, name):
    return kbd.windows_system_key_code(vk, name, US_SCAN.get)


class ScanCodeTest(unittest.TestCase):
    def test_modifiers_left_right(self):
        self.assertEqual(scan(0x10, "Shift_L"), 0x2A)
        self.assertEqual(scan(0x10, "Shift_R"), 0x36)
        self.assertEqual(scan(0x11, "Control_L"), 0x1D)
        self.assertEqual(scan(0x11, "Control_R"), 0x9D)
        self.assertEqual(scan(0x12, "Alt_L"), 0x38)
        self.assertEqual(scan(0x12, "Alt_R"), 0xB8)

    def test_extended_keys(self):
        self.assertEqual(scan(0x41, "a"), 0x1E)
        self.assertEqual(scan(0x25, "Left"), 0xCB)       # arrow: E0 4B
        self.assertEqual(scan(0x25, "KP_Left"), 0x4B)    # keypad 4
        self.assertEqual(scan(0x0D, "Return"), 0x1C)
        self.assertEqual(scan(0x0D, "KP_Enter"), 0x9C)
        self.assertEqual(scan(0x5B, "Win_L"), 0xDB)
        self.assertEqual(scan(0x6F, "KP_Divide"), 0xB5)

    def test_special_cases(self):
        # fixSystemKeyCode / translateSystemKeyCode
        self.assertEqual(scan(0x2C, "Print"), 0x54)
        self.assertEqual(kbd.translate_system_key_code(scan(0x13, "Pause")),
                         0xC6)
        self.assertEqual(kbd.translate_system_key_code(scan(0x90,
                                                            "Num_Lock")),
                         0x45)
        self.assertEqual(kbd.translate_system_key_code(0x1E), 0x1E)


class FakeTk:
    def __init__(self):
        self.jobs = {}
        self.n = 0

    def after(self, ms, func):
        self.n += 1
        self.jobs[self.n] = func
        return self.n

    def after_cancel(self, job):
        self.jobs.pop(job, None)

    def fire(self):
        for job, func in list(self.jobs.items()):
            del self.jobs[job]
            func()


def ev(vk, keysym, num, char="", time=0):
    return SimpleNamespace(keycode=vk, keysym=keysym, keysym_num=num,
                           char=char, time=time)


class KeyboardTest(unittest.TestCase):
    def make(self, altgr=True):
        self.calls = []
        self.tk = FakeTk()
        k = kbd.Keyboard(lambda *a: self.calls.append(("press",) + a),
                         lambda c: self.calls.append(("release", c)),
                         self.tk.after, self.tk.after_cancel,
                         altgr_check=lambda: altgr, windows=True)
        k.system_key_code = lambda e: scan(e.keycode, e.keysym)
        return k

    def test_altgr_merged(self):
        """Fake LCtrl + RAlt within 50 ms -> ISO_Level3_Shift only"""
        k = self.make()
        k.key_press(ev(0x11, "Control_L", 0xFFE3, time=1000))
        self.assertEqual(self.calls, [])               # held back
        k.key_press(ev(0x12, "Alt_R", 0xFFEA, time=1001))
        self.assertEqual(self.calls, [("press", 0xB8, 0xB8,
                                       kbd.XK_ISO_Level3_Shift)])
        k.key_press(ev(0x51, "at", 0x40, "@", time=1010))
        self.assertEqual(self.calls[-1][3], 0x40)
        k.key_release(ev(0x11, "Control_L", 0xFFE3))   # fake ctrl release
        self.assertEqual(self.calls[-1], ("release", 0x1D))  # ignored later

    def test_real_ctrl_after_timeout(self):
        k = self.make()
        k.key_press(ev(0x11, "Control_L", 0xFFE3, time=1000))
        self.tk.fire()                                 # 100 ms passed
        self.assertEqual(self.calls, [("press", 0x1D, 0x1D, 0xFFE3)])

    def test_ctrl_then_other_key(self):
        k = self.make()
        k.key_press(ev(0x11, "Control_L", 0xFFE3, time=1000))
        k.key_press(ev(0x41, "c", 0x63, "\x03", time=1005))
        self.assertEqual([c[1:] for c in self.calls],
                         [(0x1D, 0x1D, 0xFFE3), (0x1E, 0x1E, 0x63)])

    def test_no_altgr_layout_sends_ctrl_now(self):
        k = self.make(altgr=False)
        k.key_press(ev(0x11, "Control_L", 0xFFE3))
        self.assertEqual(self.calls, [("press", 0x1D, 0x1D, 0xFFE3)])

    def test_both_shifts_released(self):
        """Windows doesn't release one Shift while the other is held"""
        k = self.make()
        k.key_press(ev(0x10, "Shift_L", 0xFFE1))
        k.key_press(ev(0x10, "Shift_R", 0xFFE2))
        k.key_release(ev(0x10, "Shift_R", 0xFFE2))
        rel = [c[1] for c in self.calls if c[0] == "release"]
        self.assertEqual(sorted(set(rel)), [0x2A, 0x36])

    def test_mouse_resolves_altgr(self):
        k = self.make()
        k.key_press(ev(0x11, "Control_L", 0xFFE3))
        k.mouse_event()
        self.assertEqual(self.calls, [("press", 0x1D, 0x1D, 0xFFE3)])


class HostParsingTest(unittest.TestCase):
    """network::getHostAndPort()"""

    def test_forms(self):
        # As upstream: "a::b" is host::port, so a bare IPv6 needs more
        # colons than that or brackets
        self.assertEqual(parse_server("2001:db8::1"), ("2001:db8::1", 5900))
        self.assertEqual(parse_server("fe80::1"), ("fe80", 1))
        self.assertEqual(parse_server("[fe80::1]:2"), ("fe80::1", 5902))
        self.assertEqual(parse_server("[fe80::1]::443"), ("fe80::1", 443))
        self.assertEqual(parse_server("host::22"), ("host", 22))
        self.assertEqual(parse_server("host:1"), ("host", 5901))
        self.assertEqual(parse_server("  host:5999 "), ("host", 5999))
        self.assertEqual(parse_server(":3"), ("localhost", 5903))
        with self.assertRaises(ValueError):
            parse_server("[fe80::1")


if __name__ == "__main__":
    unittest.main()
