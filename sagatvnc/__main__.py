# SagatVNC Viewer - a Python port of the TigerVNC viewer
# Copyright (C) 2026 SagatVNC Viewer contributors
#
# This is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
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


def _show_error(title, message):
    print(message, file=sys.stderr)
    if _WINDOWED:
        try:
            import tkinter as tk
            from tkinter import messagebox
            from .viewer import icon
            icon.set_app_id()
            root = tk.Tk()
            root.withdraw()
            icon.apply(root)
            messagebox.showerror(title, message, parent=root)
            root.destroy()
        except Exception:
            pass


def main(argv=None):
    from . import logfile
    from .viewer import configfile
    from .viewer.parameters import UsageError, parse_args
    try:
        opts = parse_args(sys.argv[1:] if argv is None else argv)
    except UsageError as e:
        _show_error("SagatVNC Viewer", str(e))
        return 2

    # The file may set LogLevel, so its messages wait until the log is open
    notes = []
    if opts.server and not opts.listen and \
            configfile.looks_like_config_file(opts.server):
        path = opts.server
        try:
            # As in TigerVNC, the file overrides the command line, and an
            # empty ServerName means "ask with the connection dialog".
            opts.server = configfile.load(path, opts, log=notes.append)
        except configfile.ConfigFileError as e:
            _show_error("SagatVNC Viewer",
                        "Unable to load the specified configuration "
                        "file:\n\n%s" % e)
            return 1
        opts.config_file = os.path.abspath(path)
    logfile.setup(_WINDOWED, opts.loglevel)
    for note in notes:
        print(note, file=sys.stderr)

    from .viewer.app import main as run_viewer
    try:
        return run_viewer(opts)
    except Exception as e:
        import traceback
        traceback.print_exc()
        where = ("Details were written to %s." % logfile.path
                 if logfile.path else "")
        _show_error("SagatVNC Viewer",
                    "An unexpected error occurred:\n\n%s: %s\n\n%s"
                    % (type(e).__name__, e, where))
        return 1


if __name__ == "__main__":
    sys.exit(main())
