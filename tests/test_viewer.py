# SagatVNC Viewer tests
# Copyright (C) 2026 SagatVNC Viewer contributors
# SPDX-License-Identifier: GPL-3.0-or-later

import os
import unittest
from types import SimpleNamespace
from unittest import mock

from sagatvnc.viewer import dialogs
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


class MenuStaysOpenTest(unittest.TestCase):
    """The F8 menu comes back after a setting is changed; entries that act
    on the remote computer, end the session or open a window close it."""

    def closes(self, kind, label):
        app = SimpleNamespace(MENU_CLOSERS=ViewerApp.MENU_CLOSERS)
        return ViewerApp._menu_closes(app, kind, label)

    def test_settings_keep_it_open(self):
        for kind, label in (("checkbutton", "Traffic in title bar"),
                            ("radiobutton", "Maximized"),
                            ("checkbutton", "Send clipboard to remote"),
                            ("command", "Refresh screen"),
                            ("command", "Save settings")):
            self.assertFalse(self.closes(kind, label), label)

    def test_actions_close_it(self):
        for kind, label in (("command", "Close menu"),
                            ("command", "Disconnect"),
                            ("command", "Send Ctrl-Alt-Del"),
                            ("command", "Send F8"),
                            ("command", "Open log folder"),
                            ("command", "About SagatVNC Viewer..."),
                            ("command", "Troubleshooting guide (web)...")):
            self.assertTrue(self.closes(kind, label), label)


class UserNameTest(unittest.TestCase):
    """The login dialog offers the user name typed last (UserName)."""

    def setUp(self):
        env = {k: v for k, v in os.environ.items()
               if k not in ("VNC_USERNAME", "VNC_PASSWORD")}
        patcher = mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.offered = []
        self.typed = None
        test = self

        class FakeDialog:
            def __init__(self, parent, need_username, *args, **kw):
                test.offered.append(kw["username"])

            def run(self):
                return test.typed

        patcher = mock.patch.object(dialogs, "AuthDialog", FakeDialog)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.app = SimpleNamespace(
            opts=parse_args([]), saved_credentials=None,
            pending_credentials=None, root=None, host="h:1",
            conn=SimpleNamespace(security=None, security_choices=[]))

    def ask(self, need_username, typed):
        self.typed = typed
        return ViewerApp._ask_credentials(self.app, need_username)

    def test_remembered_and_offered_again(self):
        self.assertEqual(self.ask(True, ("alice", "pw", False, None)),
                         ("alice", "pw"))
        self.assertEqual(self.app.opts.username, "alice")
        self.ask(True, ("bob", "pw", False, None))
        self.assertEqual(self.offered, ["", "alice"])
        self.assertEqual(self.app.opts.username, "bob")

    def test_from_the_command_line(self):
        self.app.opts = parse_args(["-UserName=carol"])
        self.ask(True, ("carol", "pw", False, None))
        self.assertEqual(self.offered, ["carol"])

    def test_password_only_keeps_it(self):
        self.app.opts.username = "alice"
        self.ask(False, ("", "pw", False, None))
        self.assertEqual(self.app.opts.username, "alice")
