# SagatVNC Viewer - wheel build with the optional native decoders
# Copyright (C) 2026 SagatVNC Viewer contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""The metadata is in pyproject.toml.  This file only adds the Rust
library (accel/) to wheels.

SAGATVNC_BUILD_ACCEL picks what happens:
  unset / "auto"  build it if cargo is on PATH (a cargo failure is an
                  error then: set 0 to build without it)
  "1"             build it, fail if that's not possible (release builds)
  "0"             don't: a pure-Python wheel (py3-none-any)

A wheel with the library is tagged py3-none-<platform>: it is loaded
with ctypes and uses no Python C API, so one wheel per platform serves
every Python 3 version.
"""

import os
import shutil
import sys

from setuptools import setup
from setuptools.command.build_py import build_py
from setuptools.command.bdist_wheel import bdist_wheel
from setuptools.dist import Distribution

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "packaging"))
import build_accel  # noqa: E402

MODE = os.environ.get("SAGATVNC_BUILD_ACCEL", "auto").strip().lower()
if MODE not in ("auto", "0", "1"):
    sys.exit("SAGATVNC_BUILD_ACCEL must be auto, 0 or 1, not %r" % MODE)
NATIVE = MODE == "1" or (MODE == "auto" and shutil.which("cargo"))


class NativeDistribution(Distribution):
    def has_ext_modules(self):
        # A platform wheel (and platlib) when the library goes in
        return bool(NATIVE)


class BuildPy(build_py):
    def run(self):
        super().run()
        if not NATIVE:
            return
        rfb = os.path.join(self.build_lib, "sagatvnc", "rfb")
        if not build_accel.build(os.path.join(rfb, build_accel.LIB_NAME),
                                 build_accel.targets_from_env()):
            raise SystemExit(
                "building the native decoders failed; set "
                "SAGATVNC_BUILD_ACCEL=0 to build a pure-Python wheel")
        # The Rust standard library is linked in: its notice goes along
        notice = build_accel.rust_std_notice()
        if notice:
            shutil.copyfile(notice, os.path.join(
                rfb, "sagatvnc_accel-Rust-std-COPYRIGHT.html"))


class BdistWheel(bdist_wheel):
    def get_tag(self):
        python, abi, plat = super().get_tag()
        if self.root_is_pure:
            return python, abi, plat
        return "py3", "none", plat


setup(distclass=NativeDistribution,
      cmdclass={"build_py": BuildPy, "bdist_wheel": BdistWheel})
