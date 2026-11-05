# SagatVNC Viewer - Windows executable build
# Copyright (C) 2026 SagatVNC Viewer contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Build dist/SagatVNCViewer.exe with PyInstaller.

    python -m venv .venv
    .venv\\Scripts\\python -m pip install pyinstaller pillow
    .venv\\Scripts\\python packaging\\build_exe.py

The result is a single windowed .exe.  Drop a .tigervnc file on it (or
pass one on the command line) to connect with that file's settings.
"""

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def main():
    try:
        import PIL  # noqa: F401  (bundled for window scaling)
    except ImportError:
        print("Pillow is missing: the exe would have no scaling. "
              "Run: pip install pillow", file=sys.stderr)
        return 1
    build = os.path.join(ROOT, "build")
    os.makedirs(build, exist_ok=True)
    icon = os.path.join(build, "sagatvnc.ico")
    sys.path.insert(0, HERE)
    import make_icon
    make_icon.write_ico(icon)
    import build_accel
    native = build_accel.build()

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--onefile", "--windowed",
        "--name", "SagatVNCViewer",
        "--icon", icon,
        "--paths", ROOT,
        "--distpath", os.path.join(ROOT, "dist"),
        "--workpath", os.path.join(build, "pyinstaller"),
        "--specpath", build,
        # Nothing in the viewer needs these; keeps the exe smaller
        "--exclude-module", "unittest",
        "--exclude-module", "pydoc",
    ]
    if native:
        # Loaded from next to sagatvnc/rfb/accel.py, also when frozen
        cmd += ["--add-binary", native + os.pathsep + "sagatvnc/rfb"]
    cmd.append(os.path.join(HERE, "sagatvncviewer.py"))
    print(" ".join(cmd))
    return subprocess.call(cmd, cwd=ROOT)


if __name__ == "__main__":
    sys.exit(main())
