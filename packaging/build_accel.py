# SagatVNC Viewer - native decoder library build
# Copyright (C) 2026 SagatVNC Viewer contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Build the optional Rust decoder library (accel/) and copy it next to
sagatvnc/rfb/accel.py, where the viewer looks for it.

    python packaging/build_accel.py
    python packaging/build_accel.py --target aarch64-apple-darwin \\
        --target x86_64-apple-darwin      # one universal2 library (lipo)

Needs Rust (cargo).  Without the library everything still works, using
the slower pure-Python loops.  setup.py calls build() when it builds a
wheel; on macOS it takes the targets from ARCHFLAGS (targets_from_env).
"""

import argparse
import os
import re
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

# macOS -arch names -> Rust targets
MAC_TARGETS = {"arm64": "aarch64-apple-darwin",
               "x86_64": "x86_64-apple-darwin"}


def library_path():
    """Where the viewer loads the library from."""
    return os.path.join(ROOT, "sagatvnc", "rfb", LIB_NAME)


def targets_from_env():
    """Rust targets to build for, or None for the host.

    SAGATVNC_ACCEL_TARGETS (comma-separated) wins; otherwise, on macOS,
    ARCHFLAGS ("-arch arm64 -arch x86_64", as set by cibuildwheel for
    universal2 wheels) picks the slices."""
    explicit = os.environ.get("SAGATVNC_ACCEL_TARGETS", "").strip()
    if explicit:
        return [t.strip() for t in explicit.split(",") if t.strip()]
    if sys.platform == "darwin":
        archs = re.findall(r"-arch\s+(\S+)", os.environ.get("ARCHFLAGS", ""))
        targets = [MAC_TARGETS[a] for a in archs if a in MAC_TARGETS]
        if targets:
            return targets
    return None


def _cargo_build(cargo, target=None):
    cmd = [cargo, "build", "--release", "--manifest-path",
           os.path.join(CRATE, "Cargo.toml")]
    if target:
        cmd += ["--target", target]
    print(" ".join(cmd))
    if subprocess.call(cmd) != 0:
        return None
    parts = [CRATE, "target"] + ([target] if target else []) + ["release"]
    return os.path.join(*(parts + [LIB_NAME]))


def build(dest=None, targets=None):
    """Build the library and copy it to ``dest`` (default: next to
    sagatvnc/rfb/accel.py); return that path, or None if it can't be
    built.  Several ``targets`` are merged into one file with lipo
    (macOS universal2)."""
    cargo = shutil.which("cargo")
    if not cargo:
        print("cargo not found: building without the native decoders "
              "(install Rust from https://rustup.rs)", file=sys.stderr)
        return None
    if targets and len(targets) > 1 and sys.platform != "darwin":
        print("several targets need lipo (macOS only)", file=sys.stderr)
        return None
    built = [_cargo_build(cargo, t) for t in (targets or [None])]
    if None in built:
        print("cargo build failed: building without the native decoders",
              file=sys.stderr)
        return None
    dest = dest or library_path()
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if len(built) > 1:
        cmd = ["lipo", "-create", "-output", dest] + built
        print(" ".join(cmd))
        if subprocess.call(cmd) != 0:
            print("lipo failed", file=sys.stderr)
            return None
    else:
        shutil.copyfile(built[0], dest)
    print("installed %s" % dest)
    return dest


def rust_sysroot():
    try:
        return subprocess.check_output(["rustc", "--print", "sysroot"],
                                       text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def rust_std_notice():
    """The toolchain's notice for the Rust standard library linked into
    the library (COPYRIGHT-library.html), or None."""
    root = rust_sysroot()
    if not root:
        return None
    path = os.path.join(root, "share", "doc", "rust", "COPYRIGHT-library.html")
    return path if os.path.isfile(path) else None


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--target", action="append",
                   help="Rust target triple (repeat for universal2)")
    p.add_argument("--dest", help="where to put the library")
    args = p.parse_args(argv)
    return 0 if build(args.dest, args.target or targets_from_env()) else 1


if __name__ == "__main__":
    sys.exit(main())
