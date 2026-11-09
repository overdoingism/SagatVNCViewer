# SagatVNC Viewer - a Python port of the TigerVNC viewer
# Copyright (C) 2026 SagatVNC Viewer contributors
#
# This is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
"""Windows-only window tweaks (all no-ops elsewhere)."""

import sys

IACE_CHILDREN = 0x0001
GA_ROOT = 2


def _toplevel_hwnd(widget):
    import ctypes
    user32 = ctypes.windll.user32
    user32.GetAncestor.restype = ctypes.c_void_p
    user32.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    return user32.GetAncestor(ctypes.c_void_p(widget.winfo_id()), GA_ROOT)


def bring_to_front(widget):
    """Make widget's toplevel the foreground window, so typing goes to it.

    Windows refuses SetForegroundWindow to a process that didn't get the
    last input, e.g. when the password dialog appears a few seconds after
    start and the user has clicked elsewhere meanwhile; the dialog then
    only flashes in the taskbar.  Attaching to the foreground thread's
    input for the call is the usual way around that.  Returns True if the
    window is in front afterwards.
    """
    if sys.platform != "win32":
        return False
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.c_void_p]
    user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD,
                                         wintypes.BOOL]
    for name in ("SetForegroundWindow", "BringWindowToTop", "SetFocus"):
        getattr(user32, name).argtypes = [wintypes.HWND]
    hwnd = _toplevel_hwnd(widget)
    if not hwnd:
        return False
    fg = user32.GetForegroundWindow()
    if fg == hwnd:
        return True
    ours = kernel32.GetCurrentThreadId()
    theirs = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    attached = bool(theirs and theirs != ours and
                    user32.AttachThreadInput(theirs, ours, True))
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(theirs, ours, False)
    return user32.GetForegroundWindow() == hwnd


def set_local_ime(widget, enabled):
    """Enable or disable the local input method for widget's window.

    With the local IME off, keystrokes go to the server unchanged (letters,
    not composed text), and Chinese/Japanese/Korean input is done by the
    *remote* computer's IME. No local composition or candidate window
    pops up over the desktop. Returns True if the call succeeded.
    """
    if sys.platform != "win32":
        return False
    import ctypes
    imm32 = ctypes.windll.imm32
    imm32.ImmAssociateContextEx.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                            ctypes.c_uint]
    hwnd = _toplevel_hwnd(widget)
    if not hwnd:
        return False
    if enabled:
        # IACC_DEFAULT (0x0010): restore each window's default context
        return bool(imm32.ImmAssociateContextEx(hwnd, None,
                                                0x0010 | IACE_CHILDREN))
    # hIMC = NULL with IACE_CHILDREN: no input context for the toplevel
    # and every child window (Tk's canvas included)
    return bool(imm32.ImmAssociateContextEx(hwnd, None, IACE_CHILDREN))


WM_NCDESTROY = 0x0082
WM_NCLBUTTONDOWN = 0x00A1
WM_NCLBUTTONDBLCLK = 0x00A3
HTSYSMENU = 3
SUBCLASS_ID = 0x5A47        # any number; ours on the outer window


class IconClick:
    """A left click on the title bar icon calls ``callback()`` instead of
    opening the window menu (Windows only; not in TigerVNC).  Tk has no way
    to put a button in the title bar, so the icon opens the F8 menu.  The
    window menu stays on a right click on the title bar; a double click on
    the icon no longer closes the window.

    Tk recreates the outer window on full screen changes, so call attach()
    on every <Map>; it skips a window it already has."""

    def __init__(self, callback):
        self.callback = callback
        self.hwnds = set()
        self.proc = None

    def attach(self, widget):
        if sys.platform != "win32":
            return False
        import ctypes
        from ctypes import wintypes as wt
        try:
            hwnd = int(widget.wm_frame(), 16)
        except (ValueError, Exception):
            return False
        if not hwnd or hwnd in self.hwnds:
            return False
        if self.proc is None:
            comctl = ctypes.windll.comctl32
            proto = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, wt.UINT,
                                       wt.WPARAM, wt.LPARAM, ctypes.c_size_t,
                                       ctypes.c_size_t)
            comctl.SetWindowSubclass.argtypes = [wt.HWND, proto,
                                                 ctypes.c_size_t,
                                                 ctypes.c_size_t]
            comctl.RemoveWindowSubclass.argtypes = [wt.HWND, proto,
                                                    ctypes.c_size_t]
            comctl.DefSubclassProc.restype = ctypes.c_ssize_t
            comctl.DefSubclassProc.argtypes = [wt.HWND, wt.UINT, wt.WPARAM,
                                               wt.LPARAM]
            self.comctl = comctl
            self.proc = proto(self._proc)      # kept alive by self
        if not self.comctl.SetWindowSubclass(hwnd, self.proc, SUBCLASS_ID,
                                             0):
            return False
        self.hwnds.add(hwnd)
        return True

    def _proc(self, hwnd, msg, wparam, lparam, uid, ref):
        try:
            if msg in (WM_NCLBUTTONDOWN, WM_NCLBUTTONDBLCLK) and \
                    wparam == HTSYSMENU:
                if msg == WM_NCLBUTTONDOWN:
                    self.callback()     # must only schedule, not block
                return 0
            if msg == WM_NCDESTROY:
                self.comctl.RemoveWindowSubclass(hwnd, self.proc,
                                                 SUBCLASS_ID)
                self.hwnds.discard(hwnd)
        except Exception:
            pass
        return self.comctl.DefSubclassProc(hwnd, msg, wparam, lparam)
