# SagatVNC Viewer - a Python port of the TigerVNC viewer
# Copyright (C) 2026 SagatVNC Viewer contributors
#
# This is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
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
