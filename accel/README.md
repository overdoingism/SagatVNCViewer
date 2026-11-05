# accel: optional native decoder loops (Rust)

## What it is
A small Rust library (`cdylib`) with the decoder loops that are
sequential by nature and therefore slow in Python:

| Function           | Ported from (TigerVNC)                          |
|--------------------|-------------------------------------------------|
| `tight_gradient`   | `common/rfb/TightDecoder.cxx` `FilterGradient24` |
| `zrle_rle`         | `common/rfb/ZRLEDecoder.cxx` (RLE tiles)        |
| `hextile_subrects` | `common/rfb/HextileDecoder.cxx` (subrects)      |
| `rre_subrects`     | `common/rfb/RREDecoder.cxx`                     |

`sagatvnc/rfb/accel.py` loads it with ctypes. The protocol parsing stays in
Python. Without the library (not built, failed to load, or
`SAGATVNC_NO_ACCEL=1`), the viewer uses equivalent Python code;
`tests/test_accel.py` checks that both give the same pixels.

```bash
python packaging/build_accel.py                      # build + install
cargo test --release --manifest-path accel/Cargo.toml
```

## Where the code comes from, and its license
- `src/lib.rs` was written for SagatVNC Viewer (with an AI assistant, like
  the rest of this repository). It copies no source code. The logic is a
  port of the TigerVNC decoder functions listed above, the same way the
  Python modules port it.
- Because it is derived from TigerVNC (GPL-2.0-or-later), it is licensed
  under the **GNU General Public License, version 2 or (at your option)
  any later version**, like the rest of this repository (see `../LICENSE`).
  The file header names the TigerVNC copyright holders, like the Python
  files do.
- It has **no crate dependencies**: `Cargo.lock` lists only this crate.
  Nothing from crates.io is compiled in.
- The compiled library links the **Rust standard library**, which is
  dual-licensed MIT OR Apache-2.0 (copyright the Rust Project
  Developers). Both are compatible with GPL-2.0-or-later; we use it under
  the MIT terms. When the library is distributed (for example inside
  `SagatVNCViewer.exe`), its notice has to go with it:
  `packaging/build_exe.py` copies the toolchain's `COPYRIGHT-library.html`
  and the MIT/Apache texts into `dist/licenses/`. See
  `../THIRD_PARTY_NOTICES.md`.

Not affiliated with TigerVNC. Like the rest of this repository, this code
must not be sent to TigerVNC as a patch (see `AGENTS.md`).
