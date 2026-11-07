# SagatVNC Viewer - a Python port of the TigerVNC viewer
# Copyright (C) 2026 SagatVNC Viewer contributors
#
# This is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
"""Where the log goes and how much goes into it (``LogLevel``).

Not in TigerVNC, whose viewer logs to stderr (``Log`` parameter).  A
windowed build has no stderr, so its output goes to ``log\\sagatvnc.log``
next to the program, or to the settings folder when that folder can't be
written (an installed copy under Program Files).  Each start begins a new
file and keeps the previous ones as ``sagatvnc.1.log`` ... ``.4.log``.
A console run keeps logging to stderr.

No Tk in here: ``__main__`` sets this up before the GUI exists.
"""

import os
import sys

OFF, NORMAL, DETAILED = 0, 1, 2     # LogLevel values
KEEP = 5            # sagatvnc.log (newest), sagatvnc.1.log ... sagatvnc.4.log
NAME = "sagatvnc.log"

level = NORMAL
path = None         # the log file being written, None for stderr or off
_file = None
_devnull = None
_windowed = False


def clamp(value):
    return min(max(int(value), OFF), DETAILED)


def program_dir():
    """The exe's folder when frozen, else the folder holding the
    ``sagatvnc`` package (the source checkout)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def log_dirs():
    """Folders to try, in order."""
    from .viewer.parameters import config_dir
    return [os.path.join(program_dir(), "log"), config_dir()]


def folder():
    """The folder the log is (or would be) in, for Open log folder."""
    if path:
        return os.path.dirname(path)
    return log_dirs()[0]


def setup(windowed, value):
    """Called once at start, before anything is logged."""
    global _windowed
    _windowed = windowed
    set_level(value)


def set_level(value):
    """Change the level, also while running (F8 > Troubleshooting).
    Without a console, OFF closes the file and anything else opens a new
    one if none is open."""
    global level
    level = clamp(value)
    if not _windowed:
        return
    if level == OFF:
        _close()
    elif _file is None:
        _open()


def _open():
    global _file, path
    for d in log_dirs():
        try:
            os.makedirs(d, exist_ok=True)
            names = [NAME] + ["sagatvnc.%d.log" % i for i in range(1, KEEP)]
            for older, newer in reversed(list(zip(names[1:], names[:-1]))):
                src = os.path.join(d, newer)
                if os.path.exists(src):
                    os.replace(src, os.path.join(d, older))
            p = os.path.join(d, NAME)
            f = open(p, "w", encoding="utf-8", buffering=1)
        except OSError:
            continue
        _file, path = f, p
        sys.stderr = sys.stdout = f
        return
    _close()            # nowhere to write: behave as OFF


def _close():
    global _file, path, _devnull
    f, _file, path = _file, None, None
    if _devnull is None:
        _devnull = open(os.devnull, "w")
    sys.stderr = sys.stdout = _devnull
    if f is not None:
        try:
            f.close()
        except OSError:
            pass
