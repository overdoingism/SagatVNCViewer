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


    def __eq__(self, other):
        return isinstance(other, PixelFormat) and self.pack() == other.pack()

    def __hash__(self):
        return hash(self.pack())


# TigerVNC's low colour formats (vncviewer/CConn.cxx), chosen by
# LowColorLevel when FullColor is off or AutoSelect sees < 256 kbit/s.
VERY_LOW_COLOUR = PixelFormat(8, 3, False, True, 1, 1, 1, 2, 1, 0)   # 8
LOW_COLOUR = PixelFormat(8, 6, False, True, 3, 3, 3, 4, 2, 0)        # 64
MEDIUM_COLOUR = PixelFormat(8, 8, False, True, 7, 7, 3, 5, 2, 0)     # 256
LOW_COLOUR_LEVELS = (VERY_LOW_COLOUR, LOW_COLOUR, MEDIUM_COLOUR)
LOW_COLOUR_NAMES = ("8 colours", "64 colours", "256 colours")


def rgbx_to_rgb(raw):
    """Convert packed R,G,B,X pixels (our wire format) to packed RGB."""
    n = len(raw) // 4
    out = bytearray(n * 3)
    out[0::3] = raw[0::4]
    out[1::3] = raw[1::4]
    out[2::3] = raw[2::4]
    return out


def _identity(buf):
    return buf


class PixelCodec:
    """Turns wire pixels of one PixelFormat into the framebuffer's RGB.

    Decoders use three kinds of pixel:

    * ``bypp`` byte PIXELs (Raw, Hextile, RRE, palettes)
    * ZRLE CPIXELs, ``cpix`` bytes (3 for our 32 bpp format)
    * Tight TPIXELs, ``tpix`` bytes (always R,G,B when the format is 888)

    Only the formats this viewer requests are supported: the full colour
    PixelFormat.preferred() and the 8 bpp true colour low colour formats.
    """

    def __init__(self, pf):
        self.pf = pf
        if pf == PixelFormat.preferred():
            self.bypp = 4
            self.to_rgb = rgbx_to_rgb
            self.cpix, self.cpix_to_rgb = 3, _identity   # R,G,B
            self.tpix, self.tpix_to_rgb = 3, _identity   # R,G,B
        elif pf.true_colour and pf.bpp == 8:
            self.bypp = 1
            tables = []
            for mx, sh in ((pf.red_max, pf.red_shift),
                           (pf.green_max, pf.green_shift),
                           (pf.blue_max, pf.blue_shift)):
                tables.append(bytes(((p >> sh) & mx) * 255 // mx
                                    if mx else 0 for p in range(256)))
            rt, gt, bt = tables

            def to_rgb(buf):
                buf = bytes(buf)
                out = bytearray(len(buf) * 3)
                out[0::3] = buf.translate(rt)
                out[1::3] = buf.translate(gt)
                out[2::3] = buf.translate(bt)
                return out
            self.to_rgb = to_rgb
            self.cpix, self.cpix_to_rgb = 1, to_rgb
            self.tpix, self.tpix_to_rgb = 1, to_rgb
        else:
            raise ValueError("Unsupported pixel format: %s" % pf)

    def pixel(self, raw):
        """One PIXEL (bypp bytes) -> 3 byte RGB."""
        return bytes(self.to_rgb(raw[:self.bypp]))
