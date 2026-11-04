# SagatVNC Viewer - a Python port of the TigerVNC viewer
# Copyright (C) 2026 SagatVNC Viewer contributors
# Based on TigerVNC common/rfb/PixelFormat.cxx,
# Copyright (C) 2002-2005 RealVNC Ltd. and TigerVNC contributors.
#
# This is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
"""RFB PIXEL_FORMAT structure."""

import struct
from dataclasses import dataclass

_FMT = ">BBBBHHHBBB3x"


@dataclass
class PixelFormat:
    bpp: int = 32
    depth: int = 24
    big_endian: bool = False
    true_colour: bool = True
    red_max: int = 255
    green_max: int = 255
    blue_max: int = 255
    red_shift: int = 0
    green_shift: int = 8
    blue_shift: int = 16

    @classmethod
    def preferred(cls):
        """The format this viewer always asks for.

        32 bpp little-endian with red in the lowest byte puts each pixel in
        memory as R, G, B, X - so dropping every fourth byte gives the RGB
        the framebuffer stores, and ZRLE's 3 byte CPIXEL is R, G, B too.
        """
        return cls()

    @classmethod
    def unpack(cls, data):
        (bpp, depth, be, tc, rmax, gmax, bmax,
         rs, gs, bs) = struct.unpack(_FMT, data)
        return cls(bpp, depth, bool(be), bool(tc), rmax, gmax, bmax, rs, gs, bs)

    def pack(self):
        return struct.pack(_FMT, self.bpp, self.depth, int(self.big_endian),
                           int(self.true_colour), self.red_max,
                           self.green_max, self.blue_max, self.red_shift,
                           self.green_shift, self.blue_shift)

    def is888(self):
        return (self.true_colour and self.bpp == 32 and self.depth == 24 and
                self.red_max == self.green_max == self.blue_max == 255)

    def __str__(self):
        if not self.true_colour:
            return "depth %d (%dbpp) colour-mapped" % (self.depth, self.bpp)
        return ("depth %d (%dbpp) %s-endian rgb max %d,%d,%d shift %d,%d,%d" %
                (self.depth, self.bpp, "big" if self.big_endian else "little",
                 self.red_max, self.green_max, self.blue_max,
                 self.red_shift, self.green_shift, self.blue_shift))


def rgbx_to_rgb(raw):
    """Convert packed R,G,B,X pixels (our wire format) to packed RGB."""
    n = len(raw) // 4
    out = bytearray(n * 3)
    out[0::3] = raw[0::4]
    out[1::3] = raw[1::4]
    out[2::3] = raw[2::4]
    return out
