# SagatVNC Viewer - a Python port of the TigerVNC viewer
# Copyright (C) 2026 SagatVNC Viewer contributors
#
# This is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
"""Put the SagatVNC icon on every window (not in TigerVNC: its icon comes
from FLTK / the platform build).

The .exe's own icon resource only shows in Explorer.  Tk windows get Tk's
feather unless told otherwise, so ``apply`` sets ``wm iconphoto -default``
on the root, which every later Toplevel (dialogs too) inherits.

On Windows that isn't enough at high DPI: Tk only uses an image whose size
equals SM_CXICON / SM_CXSMICON and otherwise rescales another one, so at
175% the title bar and taskbar showed a stretched 16/32 px icon.
``set_window_icon`` therefore also sends WM_SETICON with icons made at
exactly the window's DPI.
"""

import base64
import sys
import tkinter as tk

from .icon_data import PNGS

APP_USER_MODEL_ID = "SagatVNC.Viewer"

_handles = {}          # (size) -> HICON, kept for the process lifetime


def set_app_id():
    """Give the process its own taskbar identity.

    Without it, ``python -m sagatvnc`` is grouped under (and shown with
    the icon of) python.exe.  Must run before the first window exists.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            APP_USER_MODEL_ID)
    except (AttributeError, OSError):
        pass


def photo(master, size):
    """The icon as a PhotoImage of ``size`` (one of icon_data.PNGS)."""
    return tk.PhotoImage(master=master, data=PNGS[size], format="png")


def best_size(size):
    """The pre-rendered size to draw a ``size`` px icon from: exact, else
    the next larger (shrinking looks better than enlarging)."""
    larger = [s for s in sorted(PNGS) if s >= size]
    return larger[0] if larger else max(PNGS)


def apply(root):
    """Set the icon on ``root`` and, as the default, on all Toplevels."""
    try:
        images = [photo(root, s) for s in sorted(PNGS, reverse=True)
                  if s <= 96]
        root.iconphoto(True, *images)
    except tk.TclError:
        return
    root._sagatvnc_icons = images     # keep the images alive
    # Tk creates (and on full screen changes recreates) the outer window
    # when it maps it, so the exact-size icons go on at <Map>
    def mapped(event):
        if event.widget is event.widget.winfo_toplevel():
            set_window_icon(event.widget)
    root.bind("<Map>", mapped, add="+")
    root.bind_class("Toplevel", "<Map>", mapped, add="+")


def _hicon(size):
    """An HICON of exactly ``size`` px, from the PNG (Windows Vista and
    later take PNG data in icon resources)."""
    if size in _handles:
        return _handles[size]
    import ctypes
    user32 = ctypes.windll.user32
    user32.CreateIconFromResourceEx.restype = ctypes.c_void_p
    user32.CreateIconFromResourceEx.argtypes = [
        ctypes.c_char_p, ctypes.c_uint, ctypes.c_int, ctypes.c_uint,
        ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    data = base64.b64decode("".join(PNGS[best_size(size)]))
    h = user32.CreateIconFromResourceEx(data, len(data), True, 0x00030000,
                                        size, size, 0)
    _handles[size] = h
    return h


def set_window_icon(widget):
    """WM_SETICON big and small at the window's own DPI (Windows only).
    Safe to call again, e.g. after the window moved to another monitor."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        user32 = ctypes.windll.user32
        hwnd = int(widget.wm_frame(), 16)
        try:
            dpi = user32.GetDpiForWindow(hwnd)
            big = user32.GetSystemMetricsForDpi(11, dpi)      # SM_CXICON
            small = user32.GetSystemMetricsForDpi(49, dpi)    # SM_CXSMICON
        except AttributeError:                  # before Windows 10 1607
            big = user32.GetSystemMetrics(11)
            small = user32.GetSystemMetrics(49)
        user32.SendMessageW.restype = ctypes.c_void_p
        user32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                        ctypes.c_void_p, ctypes.c_void_p]
        for which, size in ((1, big), (0, small)):    # ICON_BIG, ICON_SMALL
            h = _hicon(size)
            if h:
                user32.SendMessageW(hwnd, 0x0080, which, h)   # WM_SETICON
    except (AttributeError, OSError, ValueError, tk.TclError):
        pass
