# SagatVNC Viewer - a Python port of the TigerVNC viewer
# Copyright (C) 2026 SagatVNC Viewer contributors
# Based on TigerVNC common/rfb/{Raw,CopyRect,RRE,Hextile,ZRLE,Tight}Decoder.cxx,
# Copyright (C) 2002-2005 RealVNC Ltd., Copyright 2004-2005 Cendio AB,
# Copyright 2009-2022 Pierre Ossman for Cendio AB, and TigerVNC contributors.
#
# This is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
"""Rectangle decoders.

Every decoder assumes the server uses PixelFormat.preferred() (4 byte
pixels, R,G,B,X in memory) and writes RGB into a FrameBuffer.  Decoders
read directly from a Stream; ZRLE and Tight keep zlib state between
rectangles, which is why they are objects rather than functions.
"""

import zlib

from . import encodings as enc
from .pixelformat import rgbx_to_rgb


class DecodeError(Exception):
    pass


class Decoder:
    def decode(self, x, y, w, h, stream, fb):
        raise NotImplementedError

    def reset(self):
        pass


class RawDecoder(Decoder):
    def decode(self, x, y, w, h, stream, fb):
        fb.image_rect(x, y, w, h, rgbx_to_rgb(stream.read(w * h * 4)))


class CopyRectDecoder(Decoder):
    def decode(self, x, y, w, h, stream, fb):
        src_x = stream.u16()
        src_y = stream.u16()
        fb.copy_rect(x, y, w, h, src_x, src_y)


class RREDecoder(Decoder):
    def __init__(self, compact=False):
        self.compact = compact

    def decode(self, x, y, w, h, stream, fb):
        count = stream.u32()
        bg = stream.read(4)[:3]
        fb.fill_rect(x, y, w, h, bg)
        size = 4 + (4 if self.compact else 8)
        data = stream.read(count * size)
        for i in range(count):
            o = i * size
            pix = data[o:o + 3]
            if self.compact:
                sx, sy, sw, sh = data[o + 4:o + 8]
            else:
                sx = int.from_bytes(data[o + 4:o + 6], "big")
                sy = int.from_bytes(data[o + 6:o + 8], "big")
                sw = int.from_bytes(data[o + 8:o + 10], "big")
                sh = int.from_bytes(data[o + 10:o + 12], "big")
            if sx + sw > w or sy + sh > h:
                raise DecodeError("RRE subrect outside of rectangle")
            fb.fill_rect(x + sx, y + sy, sw, sh, pix)


class HextileDecoder(Decoder):
    RAW, BG, FG, ANY_SUBRECTS, SUBRECTS_COLOURED = 1, 2, 4, 8, 16

    def decode(self, x, y, w, h, stream, fb):
        bg = fg = b"\0\0\0"
        for ty in range(y, y + h, 16):
            th = min(16, y + h - ty)
            for tx in range(x, x + w, 16):
                tw = min(16, x + w - tx)
                sub = stream.u8()
                if sub & self.RAW:
                    fb.image_rect(tx, ty, tw, th,
                                  rgbx_to_rgb(stream.read(tw * th * 4)))
                    continue
                if sub & self.BG:
                    bg = stream.read(4)[:3]
                fb.fill_rect(tx, ty, tw, th, bg)
                if sub & self.FG:
                    fg = stream.read(4)[:3]
                if not sub & self.ANY_SUBRECTS:
                    continue
                n = stream.u8()
                coloured = sub & self.SUBRECTS_COLOURED
                data = stream.read(n * (6 if coloured else 2))
                o = 0
                for _ in range(n):
                    if coloured:
                        pix = data[o:o + 3]
                        o += 4
                    else:
                        pix = fg
                    xy, wh = data[o], data[o + 1]
                    o += 2
                    sx, sy = xy >> 4, xy & 15
                    sw, sh = (wh >> 4) + 1, (wh & 15) + 1
                    if sx + sw > tw or sy + sh > th:
                        raise DecodeError("Hextile subrect outside of tile")
                    fb.fill_rect(tx + sx, ty + sy, sw, sh, pix)
                if coloured:
                    # The RFB spec says the foreground is undefined after
                    # coloured subrects; TigerVNC leaves it unchanged.
                    pass


def _unpack_indices(data, pos, w, h, bits):
    """Unpack palette indices packed ``bits`` per pixel, rows byte aligned."""
    rowbytes = (w * bits + 7) // 8
    out = bytearray(w * h)
    mask = (1 << bits) - 1
    per_byte = 8 // bits
    i = 0
    for _ in range(h):
        row = data[pos:pos + rowbytes]
        pos += rowbytes
        n = 0
        for byte in row:
            for k in range(per_byte):
                if n >= w:
                    break
                out[i] = (byte >> (8 - bits * (k + 1))) & mask
                i += 1
                n += 1
    return out, pos


def _palette_to_rgb(indices, palette):
    """Map palette indices to RGB; ``palette`` is a list of 3 byte values."""
    table = [bytes(p) for p in palette]
    if len(table) < 256:
        table += [b"\0\0\0"] * (256 - len(table))
    return b"".join(table[i] for i in indices)


class ZRLEDecoder(Decoder):
    def __init__(self):
        self.reset()

    def reset(self):
        self.zs = zlib.decompressobj()

    def decode(self, x, y, w, h, stream, fb):
        length = stream.u32()
        data = self.zs.decompress(stream.read(length))
        pos = 0
        try:
            for ty in range(y, y + h, 64):
                th = min(64, y + h - ty)
                for tx in range(x, x + w, 64):
                    tw = min(64, x + w - tx)
                    pos = self._tile(data, pos, tx, ty, tw, th, fb)
        except IndexError:
            raise DecodeError("ZRLE data ended unexpectedly")

    @staticmethod
    def _tile(data, pos, tx, ty, tw, th, fb):
        sub = data[pos]
        pos += 1
        rle = sub & 128
        pal_size = sub & 127
        palette = [data[pos + i * 3:pos + i * 3 + 3] for i in range(pal_size)]
        pos += pal_size * 3

        if not rle:
            if pal_size == 0:            # raw cpixels
                n = tw * th * 3
                fb.image_rect(tx, ty, tw, th, data[pos:pos + n])
                return pos + n
            if pal_size == 1:            # solid
                fb.fill_rect(tx, ty, tw, th, palette[0])
                return pos
            bits = 1 if pal_size <= 2 else 2 if pal_size <= 4 else 4
            idx, pos = _unpack_indices(data, pos, tw, th, bits)
            fb.image_rect(tx, ty, tw, th, _palette_to_rgb(idx, palette))
            return pos

        if pal_size == 1:
            raise DecodeError("Invalid ZRLE subencoding 129")

        total = tw * th
        out = bytearray(total * 3)
        i = 0
        if pal_size == 0:                # plain RLE
            while i < total:
                pix = data[pos:pos + 3]
                pos += 3
                run = 1
                while True:
                    b = data[pos]
                    pos += 1
                    run += b
                    if b != 255:
                        break
                if i + run > total:
                    raise DecodeError("ZRLE run exceeds tile")
                out[i * 3:(i + run) * 3] = pix * run
                i += run
        else:                            # palette RLE
            while i < total:
                index = data[pos]
                pos += 1
                run = 1
                if index & 128:
                    while True:
                        b = data[pos]
                        pos += 1
                        run += b
                        if b != 255:
                            break
                    index &= 127
                if index >= pal_size:
                    raise DecodeError("ZRLE palette index out of range")
                if i + run > total:
                    raise DecodeError("ZRLE run exceeds tile")
                out[i * 3:(i + run) * 3] = palette[index] * run
                i += run
        fb.image_rect(tx, ty, tw, th, out)
        return pos


class TightDecoder(Decoder):
    FILL, JPEG, EXPLICIT_FILTER = 0x08, 0x09, 0x04
    FILTER_COPY, FILTER_PALETTE, FILTER_GRADIENT = 0, 1, 2
    MIN_TO_COMPRESS = 12

    def __init__(self):
        self.reset()

    def reset(self):
        self.zs = [zlib.decompressobj() for _ in range(4)]

    @staticmethod
    def _compact_length(stream):
        b = stream.u8()
        n = b & 0x7F
        if b & 0x80:
            b = stream.u8()
            n |= (b & 0x7F) << 7
            if b & 0x80:
                n |= stream.u8() << 14
        return n

    def decode(self, x, y, w, h, stream, fb):
        ctl = stream.u8()
        for i in range(4):
            if ctl & (1 << i):
                self.zs[i] = zlib.decompressobj()
        ctl >>= 4

        if ctl == self.FILL:
            fb.fill_rect(x, y, w, h, stream.read(3))
            return
        if ctl == self.JPEG:
            raise DecodeError("Tight JPEG is not supported by this viewer "
                              "(the server should not send it, since no "
                              "quality level was requested)")
        if ctl > self.JPEG:
            raise DecodeError("Illegal Tight compression received")

        stream_id = ctl & 3
        filt = self.FILTER_COPY
        if ctl & self.EXPLICIT_FILTER:
            filt = stream.u8()

        palette = []
        if filt == self.FILTER_PALETTE:
            n = stream.u8() + 1
            pal = stream.read(n * 3)
            palette = [pal[i * 3:i * 3 + 3] for i in range(n)]
            row = (w + 7) // 8 if n <= 2 else w
        elif filt in (self.FILTER_COPY, self.FILTER_GRADIENT):
            row = w * 3
        else:
            raise DecodeError("Unknown Tight filter code %d" % filt)

        size = row * h
        if size < self.MIN_TO_COMPRESS:
            data = stream.read(size)
        else:
            length = self._compact_length(stream)
            data = self.zs[stream_id].decompress(stream.read(length))
            if len(data) < size:
                raise DecodeError("Tight data ended unexpectedly")

        if filt == self.FILTER_PALETTE:
            if len(palette) <= 2:
                idx, _ = _unpack_indices(data, 0, w, h, 1)
            else:
                idx = data[:size]
            fb.image_rect(x, y, w, h, _palette_to_rgb(idx, palette))
        elif filt == self.FILTER_GRADIENT:
            fb.image_rect(x, y, w, h, self._gradient(data, w, h))
        else:
            fb.image_rect(x, y, w, h, data[:size])

    @staticmethod
    def _gradient(data, w, h):
        stride = w * 3
        out = bytearray(stride * h)
        prev = bytearray(stride)
        for yy in range(h):
            base = yy * stride
            cur = out  # alias for speed
            for c in range(3):
                left = 0
                upleft = 0
                for xx in range(c, stride, 3):
                    up = prev[xx]
                    p = left + up - upleft
                    if p < 0:
                        p = 0
                    elif p > 255:
                        p = 255
                    v = (p + data[base + xx]) & 0xFF
                    cur[base + xx] = v
                    left = v
                    upleft = up
            prev = out[base:base + stride]
        return out


def make_decoders():
    return {
        enc.RAW: RawDecoder(),
        enc.COPYRECT: CopyRectDecoder(),
        enc.RRE: RREDecoder(),
        enc.CORRE: RREDecoder(compact=True),
        enc.HEXTILE: HextileDecoder(),
        enc.ZRLE: ZRLEDecoder(),
        enc.TIGHT: TightDecoder(),
    }
