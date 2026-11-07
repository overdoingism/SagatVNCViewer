# SagatVNC Viewer - a Python port of the TigerVNC viewer
# Copyright (C) 2026 SagatVNC Viewer contributors
# Based on TigerVNC vncviewer/win32.cxx (win32_enable_lowlevel_keyboard,
# keyboard_hook, message_hook) and DesktopWindow.cxx (grabKeyboard),
# Copyright 2011 Pierre Ossman for Cendio AB, and TigerVNC contributors.
#
# This is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
"""Send system keys (Alt+Esc, Alt+F4, input-language hotkeys, ...) to the
server instead of letting Windows act on them.

As upstream, a low-level keyboard hook (WH_KEYBOARD_LL, on its own
thread so it answers quickly) swallows key events while the desktop has
focus and posts them to the viewer window as ordinary WM_KEYDOWN/UP, and a
WH_GETMESSAGE hook on the GUI thread keeps the thread's keyboard state
right, since Windows stops updating it for swallowed keys.

Differences from upstream (SagatVNC):

* Upstream grabs only in full screen (FullscreenSystemKeys) or on a
  shortcut; here the grab is on whenever the desktop has focus
  (``SystemKeys``), because the owner wants everything but Ctrl+Alt+Del to
  reach the server.
* Per-key choices (``KeyPolicy``): Print Screen, Alt+Tab and the Windows
  key can stay local.  A local combination needs Windows to see its
  modifiers, which we swallowed; they are replayed with SendInput just
  before the key is let through (and their releases then go to Windows).
* System keys are posted as WM_KEYDOWN, not WM_SYSKEYDOWN, so that the
  window's DefWindowProc doesn't act on them (Alt+F4 would close the
  viewer, Alt+Space open the window menu, F10 enter menu mode).

Ctrl+Alt+Del never reaches any hook, and Windows handles Win+L itself:
those always act locally (use F8 > Send Ctrl-Alt-Del).

The hook also closes the F8 menu when the menu key is pressed again (a
Windows popup menu runs its own loop and never shows Tk that key), so it
runs while the desktop has focus even when ``swallow`` is off.
"""

import sys
import threading

# Virtual keys
VK_TAB, VK_SHIFT, VK_CONTROL, VK_MENU = 0x09, 0x10, 0x11, 0x12
VK_CAPITAL, VK_NUMLOCK, VK_SCROLL = 0x14, 0x90, 0x91
VK_SNAPSHOT = 0x2C
VK_LWIN, VK_RWIN = 0x5B, 0x5C
VK_LSHIFT, VK_RSHIFT, VK_LCONTROL, VK_RCONTROL, VK_LMENU, VK_RMENU = \
    0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5
MODIFIERS = (VK_LSHIFT, VK_RSHIFT, VK_LCONTROL, VK_RCONTROL, VK_LMENU,
             VK_RMENU)
ALTS = (VK_LMENU, VK_RMENU)
CTRLS = (VK_LCONTROL, VK_RCONTROL)
WINS = (VK_LWIN, VK_RWIN)
LOCKS = (VK_CAPITAL, VK_NUMLOCK, VK_SCROLL)

PASS, BLOCK = "pass", "block"


class KeyPolicy:
    """Decides, per low-level key event, whether Windows gets it (PASS) or
    it goes to the server only (BLOCK).  Pure logic, no Win32 calls.

    ``pass_print`` / ``pass_alt_tab`` / ``pass_win``: send those to the
    server (True) or leave them to Windows (False).
    """

    def __init__(self, pass_print=False, pass_alt_tab=False,
                 pass_win=False):
        self.pass_print = pass_print
        self.pass_alt_tab = pass_alt_tab
        self.pass_win = pass_win
        self.reset()

    def reset(self, down=()):
        """Start a grab.  ``down``: keys already held, whose releases must
        reach Windows (keyboard_hook's kbd_state)."""
        self.route = {vk: PASS for vk in down}   # how each held key went
        self.replayed = set()     # swallowed modifiers replayed to Windows

    def _held(self, vks):
        return any(vk in self.route for vk in vks)

    def _blocked_modifiers(self):
        return [vk for vk in MODIFIERS
                if self.route.get(vk) == BLOCK and vk not in self.replayed]

    def decide(self, vk, up, injected=False):
        """-> (PASS or BLOCK, modifiers to replay to Windows first)."""
        if injected:
            return PASS, []           # ours (replays) or another program's
        if vk in LOCKS:
            # We can't change the global lock state ourselves
            return PASS, []
        if up:
            how = self.route.pop(vk, BLOCK)
            if vk in self.replayed:
                self.replayed.discard(vk)
                return PASS, []
            return how, []
        if vk in self.route:          # auto-repeat: same way as the press
            return self.route[vk], []
        how, replay = BLOCK, []
        local = False
        if vk in WINS:
            local = not self.pass_win
        elif self._held(WINS) and self.route.get(
                VK_LWIN, self.route.get(VK_RWIN)) == PASS:
            local = True              # Win+E etc. with a local Win key
        elif vk == VK_SNAPSHOT:
            local = not self.pass_print
        elif vk == VK_TAB and self._held(ALTS) and not self._held(CTRLS):
            local = not self.pass_alt_tab
        if local:
            how = PASS
            replay = self._blocked_modifiers()
            self.replayed.update(replay)
        self.route[vk] = how
        return how, replay


# ---------------------------------------------------------------------------
# Win32 part
# ---------------------------------------------------------------------------
WH_KEYBOARD_LL, WH_GETMESSAGE = 13, 3
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x100, 0x101, 0x104, \
    0x105
_MSG_NAMES = {WM_KEYDOWN: "WM_KEYDOWN", WM_KEYUP: "WM_KEYUP",
              WM_SYSKEYDOWN: "WM_SYSKEYDOWN", WM_SYSKEYUP: "WM_SYSKEYUP"}
PM_REMOVE = 1
WM_QUIT = 0x12
WM_CANCELMODE = 0x1F
LLKHF_EXTENDED, LLKHF_INJECTED, LLKHF_ALTDOWN, LLKHF_UP = 0x01, 0x10, \
    0x20, 0x80
GUI_INMOVESIZE, GUI_INMENUMODE, GUI_SYSTEMMENUMODE, GUI_POPUPMENUMODE = \
    0x2, 0x4, 0x8, 0x10
KEYEVENTF_EXTENDEDKEY = 0x1
INPUT_KEYBOARD = 1
# Scan codes for replaying modifiers
_MOD_SCAN = {VK_LSHIFT: (0x2A, False), VK_RSHIFT: (0x36, False),
             VK_LCONTROL: (0x1D, False), VK_RCONTROL: (0x1D, True),
             VK_LMENU: (0x38, False), VK_RMENU: (0x38, True)}
# message_hook: low-level vkeys -> the generic ones applications expect
_GENERIC = {VK_LSHIFT: VK_SHIFT, VK_RSHIFT: VK_SHIFT,
            VK_LCONTROL: VK_CONTROL, VK_RCONTROL: VK_CONTROL,
            VK_LMENU: VK_MENU, VK_RMENU: VK_MENU}


def available():
    return sys.platform == "win32"


class SystemKeyGrab:
    """The keyboard grab for one desktop window (Windows only).

    ``window`` is the desktop toplevel's outer HWND (``wm frame``; update
    it with ``set_window`` when Tk recreates it, e.g. for full screen).
    Keys are only swallowed while that window is in the foreground with
    no menu open; ``start()`` / ``stop()`` follow the desktop's keyboard
    focus, like DesktopWindow's grabKeyboard() / ungrabKeyboard().  Both
    run on the GUI thread.
    """

    def __init__(self, window, policy, log=lambda m: None):
        import ctypes
        from ctypes import wintypes
        self.ct, self.wt = ctypes, wintypes
        self.window = window
        self.focus_hwnd = None     # where Tk takes keyboard input
        self.policy = policy
        self.log = log
        self.thread = None
        self.thread_id = None
        self.ll_hook = None
        self.msg_hook = None
        self.gui_thread = None
        self.was_active = False
        self.swallow = True        # send system keys to the server
        self.menu_vk = None        # the F8 menu's key, to close it again
        self.menu_key_down = False
        self.swallow_menu_release = False
        # LogKeys: called with one line per event (from the hook thread
        # too, so it must only queue it)
        self.trace = None
        self.inactive_reason = ""
        self._setup_api()

    # -- ctypes declarations ------------------------------------------------
    def _setup_api(self):
        ct, wt = self.ct, self.wt
        u = ct.WinDLL("user32", use_last_error=True)
        k = ct.WinDLL("kernel32", use_last_error=True)
        self.u, self.k = u, k
        LRESULT = ct.c_ssize_t
        self.HOOKPROC = ct.WINFUNCTYPE(LRESULT, ct.c_int, wt.WPARAM,
                                       wt.LPARAM)

        class KBDLLHOOKSTRUCT(ct.Structure):
            _fields_ = [("vkCode", wt.DWORD), ("scanCode", wt.DWORD),
                        ("flags", wt.DWORD), ("time", wt.DWORD),
                        ("dwExtraInfo", ct.c_size_t)]

        class MSG(ct.Structure):
            _fields_ = [("hwnd", wt.HWND), ("message", wt.UINT),
                        ("wParam", wt.WPARAM), ("lParam", wt.LPARAM),
                        ("time", wt.DWORD), ("pt", wt.POINT)]

        class GUITHREADINFO(ct.Structure):
            _fields_ = [("cbSize", wt.DWORD), ("flags", wt.DWORD),
                        ("hwndActive", wt.HWND), ("hwndFocus", wt.HWND),
                        ("hwndCapture", wt.HWND),
                        ("hwndMenuOwner", wt.HWND),
                        ("hwndMoveSize", wt.HWND), ("hwndCaret", wt.HWND),
                        ("rcCaret", wt.RECT)]

        class KEYBDINPUT(ct.Structure):
            _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD),
                        ("dwFlags", wt.DWORD), ("time", wt.DWORD),
                        ("dwExtraInfo", ct.c_size_t)]

        class INPUT(ct.Structure):
            class _U(ct.Union):
                # MOUSEINPUT is the largest member; pad to its size
                _fields_ = [("ki", KEYBDINPUT),
                            ("pad", ct.c_byte * 32)]
            _anonymous_ = ("u",)
            _fields_ = [("type", wt.DWORD), ("u", _U)]

        self.KBDLLHOOKSTRUCT, self.MSG = KBDLLHOOKSTRUCT, MSG
        self.GUITHREADINFO, self.INPUT = GUITHREADINFO, INPUT
        u.SetWindowsHookExW.restype = ct.c_void_p
        u.SetWindowsHookExW.argtypes = [ct.c_int, self.HOOKPROC,
                                        ct.c_void_p, wt.DWORD]
        u.CallNextHookEx.restype = LRESULT
        u.CallNextHookEx.argtypes = [ct.c_void_p, ct.c_int, wt.WPARAM,
                                     wt.LPARAM]
        u.UnhookWindowsHookEx.argtypes = [ct.c_void_p]
        u.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
        u.PostThreadMessageW.argtypes = [wt.DWORD, wt.UINT, wt.WPARAM,
                                         wt.LPARAM]
        u.GetForegroundWindow.restype = wt.HWND
        u.GetWindowThreadProcessId.restype = wt.DWORD
        u.GetWindowThreadProcessId.argtypes = [wt.HWND, ct.c_void_p]
        u.GetGUIThreadInfo.argtypes = [wt.DWORD, ct.c_void_p]
        u.GetMessageW.argtypes = [ct.c_void_p, wt.HWND, wt.UINT, wt.UINT]
        u.PeekMessageW.argtypes = [ct.c_void_p, wt.HWND, wt.UINT, wt.UINT,
                                   wt.UINT]
        u.GetAsyncKeyState.restype = ct.c_short
        u.GetKeyboardState.argtypes = [ct.c_void_p]
        u.SetKeyboardState.argtypes = [ct.c_void_p]
        u.SendInput.argtypes = [wt.UINT, ct.c_void_p, ct.c_int]
        k.GetModuleHandleW.restype = ct.c_void_p
        k.GetModuleHandleW.argtypes = [wt.LPCWSTR]
        k.GetCurrentThreadId.restype = wt.DWORD
        k.GetTickCount.restype = wt.DWORD

    # -- start / stop (GUI thread) -------------------------------------------
    def set_window(self, window):
        self.window = window

    @property
    def running(self):
        return self.thread is not None

    def start(self):
        if self.thread is not None:
            return
        self.gui_thread = self.k.GetCurrentThreadId()
        self.was_active = False
        # message_hook on our own (the GUI) thread
        self._msg_proc = self.HOOKPROC(self._message_hook)
        self.msg_hook = self.u.SetWindowsHookExW(
            WH_GETMESSAGE, self._msg_proc, None, self.gui_thread)
        if not self.msg_hook:
            self.log("Failure grabbing control of the keyboard "
                     "(WH_GETMESSAGE: %d)" % self.ct.get_last_error())
            return
        ready = threading.Event()
        self.thread = threading.Thread(target=self._keyboard_thread,
                                       args=(ready,), name="keyboard-hook",
                                       daemon=True)
        self.thread.start()
        ready.wait(2)
        if not self.ll_hook:
            self.log("Failure grabbing control of the keyboard")
            self.stop()

    def stop(self):
        if self.thread is not None:
            if self.thread_id:
                self.u.PostThreadMessageW(self.thread_id, WM_QUIT, 0, 0)
            self.thread.join(2)
            self.thread = None
            self.thread_id = None
        if self.msg_hook:
            self.u.UnhookWindowsHookEx(self.msg_hook)
            self.msg_hook = None

    # -- the hook thread ------------------------------------------------------
    def _keyboard_thread(self, ready):
        u = self.u
        msg = self.MSG()
        self.thread_id = self.k.GetCurrentThreadId()
        # Make sure a message queue exists for PostThreadMessage
        u.PeekMessageW(self.ct.byref(msg), None, 0, 0, 0)
        self._ll_proc = self.HOOKPROC(self._keyboard_hook)
        self.ll_hook = u.SetWindowsHookExW(
            WH_KEYBOARD_LL, self._ll_proc,
            self.k.GetModuleHandleW(None), 0)
        ready.set()
        if self.ll_hook:
            while u.GetMessageW(self.ct.byref(msg), None, 0, 0) > 0:
                pass
            u.UnhookWindowsHookEx(self.ll_hook)
        self.ll_hook = None

    def _active(self):
        """Do key presses go to our desktop right now?  (Not to a dialog,
        and no menu is open.)"""
        u = self.u
        fg = u.GetForegroundWindow()
        if not fg or fg != self.window:
            self.inactive_reason = "foreground %s, not ours %s" % (
                fg and hex(fg), self.window and hex(self.window))
            return False
        info = self.GUITHREADINFO()
        info.cbSize = self.ct.sizeof(info)
        if not u.GetGUIThreadInfo(self.gui_thread, self.ct.byref(info)):
            self.inactive_reason = "GetGUIThreadInfo failed"
            return False
        if info.flags & (GUI_INMENUMODE | GUI_SYSTEMMENUMODE |
                         GUI_POPUPMENUMODE | GUI_INMOVESIZE):
            self.inactive_reason = "menu or move/size (flags 0x%x)" % \
                info.flags
            return False
        # Tk keeps the Win32 focus on one window per toplevel and routes
        # keys to the focused widget itself
        self.focus_hwnd = info.hwndFocus
        self.inactive_reason = "" if info.hwndFocus else "no focus window"
        return bool(info.hwndFocus)

    def _foreground_ours(self):
        """Is the foreground window one of ours (the desktop, its menu or
        one of our dialogs)?"""
        fg = self.u.GetForegroundWindow()
        return bool(fg) and \
            self.u.GetWindowThreadProcessId(fg, None) == self.gui_thread

    def _menu_owner(self):
        """The window whose popup menu is open, if one of ours is."""
        u = self.u
        fg = u.GetForegroundWindow()
        if not fg or u.GetWindowThreadProcessId(fg, None) != \
                self.gui_thread:
            return None
        info = self.GUITHREADINFO()
        info.cbSize = self.ct.sizeof(info)
        if not u.GetGUIThreadInfo(self.gui_thread, self.ct.byref(info)):
            return None
        if info.flags & GUI_POPUPMENUMODE:
            return info.hwndMenuOwner
        return None

    def _menu_key(self, vk, up):
        """The menu key while our menu is open: a new press closes the
        menu (WM_CANCELMODE ends menu tracking).  -> swallow it?"""
        if vk != self.menu_vk:
            return False
        was_down = self.menu_key_down
        self.menu_key_down = not up
        if up:
            if self.swallow_menu_release:
                self.swallow_menu_release = False
                return True
            return False
        owner = self._menu_owner()
        if not owner:
            return False
        if not was_down:           # not the auto-repeat of the opening press
            self.u.PostMessageW(owner, WM_CANCELMODE, 0, 0)
            self.swallow_menu_release = True
        return True

    def _held_keys(self):
        gaks = self.u.GetAsyncKeyState
        return [vk for vk in range(1, 256) if gaks(vk) & 0x8000]

    def _keyboard_hook(self, code, wparam, lparam):
        try:
            if code >= 0:
                ks = self.KBDLLHOOKSTRUCT.from_address(lparam)
                up = bool(ks.flags & LLKHF_UP)
                if not ks.flags & LLKHF_INJECTED and \
                        self._menu_key(ks.vkCode, up):
                    self._trace(ks, up, "menu key: closes the menu")
                    return 1
                active = self.swallow and self._active()
                if active and not self.was_active:
                    self.policy.reset(self._held_keys())
                self.was_active = active
                if active:
                    how, replay = self.policy.decide(
                        ks.vkCode, up, bool(ks.flags & LLKHF_INJECTED))
                    if replay:
                        self._replay(replay)
                    if how == BLOCK:
                        posted = self._post(ks, up)
                        self._trace(ks, up, "posted to the viewer" if posted
                                    else "PostMessage FAILED (error %d)" %
                                    self.ct.get_last_error())
                        return 1
                    self._trace(ks, up, "to Windows (local key)%s" % (
                        ", replayed %s" % " ".join("0x%x" % v for v in replay)
                        if replay else ""))
                elif self.trace and self._foreground_ours():
                    # Keys for other programs stay out of the log: the
                    # hook sees them while Tk still thinks we have focus
                    self._trace(ks, up, "to Windows (%s)" % (
                        self.inactive_reason if self.swallow
                        else "system keys off"))
        except Exception as e:             # never break the keyboard
            try:
                self.trace and self.trace("hook error: %r" % e)
            except Exception:
                pass
        return self.u.CallNextHookEx(None, code, wparam, lparam)

    def _trace(self, ks, up, what):
        """LogKeys: one line per low-level event.  ``lag`` is how long
        after the key event the hook ran (Windows gives up on a hook after
        LowLevelHooksTimeout and may remove it)."""
        if self.trace is None:
            return
        lag = (self.k.GetTickCount() - ks.time) & 0xFFFFFFFF
        self.trace("hook  vk 0x%02x scan 0x%02x %s%s%s: %s%s" % (
            ks.vkCode, ks.scanCode, "up  " if up else "down",
            " ext" if ks.flags & LLKHF_EXTENDED else "",
            " injected" if ks.flags & LLKHF_INJECTED else "", what,
            ", hook ran %d ms late" % lag if lag >= 50 else ""))

    def _post(self, ks, up):
        # keyboard_hook: PostMessage(target_wnd, wParam, vkey,
        # scanCode << 16 | flags << 24), but always as WM_KEY* (see the
        # module docstring) and with a repeat count of 1
        lparam = 1 | ((ks.scanCode & 0xFF) << 16) | \
            ((ks.flags & LLKHF_EXTENDED) << 24) | \
            (((ks.flags & LLKHF_ALTDOWN) and 1) << 29)
        if up:
            lparam |= (1 << 30) | (1 << 31)
        if lparam >= 1 << 31 and self.ct.sizeof(self.wt.LPARAM) == 4:
            lparam -= 1 << 32
        return bool(self.u.PostMessageW(self.focus_hwnd,
                                        WM_KEYUP if up else WM_KEYDOWN,
                                        ks.vkCode, lparam))

    def _replay(self, vks):
        """Tell Windows about swallowed modifiers a local combination
        needs (injected, so our hook lets them through)."""
        ct = self.ct
        inputs = (self.INPUT * len(vks))()
        for i, vk in enumerate(vks):
            scan, ext = _MOD_SCAN[vk]
            inputs[i].type = INPUT_KEYBOARD
            inputs[i].ki.wVk = vk
            inputs[i].ki.wScan = scan
            inputs[i].ki.dwFlags = KEYEVENTF_EXTENDEDKEY if ext else 0
        self.u.SendInput(len(vks), inputs, ct.sizeof(self.INPUT))

    # -- message_hook (GUI thread) --------------------------------------------
    def _message_hook(self, code, wparam, lparam):
        try:
            if code >= 0:
                msg = self.MSG.from_address(lparam)
                if msg.message in (WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN,
                                   WM_SYSKEYUP):
                    if self.trace is not None and wparam == PM_REMOVE:
                        self.trace("queue %s vk 0x%02x scan 0x%02x" % (
                            _MSG_NAMES[msg.message], msg.wParam & 0xFF,
                            (msg.lParam >> 16) & 0xFF))
                    self._fix_state(msg)
        except Exception:
            pass
        return self.u.CallNextHookEx(None, code, wparam, lparam)

    def _fix_state(self, msg):
        """Windows stops updating our local keyboard state when we do a
        low-level intercept of events, so we need to do that manually."""
        state = (self.ct.c_ubyte * 256)()
        self.u.GetKeyboardState(state)
        vk = msg.wParam & 0xFF
        down = not (msg.lParam & (1 << 31))
        if down:
            state[vk] |= 0x80
        else:
            state[vk] &= 0x7F
        for generic, pair in ((VK_SHIFT, (VK_LSHIFT, VK_RSHIFT)),
                              (VK_CONTROL, (VK_LCONTROL, VK_RCONTROL)),
                              (VK_MENU, (VK_LMENU, VK_RMENU))):
            if any(state[v] & 0x80 for v in pair):
                state[generic] |= 0x80
            else:
                state[generic] &= 0x7F
        self.u.SetKeyboardState(state)
        # The application code expects the generic modifier vkeys
        if vk in _GENERIC:
            msg.wParam = _GENERIC[vk]
            if vk == VK_RSHIFT:
                # The extended bit is also always missing for right shift
                msg.lParam &= ~(1 << 24)
