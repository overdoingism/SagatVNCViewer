# Third-party notices

SagatVNC Viewer itself is licensed under the GNU General Public License,
version 3 or later (`LICENSE`). It is a port of TigerVNC (GPL-2.0-or-later,
Copyright (C) 1999-2026 the TigerVNC team and others, including RealVNC
Ltd and AT&T Laboratories Cambridge), whose "or any later version" option
allows distributing the derived work under GPLv3; the source files name
the copyright holders of the upstream code they port. The parts of
TigerVNC used as references that carry no GPL notice are public domain
(D3DES by Richard Outerbridge) or under the X11/MIT-style Open Group
licence (keysym tables); both are GPLv3-compatible.

## In the source repository
| Component | Where | License |
|-----------|-------|---------|
| TigerVNC (reference only, not distributed) | `upstream/tigervnc` (git-ignored) | GPL-2.0-or-later |
| TigerVNC's licence text | `licenses/TigerVNC-LICENCE.TXT` | GPL-2.0 text (verbatim copy of upstream `LICENCE.TXT` at commit `009df04af8fe`) |
| SagatVNC native decoders | `accel/` | GPL-3.0-or-later, see `accel/README.md` |
| OpenSSL licence text (for the exe) | `licenses/OpenSSL-LICENSE.txt` | Apache-2.0 (verbatim copy) |
| Rust standard library licence texts (for the wheels and the exe) | `licenses/Rust-MIT.txt`, `licenses/Rust-Apache-2.0.txt` | MIT, Apache-2.0 (verbatim copies from the Rust toolchain) |

No third-party source code is included in this repository.

## In the wheels
The platform wheels (`*-py3-none-<platform>.whl`) contain the Rust
decoder library, which links the Rust standard library (MIT OR
Apache-2.0, copyright the Rust Project Developers). The wheels carry
`LICENSE`, this file and `licenses/` in their `.dist-info/licenses/`,
and the toolchain's `COPYRIGHT-library.html` next to the library as
`sagatvnc/rfb/sagatvnc_accel-Rust-std-COPYRIGHT.html`. The pure-Python
wheel and the sdist contain no third-party code.

## In `SagatVNCViewer.exe`: GPLv3 as a whole
The exe combines SagatVNC Viewer with Apache-2.0 code (OpenSSL 3, through
Python's `ssl` module). Apache-2.0 is compatible with GPLv3 but not with
GPLv2, which is one reason the project is GPL-3.0-or-later. The exe as a
whole is distributed under the GNU GPL version 3 or later; its source is
this repository at the matching commit.

## What `SagatVNCViewer.exe` bundles
The Windows executable bundles the following. `packaging/build_exe.py`
copies their license texts into `dist/licenses/`; distribute that folder
with the exe.

| Component | License | Text in `dist/licenses/` |
|-----------|---------|--------------------------|
| SagatVNC Viewer | GPL-3.0-or-later | `SagatVNC-LICENSE.txt` |
| TigerVNC, which it is ported from | GPL-2.0-or-later | `TigerVNC-LICENCE.txt` |
| Python runtime | PSF License 2.0 (and the notices for what it bundles) | `Python-LICENSE.txt` |
| OpenSSL 3 (`libssl-3.dll`, `libcrypto-3.dll`, used by Python's `ssl`) | Apache-2.0 (no NOTICE file) | `OpenSSL-LICENSE.txt` |
| Tcl/Tk 8.6 | Tcl/Tk license (BSD-style) | `Tcl-Tk-license.terms` |
| Pillow, with the libraries in its wheel (libjpeg-turbo, zlib, FreeType, ...) | MIT-CMU, plus each library's license | `Pillow-LICENSE.txt` |
| PyInstaller bootloader | GPL-2.0-or-later with the bootloader exception (allows any license for the bundled program) | `PyInstaller-COPYING.txt` |
| Rust standard library (linked into `sagatvnc_accel.dll`) | MIT OR Apache-2.0 | `Rust-std-COPYRIGHT.html`, `Rust-MIT.txt`, `Rust-Apache-2.0.txt` |

The icon is drawn by `packaging/make_icon.py` (part of SagatVNC Viewer). It
is not TigerVNC's logo.
