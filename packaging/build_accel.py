# SagatVNC Viewer - native decoder library build
# Copyright (C) 2026 SagatVNC Viewer contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Build the optional Rust decoder library (accel/) and copy it next to
sagatvnc/rfb/accel.py, where the viewer looks for it.

    python packaging/build_accel.py

Needs Rust (cargo).  Without the library everything still works, using
the slower pure-Python loops.
"""

import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CRATE = os.path.join(ROOT, "accel")

# As sagatvnc/rfb/accel.py.  Not imported from there: importing it loads
# the old library, and Windows then won't let us replace the file.
if sys.platform == "win32":
    LIB_NAME = "sagatvnc_accel.dll"
elif sys.platform == "darwin":
    LIB_NAME = "libsagatvnc_accel.dylib"
else:
    LIB_NAME = "libsagatvnc_accel.so"


def library_path():
    """Where the viewer loads the library from."""
    return os.path.join(ROOT, "sagatvnc", "rfb", LIB_NAME)


def build():
    """Build and install the library; return its path, or None."""
    cargo = shutil.which("cargo")
    if not cargo:
        print("cargo not found: building without the native decoders "
              "(install Rust from https://rustup.rs)", file=sys.stderr)
        return None
    cmd = [cargo, "build", "--release", "--manifest-path",
           os.path.join(CRATE, "Cargo.toml")]
    print(" ".join(cmd))
    if subprocess.call(cmd) != 0:
        print("cargo build failed: building without the native decoders",
              file=sys.stderr)
        return None
    built = os.path.join(CRATE, "target", "release", LIB_NAME)
    dest = library_path()
    shutil.copyfile(built, dest)
    print("installed %s" % dest)
    return dest


if __name__ == "__main__":
    sys.exit(0 if build() else 1)
