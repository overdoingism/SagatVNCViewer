# SagatVNC Viewer - a Python port of the TigerVNC viewer
# Copyright (C) 2026 SagatVNC Viewer contributors
#
# This is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
"""Command line entry point: ``python -m sagatvnc [options] [server|file]``.

The server argument may also be a ``.tigervnc`` configuration file, which
is what happens when a file is dropped on SagatVNCViewer.exe.
"""

import os
import sys


def _has_console():
    return sys.stderr is not None and sys.stdout is not None


# True when started without a console (windowed exe, pythonw): errors then
# need a dialog, because nobody would see them on stderr.
_WINDOWED = not _has_console()


def _setup_windowed_logging():
    """A windowed (no console) build has no stderr; log to a file instead."""
    if not _WINDOWED:
        return
    from .viewer.parameters import config_dir
    keep = 5      # sagatvnc.log (newest), sagatvnc.1.log ... sagatvnc.4.log
    try:
        d = config_dir()
        os.makedirs(d, exist_ok=True)
        names = ["sagatvnc.log"] + ["sagatvnc.%d.log" % i
                                    for i in range(1, keep)]
        for older, newer in reversed(list(zip(names[1:], names[:-1]))):
            src = os.path.join(d, newer)
            if os.path.exists(src):
                os.replace(src, os.path.join(d, older))
        f = open(os.path.join(d, names[0]), "w",
                 encoding="utf-8", buffering=1)
    except OSError:
        f = open(os.devnull, "w")
    sys.stderr = f
    sys.stdout = f


def _show_error(title, message):
    print(message, file=sys.stderr)
    if _WINDOWED:
        try:
            import tkinter as tk
            from tkinter import messagebox
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror(title, message, parent=root)
            root.destroy()
        except Exception:
            pass


def main(argv=None):
    _setup_windowed_logging()
    from .viewer import configfile
    from .viewer.parameters import UsageError, parse_args
    try:
        opts = parse_args(sys.argv[1:] if argv is None else argv)
    except UsageError as e:
        _show_error("SagatVNC Viewer", str(e))
        return 2

    if opts.server and not opts.listen and \
            configfile.looks_like_config_file(opts.server):
        path = opts.server
        try:
            # As in TigerVNC, the file overrides the command line, and an
            # empty ServerName means "ask with the connection dialog".
            opts.server = configfile.load(
                path, opts, log=lambda m: print(m, file=sys.stderr))
        except configfile.ConfigFileError as e:
            _show_error("SagatVNC Viewer",
                        "Unable to load the specified configuration "
                        "file:\n\n%s" % e)
            return 1
        opts.config_file = os.path.abspath(path)

    from .viewer.app import main as run_viewer
    try:
        return run_viewer(opts)
    except Exception as e:
        import traceback
        traceback.print_exc()
        _show_error("SagatVNC Viewer",
                    "An unexpected error occurred:\n\n%s: %s\n\nDetails "
                    "were written to the log." % (type(e).__name__, e))
        return 1


if __name__ == "__main__":
    sys.exit(main())
