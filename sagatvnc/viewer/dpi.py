# SagatVNC Viewer - a Python port of the TigerVNC viewer
# Copyright (C) 2026 SagatVNC Viewer contributors
#
# This is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
"""Opt out of Windows' bitmap scaling of DPI-unaware windows.

python.exe/Tk do not declare DPI awareness, so on a 125%/150%/200% display
Windows renders the viewer at 96 DPI and stretches the whole window - the
remote desktop ends up blurry and magnified by the DPI factor.  Declaring
per-monitor awareness makes one remote pixel one screen pixel; any
scaling is then done deliberately by viewer/scaler.py.

Must run before the first Tk window is created.
"""

import sys

_done = False


def enable_dpi_awareness():
    global _done
    if _done or sys.platform != "win32":
        return None
    _done = True
    import ctypes
    # Windows 10 1703+: per-monitor v2 (also scales non-client areas)
    try:
        ctx = ctypes.c_void_p(-4)   # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(ctx):
            return "per-monitor-v2"
    except (AttributeError, OSError):
        pass
    # Windows 8.1+: per-monitor
    try:
        if ctypes.windll.shcore.SetProcessDpiAwareness(2) == 0:
            return "per-monitor"
    except (AttributeError, OSError):
        pass
    # Vista+: system DPI aware
    try:
        if ctypes.windll.user32.SetProcessDPIAware():
            return "system"
    except (AttributeError, OSError):
        pass
    return None
