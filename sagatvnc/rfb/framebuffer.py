# SagatVNC Viewer - a Python port of the TigerVNC viewer
# Copyright (C) 2026 SagatVNC Viewer contributors
# Based on TigerVNC common/rfb/PixelBuffer.cxx,
# Copyright (C) 2002-2005 RealVNC Ltd. and TigerVNC contributors.
#
# This is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
"""Client side framebuffer, stored as packed 24-bit RGB rows.

RGB was chosen because it is what Tk's PPM loader wants, and because the
Tight and ZRLE compact pixel formats are RGB as well for the pixel format
this viewer asks the server for (see PixelFormat.preferred()).
"""

import threading


class FrameBuffer:
    BPP = 3

    def __init__(self, width, height):
        self.lock = threading.RLock()
        self.resize(width, height)

    def resize(self, width, height):
        with self.lock:
            self.width = width
            self.height = height
            self.stride = width * 3
            self.data = bytearray(self.stride * height)

    def _clip(self, x, y, w, h):
        if x < 0 or y < 0 or w < 0 or h < 0 or \
                x + w > self.width or y + h > self.height:
            raise ValueError("Rectangle %dx%d at %d,%d exceeds framebuffer "
                             "%dx%d" % (w, h, x, y, self.width, self.height))

    def fill_rect(self, x, y, w, h, rgb):
        """Fill with a single 3 byte RGB pixel."""
        if w <= 0 or h <= 0:
            return
        self._clip(x, y, w, h)
        row = bytes(rgb) * w
        data, stride = self.data, self.stride
        off = y * stride + x * 3
        for _ in range(h):
            data[off:off + w * 3] = row
            off += stride

    def image_rect(self, x, y, w, h, pixels):
        """Copy ``w*h*3`` bytes of RGB into the framebuffer."""
        if w <= 0 or h <= 0:
            return
        self._clip(x, y, w, h)
        data, stride = self.data, self.stride
        rowlen = w * 3
        if x == 0 and w == self.width:
            off = y * stride
            data[off:off + rowlen * h] = pixels[:rowlen * h]
            return
        off = y * stride + x * 3
        src = 0
        for _ in range(h):
            data[off:off + rowlen] = pixels[src:src + rowlen]
            off += stride
            src += rowlen

    def copy_rect(self, x, y, w, h, src_x, src_y):
        if w <= 0 or h <= 0:
            return
        self._clip(x, y, w, h)
        self._clip(src_x, src_y, w, h)
        data, stride = self.data, self.stride
        rowlen = w * 3
        rows = range(h) if src_y >= y else range(h - 1, -1, -1)
        for i in rows:
            s = (src_y + i) * stride + src_x * 3
            d = (y + i) * stride + x * 3
            data[d:d + rowlen] = data[s:s + rowlen]

    def get_rect(self, x, y, w, h):
        """Return the RGB bytes of a rectangle (rows packed)."""
        data, stride = self.data, self.stride
        if x == 0 and w == self.width:
            return bytes(data[y * stride:(y + h) * stride])
        rowlen = w * 3
        out = bytearray(rowlen * h)
        off = y * stride + x * 3
        dst = 0
        for _ in range(h):
            out[dst:dst + rowlen] = data[off:off + rowlen]
            off += stride
            dst += rowlen
        return bytes(out)

    def get_pixel(self, x, y):
        off = y * self.stride + x * 3
        return bytes(self.data[off:off + 3])
