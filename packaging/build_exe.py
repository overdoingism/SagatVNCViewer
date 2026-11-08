# SagatVNC Viewer - Windows executable build
# Copyright (C) 2026 SagatVNC Viewer contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Build dist/SagatVNCViewer.exe with PyInstaller.

    python -m venv .venv
    .venv\\Scripts\\python -m pip install pyinstaller pillow
    .venv\\Scripts\\python packaging\\build_exe.py

The result is a single windowed .exe.  Drop a .tigervnc file on it (or
pass one on the command line) to connect with that file's settings.
"""

import glob
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LICENSES = os.path.join(ROOT, "licenses")


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
    result = subprocess.call(cmd, cwd=ROOT)
    if result == 0:
        collect_licenses(os.path.join(ROOT, "dist", "licenses"), native)
    return result


def collect_licenses(dest, native):
    """Copy the license texts of everything bundled in the exe (see
    THIRD_PARTY_NOTICES.md).  Missing ones are reported, not fatal."""
    os.makedirs(dest, exist_ok=True)
    base = sys.base_prefix
    wanted = [
        ("SagatVNC-LICENSE.txt", [os.path.join(ROOT, "LICENSE")]),
        ("THIRD_PARTY_NOTICES.md",
         [os.path.join(ROOT, "THIRD_PARTY_NOTICES.md")]),
        # The upstream this is ported from (GPL-2.0-or-later)
        ("TigerVNC-LICENCE.txt", [os.path.join(LICENSES,
                                               "TigerVNC-LICENCE.TXT")]),
        ("Python-LICENSE.txt", [os.path.join(base, "LICENSE.txt")]),
        # Python's ssl module: OpenSSL 3 (Apache-2.0, no NOTICE file)
        ("OpenSSL-LICENSE.txt",
         [os.path.join(LICENSES, "OpenSSL-LICENSE.txt")]),
        ("Tcl-Tk-license.terms",
         glob.glob(os.path.join(base, "tcl", "tk*", "license.terms"))),
        ("Pillow-LICENSE.txt",
         glob.glob(os.path.join(sys.prefix, "Lib", "site-packages",
                                "pillow-*.dist-info", "licenses",
                                "LICENSE"))),
        ("PyInstaller-COPYING.txt",
         glob.glob(os.path.join(sys.prefix, "Lib", "site-packages",
                                "pyinstaller-*.dist-info", "licenses",
                                "COPYING.txt"))),
    ]
    if native:
        import build_accel
        notice = build_accel.rust_std_notice()
        wanted += [
            ("Rust-std-COPYRIGHT.html", [notice] if notice else []),
            ("Rust-MIT.txt", [os.path.join(LICENSES, "Rust-MIT.txt")]),
            ("Rust-Apache-2.0.txt",
             [os.path.join(LICENSES, "Rust-Apache-2.0.txt")]),
        ]
    for name, sources in wanted:
        found = [f for f in sources if os.path.isfile(f)]
        if not found:
            print("WARNING: license text for %s not found" % name,
                  file=sys.stderr)
            continue
        shutil.copyfile(found[0], os.path.join(dest, name))
    print("license texts in %s" % dest)


if __name__ == "__main__":
    sys.exit(main())
