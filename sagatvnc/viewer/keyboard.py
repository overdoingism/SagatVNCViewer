# SagatVNC Viewer - a Python port of the TigerVNC viewer
# Copyright (C) 2026 SagatVNC Viewer contributors
# Based on TigerVNC vncviewer/KeyboardWin32.cxx and Viewport.cxx,
# Copyright 2011-2021 Pierre Ossman for Cendio AB, and TigerVNC
# contributors.
#
# This is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
"""Key event translation: Tk events -> (system key code, key code, keysym).

Like TigerVNC, every key is identified by a *system key code* (on Windows
the scan code, with 0x80 for extended keys), so a release always matches
its press even if the keysym changed in between.  The *key code* is what
goes in QEMU extended key events (RFB uses the Windows scan code set).

Tk hides the raw Win32 messages, so the scan code is recovered from the
virtual key (``event.keycode``) and the keysym name, which tells the left
and right modifiers and the keypad apart.

AltGr: Windows reports AltGr as a fake left Ctrl followed by right Alt.
As KeyboardWin32 does, a left Ctrl press is held back for up to 50 ms; if
right Alt follows, the Ctrl is dropped and the Alt is sent as
ISO_Level3_Shift (on layouts that have AltGr).
"""

import sys

from .keysyms import event_to_keysym

IS_WINDOWS = sys.platform == "win32"

XK_Control_L = 0xFFE3
XK_Alt_R = 0xFFEA
XK_ISO_Level3_Shift = 0xFE03
XK_Shift_L = 0xFFE1
XK_Shift_R = 0xFFE2
XK_Print = 0xFF61
XK_Sys_Req = 0xFF15

# Virtual keys
VK_SHIFT, VK_CONTROL, VK_MENU = 0x10, 0x11, 0x12
VK_PAUSE, VK_CAPITAL, VK_NUMLOCK, VK_SCROLL = 0x13, 0x14, 0x90, 0x91
VK_SNAPSHOT, VK_PACKET = 0x2C, 0xE7

# Keys whose virtual key is also produced by the keypad (NumLock off):
# extended only when Tk names them without the KP_ prefix
_NAV_VKS = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E}
# Always extended
_EXTENDED_VKS = {0x5B, 0x5C, 0x5D,            # Win L/R, Apps
                 0x6F,                        # keypad divide
                 VK_NUMLOCK, VK_SNAPSHOT,
                 0xA3, 0xA5,                  # right Ctrl / Alt
                 0xAD, 0xAE, 0xAF, 0xB0, 0xB1, 0xB2, 0xB3,  # media
                 0xA6, 0xA7, 0xA8, 0xA9, 0xAA, 0xAB, 0xAC}  # browser

ALTGR_TIMEOUT_MS = 50          # KeyboardWin32: "very short time (<50ms)"
ALTGR_HOLD_MS = 100            # Fl::add_timeout(0.1, handleAltGrTimeout)


def _map_virtual_key(vk):
    import ctypes
    return ctypes.windll.user32.MapVirtualKeyW(vk, 0)   # MAPVK_VK_TO_VSC


def windows_system_key_code(vk, keysym_name, map_vk=None):
    """Scan code | 0x80-if-extended for a Tk key event on Windows.

    Equivalent to KeyboardWin32's lParam decoding plus fixSystemKeyCode().
    Returns 0 if unknown.
    """
    map_vk = map_vk or _map_virtual_key
    # Left/right modifiers: Tk gives VK_SHIFT/CONTROL/MENU for both
    if vk == VK_SHIFT:
        return 0x36 if keysym_name == "Shift_R" else 0x2A
    if vk == VK_CONTROL:
        return 0x9D if keysym_name == "Control_R" else 0x1D
    if vk == VK_MENU:
        return 0xB8 if keysym_name in ("Alt_R", "ISO_Level3_Shift") \
            else 0x38
    if vk == VK_PAUSE:
        return 0x45            # Pause (translate_system_key_code -> 0xC6)
    if vk == VK_SNAPSHOT:
        return 0x54            # fixSystemKeyCode: PrintScreen
    if vk == VK_PACKET:
        return 0
    scan = (map_vk(vk) or 0) & 0x7F
    if not scan:
        return 0
    extended = vk in _EXTENDED_VKS
    if vk in _NAV_VKS and not keysym_name.startswith("KP_"):
        extended = True
    if vk == 0x0D and keysym_name == "KP_Enter":
        extended = True
    return scan | (0x80 if extended else 0)


def translate_system_key_code(code):
    """KeyboardWin32::translateSystemKeyCode(): scan code -> RFB key code."""
    if code == 0x45:           # Pause conflicts with NumLock
        return 0xC6
    if code == 0xC5:           # NumLock incorrectly has the extended bit
        return 0x45
    return code


def has_altgr():
    """KeyboardWin32::hasAltGr(): does Ctrl+Alt produce characters in the
    current layout?  Cached per layout."""
    if not IS_WINDOWS:
        return False
    import ctypes
    user32 = ctypes.windll.user32
    user32.GetKeyboardLayout.restype = ctypes.c_void_p
    layout = user32.GetKeyboardLayout(0)
    if _altgr_cache.get("layout") == layout:
        return _altgr_cache["value"]
    # Save the keyboard state so we can get things sane again afterwards
    orig = (ctypes.c_ubyte * 256)()
    if not user32.GetKeyboardState(orig):
        return False
    state = (ctypes.c_ubyte * 256)()
    state[VK_CONTROL] = 0x80
    state[VK_MENU] = 0x80
    buf = ctypes.create_unicode_buffer(10)
    found = False
    for vk in range(256):
        if vk == VK_PACKET:
            continue
        ret = user32.ToUnicode(vk, 0, state, buf, 10, 0)
        if ret == 1:
            found = True
            break
        if ret == -1:          # dead key: clear it out again
            while user32.ToUnicode(vk, 0, state, buf, 10, 0) < 0:
                pass
    user32.SetKeyboardState(orig)
    _altgr_cache.update(layout=layout, value=found)
    return found


_altgr_cache = {}


class Keyboard:
    """Turns Tk key events into press/release calls.

    ``press(system_key_code, key_code, keysym)`` and
    ``release(system_key_code)`` are called for real keys; ``after(ms,
    func)`` / ``after_cancel(id)`` schedule the AltGr timeout.
    """

    def __init__(self, press, release, after, after_cancel,
                 altgr_check=has_altgr, windows=IS_WINDOWS):
        self.press = press
        self.release = release
        self.after = after
        self.after_cancel = after_cancel
        self.altgr_check = altgr_check
        self.windows = windows
        self.altgr_armed = False
        self.altgr_time = 0
        self.altgr_job = None
        self.left_shift_down = False
        self.right_shift_down = False

    # -- AltGr (KeyboardWin32::resolveAltGrDetection) --------------------------
    def _altgr_timeout(self):
        self.altgr_job = None
        self.altgr_armed = False
        self.press(0x1D, 0x1D, XK_Control_L)

    def resolve_altgr(self, is_altgr):
        if not self.altgr_armed:
            return
        self.altgr_armed = False
        if self.altgr_job is not None:
            self.after_cancel(self.altgr_job)
            self.altgr_job = None
        if not is_altgr:
            self.press(0x1D, 0x1D, XK_Control_L)

    def mouse_event(self):
        """No mouse event can come in the middle of an AltGr sequence."""
        self.resolve_altgr(False)

    def reset(self):
        """Keyboard::reset() - e.g. on focus loss."""
        if self.altgr_job is not None:
            self.after_cancel(self.altgr_job)
        self.altgr_job = None
        self.altgr_armed = False
        self.left_shift_down = self.right_shift_down = False

    # -- events ---------------------------------------------------------------
    def system_key_code(self, event):
        if self.windows:
            return windows_system_key_code(event.keycode, event.keysym)
        return event.keycode           # X11/macOS: Tk's own key code

    def key_press(self, event):
        code = self.system_key_code(event)
        keysym = event_to_keysym(event.keysym, event.keysym_num, event.char)
        if self.altgr_armed:
            is_alt = code == 0xB8 and \
                (event.time - self.altgr_time) < ALTGR_TIMEOUT_MS
            self.resolve_altgr(is_alt)
        if not code:
            # No scan code (e.g. characters from an IME/VK_PACKET):
            # use a synthetic code so the release can still be matched
            code = 0x20000 + (keysym or 0)
        if self.windows and event.keycode == VK_SNAPSHOT:
            # Tk has no keysym for it (KeyboardWin32's table: Print, or
            # Sys_Req with Alt)
            keysym = XK_Sys_Req if _alt_held() else XK_Print
        if keysym is None:
            return
        if self.windows:
            # Windows sends the same virtual key for both shifts
            if keysym == XK_Shift_L and code == 0x36:
                keysym = XK_Shift_R
            if self.altgr_check():
                if code == 0xB8 and keysym == XK_Alt_R:
                    keysym = XK_ISO_Level3_Shift
                if code == 0x1D and keysym == XK_Control_L:
                    # Possible start of an AltGr sequence
                    self.altgr_armed = True
                    self.altgr_time = event.time
                    self.altgr_job = self.after(ALTGR_HOLD_MS,
                                                self._altgr_timeout)
                    return
        key_code = translate_system_key_code(code) if self.windows and \
            code < 0x100 else 0
        self.press(code, key_code, keysym)
        if code == 0x2A:
            self.left_shift_down = True
        if code == 0x36:
            self.right_shift_down = True

    def key_release(self, event):
        code = self.system_key_code(event)
        if self.altgr_armed:
            self.resolve_altgr(False)
        if not code:
            keysym = event_to_keysym(event.keysym, event.keysym_num,
                                     event.char)
            code = 0x20000 + (keysym or 0)
        self.release(code)
        # Windows won't send a release for one Shift while the other is
        # still held, so release both (KeyboardWin32)
        if self.windows and code in (0x2A, 0x36):
            if self.left_shift_down:
                self.release(0x2A)
            if self.right_shift_down:
                self.release(0x36)
            self.left_shift_down = self.right_shift_down = False


def _alt_held():
    import ctypes
    return bool(ctypes.windll.user32.GetKeyState(VK_MENU) & 0x8000)


# -- lock key LEDs (KeyboardWin32::getLEDState / setLEDState) ----------------
def get_led_state():
    """Local Caps/Num/Scroll Lock as RFB LED bits, or -1 if unknown."""
    if not IS_WINDOWS:
        return -1
    import ctypes
    gks = ctypes.windll.user32.GetKeyState
    state = 0
    if gks(VK_CAPITAL) & 1:
        state |= 4
    if gks(VK_NUMLOCK) & 1:
        state |= 2
    if gks(VK_SCROLL) & 1:
        state |= 1
    return state


def set_led_state(state):
    """Toggle local lock keys to match ``state``.  Returns the names of
    the keys toggled (their Tk events must be ignored)."""
    if not IS_WINDOWS:
        return []
    import ctypes
    from ctypes import wintypes

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                    ("dwExtraInfo", ctypes.c_size_t)]

    class INPUT(ctypes.Structure):
        class _U(ctypes.Union):
            _fields_ = [("ki", KEYBDINPUT), ("_pad", ctypes.c_byte * 32)]
        _anonymous_ = ("u",)
        _fields_ = [("type", wintypes.DWORD), ("u", _U)]

    SCAN_FAKE = 0xAA
    KEYUP, EXTENDED = 0x0002, 0x0001
    gks = ctypes.windll.user32.GetKeyState
    wanted = [(4, VK_CAPITAL, 0, "Caps_Lock"),
              (2, VK_NUMLOCK, EXTENDED, "Num_Lock"),
              (1, VK_SCROLL, 0, "Scroll_Lock")]
    inputs = []
    toggled = []
    for bit, vk, flags, name in wanted:
        if bool(state & bit) != bool(gks(vk) & 1):
            for f in (flags, flags | KEYUP):
                inp = INPUT()
                inp.type = 1                 # INPUT_KEYBOARD
                inp.ki.wVk = vk
                inp.ki.wScan = SCAN_FAKE
                inp.ki.dwFlags = f
                inputs.append(inp)
            toggled.append(name)
    if inputs:
        arr = (INPUT * len(inputs))(*inputs)
        ctypes.windll.user32.SendInput(len(inputs), arr,
                                       ctypes.sizeof(INPUT))
    return toggled
