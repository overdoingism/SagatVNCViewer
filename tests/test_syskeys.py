# SagatVNC Viewer tests
# Copyright (C) 2026 SagatVNC Viewer contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Which keys the system-key grab gives to Windows and which it sends to
the server (KeyPolicy; the Win32 hook itself needs real key presses)."""

import unittest

from sagatvnc.viewer import parameters, syskeys
from sagatvnc.viewer.syskeys import (BLOCK, PASS, VK_LMENU, VK_LSHIFT,
                                     VK_LCONTROL, VK_LWIN, VK_SNAPSHOT,
                                     VK_TAB, VK_CAPITAL, KeyPolicy)

VK_E, VK_F4, VK_ESCAPE, VK_SPACE, VK_RETURN = 0x45, 0x73, 0x1B, 0x20, 0x0D


class KeyPolicyTest(unittest.TestCase):
    def press(self, p, vk, injected=False):
        return p.decide(vk, False, injected)

    def release(self, p, vk):
        return p.decide(vk, True)

    def test_defaults_keep_print_alt_tab_win_local(self):
        opts = parameters.Options()
        self.assertFalse(opts.passprintscreen)
        self.assertFalse(opts.passalttab)
        self.assertFalse(opts.passwinkey)
        self.assertFalse(opts.passctrlaltenter)
        self.assertTrue(opts.systemkeys)
        self.assertEqual(opts.menukey, "F8")

    def test_everything_else_goes_to_the_server(self):
        p = KeyPolicy()
        for vk in (VK_E, VK_F4, VK_ESCAPE, VK_SPACE, VK_RETURN, VK_LMENU,
                   VK_LCONTROL, VK_LSHIFT):
            self.assertEqual(self.press(p, vk), (BLOCK, []), hex(vk))
            self.assertEqual(self.release(p, vk), (BLOCK, []), hex(vk))

    def test_alt_f4_and_alt_esc_are_swallowed(self):
        p = KeyPolicy()
        self.assertEqual(self.press(p, VK_LMENU)[0], BLOCK)
        self.assertEqual(self.press(p, VK_F4)[0], BLOCK)
        self.assertEqual(self.press(p, VK_ESCAPE)[0], BLOCK)

    def test_language_hotkeys_go_to_the_server(self):
        """Alt+Shift / Ctrl+Shift: Windows never sees them, so the remote
        input language switches, not the local one."""
        for mod in (VK_LMENU, VK_LCONTROL):
            p = KeyPolicy()
            self.assertEqual(self.press(p, mod)[0], BLOCK)
            self.assertEqual(self.press(p, VK_LSHIFT)[0], BLOCK)
            self.assertEqual(self.release(p, VK_LSHIFT)[0], BLOCK)
            self.assertEqual(self.release(p, mod)[0], BLOCK)

    def test_local_alt_tab_replays_alt(self):
        p = KeyPolicy()
        self.assertEqual(self.press(p, VK_LMENU), (BLOCK, []))
        # Windows didn't see Alt: replay it, then let Tab through
        self.assertEqual(self.press(p, VK_TAB), (PASS, [VK_LMENU]))
        self.assertEqual(self.release(p, VK_TAB), (PASS, []))
        self.assertEqual(self.press(p, VK_TAB), (PASS, []))   # no 2nd
        # Its release now belongs to Windows too
        self.assertEqual(self.release(p, VK_LMENU), (PASS, []))
        # The injected replay itself is let through
        self.assertEqual(self.press(p, VK_LMENU, injected=True), (PASS, []))

    def test_remote_alt_tab(self):
        p = KeyPolicy(pass_alt_tab=True)
        self.press(p, VK_LMENU)
        self.assertEqual(self.press(p, VK_TAB), (BLOCK, []))

    def test_ctrl_alt_tab_is_not_alt_tab(self):
        p = KeyPolicy()
        self.press(p, VK_LCONTROL)
        self.press(p, VK_LMENU)
        self.assertEqual(self.press(p, VK_TAB), (BLOCK, []))

    def test_local_win_key_and_combinations(self):
        p = KeyPolicy()
        self.press(p, VK_LSHIFT)
        self.assertEqual(self.press(p, VK_LWIN), (PASS, [VK_LSHIFT]))
        self.assertEqual(self.press(p, VK_E), (PASS, []))       # Win+E
        self.assertEqual(self.release(p, VK_E), (PASS, []))
        self.assertEqual(self.release(p, VK_LWIN), (PASS, []))
        self.assertEqual(self.release(p, VK_LSHIFT), (PASS, []))
        # After the Win key is released, keys go to the server again
        self.assertEqual(self.press(p, VK_E), (BLOCK, []))

    def test_remote_win_key(self):
        p = KeyPolicy(pass_win=True)
        self.assertEqual(self.press(p, VK_LWIN), (BLOCK, []))
        self.assertEqual(self.press(p, VK_E), (BLOCK, []))

    def test_print_screen(self):
        p = KeyPolicy()
        self.press(p, VK_LMENU)
        # Alt+Print Screen (active window shot) needs the Alt replayed
        self.assertEqual(self.press(p, VK_SNAPSHOT), (PASS, [VK_LMENU]))
        p = KeyPolicy(pass_print=True)
        self.assertEqual(self.press(p, VK_SNAPSHOT), (BLOCK, []))

    def test_lock_keys_always_reach_windows(self):
        p = KeyPolicy(pass_win=True)
        self.assertEqual(self.press(p, VK_CAPITAL), (PASS, []))
        self.assertEqual(self.release(p, VK_CAPITAL), (PASS, []))

    def test_keys_held_when_the_grab_starts(self):
        """keyboard_hook: their releases must reach Windows."""
        p = KeyPolicy()
        p.reset(down=[VK_LCONTROL, VK_E])
        self.assertEqual(self.press(p, VK_E), (PASS, []))      # repeat
        self.assertEqual(self.release(p, VK_E), (PASS, []))
        self.assertEqual(self.release(p, VK_LCONTROL), (PASS, []))
        self.assertEqual(self.press(p, VK_E), (BLOCK, []))

    def test_settings_change_takes_effect(self):
        p = KeyPolicy()
        self.assertEqual(self.press(p, VK_LWIN)[0], PASS)
        self.release(p, VK_LWIN)
        p.pass_win = True
        self.assertEqual(self.press(p, VK_LWIN)[0], BLOCK)

    def test_menu_key_vk(self):
        from sagatvnc.viewer.app import menu_key_vk
        self.assertEqual(menu_key_vk("F8"), 0x77)
        self.assertEqual(menu_key_vk("F7"), 0x76)
        self.assertEqual(menu_key_vk("F12"), 0x7B)
        for name in ("", "Pause", "F", "F0", "F25"):
            self.assertIsNone(menu_key_vk(name), name)

    @unittest.skipUnless(syskeys.available(), "Windows only")
    def test_menu_key_closes_the_open_menu(self):
        posted = []

        class User32:
            def PostMessageW(self, *args):
                posted.append(args)

        g = syskeys.SystemKeyGrab(0, KeyPolicy())
        g.u = User32()
        g.menu_vk = 0x77
        menu = {"owner": None}
        g._menu_owner = lambda: menu["owner"]
        # The press that opens the menu goes on to Tk as usual
        self.assertFalse(g._menu_key(0x77, False))
        menu["owner"] = 1234                 # the menu is now open
        self.assertTrue(g._menu_key(0x77, False))   # auto-repeat: eaten
        self.assertEqual(posted, [])
        self.assertFalse(g._menu_key(0x77, True))   # its release
        # A new press closes it, and its release is eaten too
        self.assertTrue(g._menu_key(0x77, False))
        self.assertEqual(posted, [(1234, syskeys.WM_CANCELMODE, 0, 0)])
        menu["owner"] = None
        self.assertTrue(g._menu_key(0x77, True))
        # Other keys are not its business
        self.assertFalse(g._menu_key(0x78, False))

    def test_win32_part_only_on_windows(self):
        import sys
        self.assertEqual(syskeys.available(), sys.platform == "win32")


if __name__ == "__main__":
    unittest.main()
