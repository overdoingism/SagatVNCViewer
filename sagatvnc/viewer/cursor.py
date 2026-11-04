# SagatVNC Viewer - a Python port of the TigerVNC viewer
# Copyright (C) 2026 SagatVNC Viewer contributors
#
# This is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
"""Show the server's cursor shape as the local mouse cursor.

Like TigerVNC, the viewer asks for the Cursor pseudo-encoding.  The
server then stops drawing its cursor into the desktop image, which a
non-DPI-aware server may draw in the wrong place, and sends the shape
instead.  We show that shape as the real local cursor, so it is always
exactly where the clicks go.

Tk can only use custom cursors from files: ``.cur`` files on Windows
(``cursor="@C:/path/x.cur"``).  Elsewhere we fall back to Tk's built-in
cursors.
"""

import atexit
import os
import shutil
import struct
import sys
import tempfile

try:
    from PIL import Image
except ImportError:
    Image = None

CUSTOM_SUPPORTED = sys.platform == "win32"


def make_cur(w, h, hot_x, hot_y, rgba):
    """Build a Windows .cur file (32 bpp BGRA DIB with AND mask)."""
    if not (0 < w <= 256 and 0 < h <= 256):
        raise ValueError("cursor size %dx%d" % (w, h))
    xor = bytearray()
    for y in range(h - 1, -1, -1):              # DIBs are bottom-up
        row = rgba[y * w * 4:(y + 1) * w * 4]
        bgra = bytearray(len(row))
        bgra[0::4] = row[2::4]
        bgra[1::4] = row[1::4]
        bgra[2::4] = row[0::4]
        bgra[3::4] = row[3::4]
        xor += bgra
    mask_row = ((w + 31) // 32) * 4
    and_mask = bytearray()
    for y in range(h - 1, -1, -1):
        bits = bytearray(mask_row)
        for x in range(w):
            if rgba[(y * w + x) * 4 + 3] == 0:   # transparent
                bits[x >> 3] |= 0x80 >> (x & 7)
        and_mask += bits
    header = struct.pack("<IiiHHIIiiII", 40, w, h * 2, 1, 32, 0,
                         len(xor) + len(and_mask), 0, 0, 0, 0)
    image = header + bytes(xor) + bytes(and_mask)
    entry = struct.pack("<BBBBHHII", w % 256, h % 256, 0, 0, hot_x, hot_y,
                        len(image), 6 + 16)
    return struct.pack("<HHH", 0, 2, 1) + entry + image


def scale_cursor(w, h, hot_x, hot_y, rgba, factor):
    """Scale a cursor along with the desktop (needs Pillow)."""
    if Image is None or abs(factor - 1.0) < 0.01:
        return w, h, hot_x, hot_y, rgba
    nw = max(1, min(256, round(w * factor)))
    nh = max(1, min(256, round(h * factor)))
    im = Image.frombytes("RGBA", (w, h), rgba)
    # Premultiplied resampling avoids dark fringes at the edges
    im = im.convert("RGBa").resize((nw, nh), Image.Resampling.LANCZOS)
    im = im.convert("RGBA")
    return (nw, nh, min(nw - 1, round(hot_x * nw / w)),
            min(nh - 1, round(hot_y * nh / h)), im.tobytes())


class CursorManager:
    """Keeps the canvas cursor in step with the server's cursor shape."""

    def __init__(self, widget, always_cursor=False, cursor_type="dot",
                 remote_shape=False):
        self.widget = widget
        # Default: always the local system arrow.  The server is still
        # asked for its cursor shape - that is what stops it drawing a
        # (possibly misplaced) cursor into the desktop image.
        self.remote_shape = remote_shape
        self.always_cursor = always_cursor
        self.fallback = "dot" if cursor_type.lower() == "dot" else "arrow"
        self.shape = None          # (w, h, hot_x, hot_y, rgba) from server
        self.scale = 1.0
        self.serial = 0
        self.dir = None
        self.current_file = None
        self.apply()

    def _tempdir(self):
        if self.dir is None:
            self.dir = tempfile.mkdtemp(prefix="sagatvnc-cursor-")
            atexit.register(shutil.rmtree, self.dir, True)
        return self.dir

    def set_shape(self, w, h, hot_x, hot_y, rgba):
        self.shape = (w, h, hot_x, hot_y, rgba)
        self.apply()

    def set_scale(self, factor):
        if abs(factor - self.scale) > 0.001:
            self.scale = factor
            if self.shape:
                self.apply()

    def apply(self):
        if not self.remote_shape:
            self.widget.configure(cursor="arrow")
            return
        if self.shape is None:
            # No shape from the server (yet): a plain local cursor
            self.widget.configure(cursor=self.fallback)
            return
        w, h, hx, hy, rgba = self.shape
        blank = w == 0 or h == 0 or not any(rgba[3::4])
        if blank:
            # The server hides its cursor (e.g. while typing)
            self.widget.configure(cursor=self.fallback if self.always_cursor
                                  else "none")
            return
        if not CUSTOM_SUPPORTED:
            self.widget.configure(cursor="arrow")
            return
        w, h, hx, hy, rgba = scale_cursor(w, h, hx, hy, rgba, self.scale)
        self.serial += 1
        path = os.path.join(self._tempdir(), "c%d.cur" % self.serial)
        with open(path, "wb") as f:
            f.write(make_cur(w, h, hx, hy, rgba))
        try:
            self.widget.configure(cursor="@" + path.replace("\\", "/"))
        except Exception:
            self.widget.configure(cursor="arrow")
            return
        old, self.current_file = self.current_file, path
        if old:
            try:
                os.remove(old)
            except OSError:
                pass
