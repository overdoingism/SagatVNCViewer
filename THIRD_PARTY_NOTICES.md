# Third-party notices

SagatVNC Viewer itself is licensed under the GNU General Public License,
version 2 or later (`LICENSE`). It is a port of TigerVNC (GPL-2.0-or-later,
Copyright (C) 1999-2026 the TigerVNC team and others, including RealVNC
Ltd and AT&T Laboratories Cambridge); the source files name the
copyright holders of the upstream code they port.

## In the source repository
| Component | Where | License |
|-----------|-------|---------|
| TigerVNC (reference only, not distributed) | `upstream/tigervnc` (git-ignored) | GPL-2.0-or-later |
| SagatVNC native decoders | `accel/` | GPL-2.0-or-later, see `accel/README.md` |

No third-party source code is included in this repository.

## In `SagatVNCViewer.exe`
The Windows executable bundles the following. `packaging/build_exe.py`
copies their license texts into `dist/licenses/`; distribute that folder
with the exe.

| Component | License | Text in `dist/licenses/` |
|-----------|---------|--------------------------|
| SagatVNC Viewer | GPL-2.0-or-later | `SagatVNC-LICENSE.txt` |
| Python runtime | PSF License 2.0 (and the notices for what it bundles) | `Python-LICENSE.txt` |
| Tcl/Tk 8.6 | Tcl/Tk license (BSD-style) | `Tcl-Tk-license.terms` |
| Pillow, with the libraries in its wheel (libjpeg-turbo, zlib, FreeType, ...) | MIT-CMU, plus each library's license | `Pillow-LICENSE.txt` |
| PyInstaller bootloader | GPL-2.0-or-later with the bootloader exception (allows any license for the bundled program) | `PyInstaller-COPYING.txt` |
| Rust standard library (linked into `sagatvnc_accel.dll`) | MIT OR Apache-2.0 | `Rust-std-COPYRIGHT.html`, `Rust-MIT.txt`, `Rust-Apache-2.0.txt` |

The icon is drawn by `packaging/make_icon.py` (part of SagatVNC Viewer). It
is not TigerVNC's logo.
