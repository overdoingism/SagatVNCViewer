# SagatVNC Viewer - a Python port of the TigerVNC viewer
# Copyright (C) 2026 SagatVNC Viewer contributors
#
# This is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
"""Optional native decoder loops (the Rust crate in ``accel/``).

Python keeps parsing the protocol; the library only does the loops that
are sequential by nature (Tight gradient, ZRLE run-length, Hextile and
RRE subrects).  It is loaded with ctypes, so it doesn't depend on the
Python version.  Without it, or with ``SAGATVNC_NO_ACCEL=1`` in the
environment, decoders.py uses its pure-Python code, which gives the same
pixels.  ``python packaging/build_accel.py`` builds it.
"""

import ctypes
import os
import sys

API_VERSION = 1

# Error codes from accel/src/lib.rs
ERR_SHORT, ERR_OUTSIDE, ERR_INDEX, ERR_ARGS = -1, -2, -3, -4

if sys.platform == "win32":
    LIB_NAME = "sagatvnc_accel.dll"
elif sys.platform == "darwin":
    LIB_NAME = "libsagatvnc_accel.dylib"
else:
    LIB_NAME = "libsagatvnc_accel.so"

lib = None
status = "not loaded"


def _load():
    global lib, status
    if os.environ.get("SAGATVNC_NO_ACCEL"):
        status = "disabled by SAGATVNC_NO_ACCEL"
        return
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), LIB_NAME)
    if not os.path.exists(path):
        status = "not built (python packaging/build_accel.py)"
        return
    try:
        candidate = ctypes.CDLL(path)
        candidate.sagatvnc_accel_version.restype = ctypes.c_uint32
        version = candidate.sagatvnc_accel_version()
    except (OSError, AttributeError) as e:
        status = "failed to load: %s" % e
        return
    if version != API_VERSION:
        status = "wrong version %d (want %d), rebuild it" % (version,
                                                             API_VERSION)
        return
    size, p, isz = ctypes.c_size_t, ctypes.c_void_p, ctypes.c_ssize_t
    for name, args in (
            ("tight_gradient", [p, p, size, size]),
            ("zrle_rle", [p, size, size, size, size, size, p]),
            ("hextile_subrects", [p, size, size, size, p, size,
                                  ctypes.c_int, p]),
            ("rre_subrects", [p, size, size, size, p, size,
                              ctypes.c_int])):
        fn = getattr(candidate, name)
        fn.argtypes = args
        fn.restype = isz
    lib = candidate
    status = "loaded (%s)" % path


def _buf(b):
    """A ctypes argument for bytes-like ``b``.  The object itself is
    passed in the call, so it stays alive while the library uses it."""
    if not len(b):
        return None
    if isinstance(b, bytes):
        return b
    if isinstance(b, bytearray):
        return (ctypes.c_char * len(b)).from_buffer(b)
    return bytes(b)


def tight_gradient(data, w, h):
    """FilterGradient24 -> w*h*3 bytes of RGB."""
    if len(data) < w * h * 3:
        raise ValueError("gradient data too short")
    data = bytes(data[:w * h * 3])
    out = bytearray(w * h * 3)
    lib.tight_gradient(_buf(data), _buf(out), w, h)
    return out


def zrle_rle(data, pos, total, cp, pal_size):
    """One ZRLE RLE tile -> (CPIXELs or palette indices, new position or
    a negative error code)."""
    out = bytearray(total * (cp if pal_size == 0 else 1))
    r = lib.zrle_rle(_buf(data), len(data), pos, total, cp, pal_size,
                     _buf(out))
    return out, r


def hextile_subrects(tile, tw, th, bp, data, n, coloured, fg):
    """Paint subrects into ``tile`` (bytearray); ``fg`` bytearray in/out."""
    if len(data) < n * ((bp + 2) if coloured else 2) or \
            len(tile) < tw * th * bp or len(fg) < bp:
        return ERR_SHORT
    return lib.hextile_subrects(_buf(tile), tw, th, bp, _buf(data), n,
                                1 if coloured else 0, _buf(fg))


def rre_subrects(buf, w, h, bp, data, count, compact):
    if len(data) < count * (bp + (4 if compact else 8)) or \
            len(buf) < w * h * bp:
        return ERR_SHORT
    return lib.rre_subrects(_buf(buf), w, h, bp, _buf(data), count,
                            1 if compact else 0)


_load()
