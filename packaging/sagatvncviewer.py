# SagatVNC Viewer - frozen executable entry script
# Copyright (C) 2026 SagatVNC Viewer contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Entry script for PyInstaller (it needs a script, not ``-m``)."""

import sys

from sagatvnc.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
