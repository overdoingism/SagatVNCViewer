# SagatVNC Viewer - a Python port of the TigerVNC viewer
# Copyright (C) 2026 SagatVNC Viewer contributors
#
# This is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
"""Command line entry point: ``python -m sagatvnc [options] [server]``."""

import sys


def main(argv=None):
    from .viewer.parameters import UsageError, parse_args
    try:
        opts = parse_args(sys.argv[1:] if argv is None else argv)
    except UsageError as e:
        print(e, file=sys.stderr)
        return 2
    from .viewer.app import main as run_viewer
    return run_viewer(opts)


if __name__ == "__main__":
    sys.exit(main())
