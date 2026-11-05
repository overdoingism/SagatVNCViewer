# SagatVNC Viewer - a Python port of the TigerVNC viewer
# Copyright (C) 2026 SagatVNC Viewer contributors
# Based on TigerVNC common/rfb/{Raw,CopyRect,RRE,Hextile,ZRLE,Tight,JPEG}
# Decoder.cxx, Copyright (C) 2002-2005 RealVNC Ltd., Copyright 2004-2005
# Cendio AB, Copyright 2009-2022 Pierre Ossman for Cendio AB, and TigerVNC
# contributors.
#
# This is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
"""Rectangle decoders.

Decoders read directly from a Stream and write RGB into a FrameBuffer.
Wire pixels are converted with the PixelCodec of the pixel format that is
in effect (full colour or one of the 8 bpp low colour formats).  ZRLE,
Tight and JPEG keep state between rectangles, which is why they are
objects rather than functions.

JPEG (Tight's JPEG subencoding and the standalone JPEG encoding) needs
Pillow; ``JPEG_AVAILABLE`` says whether it can be requested.
"""

import io
import zlib

from . import encodings as enc

try:
    from PIL import Image as _Image
    JPEG_AVAILABLE = True
except ImportError:          # optional dependency
    _Image = None
    JPEG_AVAILABLE = False


class DecodeError(Exception):
    pass


def decode_jpeg(data, w, h):
    """JPEG bytes -> packed RGB of exactly w x h."""
    if _Image is None:
        raise DecodeError("JPEG data received but Pillow is not installed")
    try:
        im = _Image.open(io.BytesIO(data))
        im.load()
    except Exception as e:
        raise DecodeError("Invalid JPEG data: %s" % e)
    if im.size != (w, h):
        raise DecodeError("JPEG is %dx%d, rectangle is %dx%d" %
                          (im.size[0], im.size[1], w, h))
    if im.mode != "RGB":
        im = im.convert("RGB")
    return im.tobytes()


class Decoder:
    def decode(self, x, y, w, h, stream, fb, pc):
        raise NotImplementedError

    def reset(self):
        pass


class RawDecoder(Decoder):
    def decode(self, x, y, w, h, stream, fb, pc):
        fb.image_rect(x, y, w, h, pc.to_rgb(stream.read(w * h * pc.bypp)))


class CopyRectDecoder(Decoder):
    def decode(self, x, y, w, h, stream, fb, pc):
        src_x = stream.u16()
        src_y = stream.u16()
        fb.copy_rect(x, y, w, h, src_x, src_y)


class RREDecoder(Decoder):
    def __init__(self, compact=False):
        self.compact = compact

    def decode(self, x, y, w, h, stream, fb, pc):
        count = stream.u32()
        bp = pc.bypp
        fb.fill_rect(x, y, w, h, pc.pixel(stream.read(bp)))
        size = bp + (4 if self.compact else 8)
        data = stream.read(count * size)
        for i in range(count):
            o = i * size
            pix = pc.pixel(data[o:o + bp])
            o += bp
            if self.compact:
                sx, sy, sw, sh = data[o:o + 4]
            else:
                sx = int.from_bytes(data[o:o + 2], "big")
                sy = int.from_bytes(data[o + 2:o + 4], "big")
                sw = int.from_bytes(data[o + 4:o + 6], "big")
                sh = int.from_bytes(data[o + 6:o + 8], "big")
            if sx + sw > w or sy + sh > h:
                raise DecodeError("RRE subrect outside of rectangle")
            fb.fill_rect(x + sx, y + sy, sw, sh, pix)


class HextileDecoder(Decoder):
    RAW, BG, FG, ANY_SUBRECTS, SUBRECTS_COLOURED = 1, 2, 4, 8, 16

    def decode(self, x, y, w, h, stream, fb, pc):
        bp = pc.bypp
        bg = fg = b"\0\0\0"
        for ty in range(y, y + h, 16):
            th = min(16, y + h - ty)
            for tx in range(x, x + w, 16):
                tw = min(16, x + w - tx)
                sub = stream.u8()
                if sub & self.RAW:
                    fb.image_rect(tx, ty, tw, th,
                                  pc.to_rgb(stream.read(tw * th * bp)))
                    continue
                if sub & self.BG:
                    bg = pc.pixel(stream.read(bp))
                fb.fill_rect(tx, ty, tw, th, bg)
                if sub & self.FG:
                    fg = pc.pixel(stream.read(bp))
                if not sub & self.ANY_SUBRECTS:
                    continue
                n = stream.u8()
                coloured = sub & self.SUBRECTS_COLOURED
                data = stream.read(n * ((bp + 2) if coloured else 2))
                o = 0
                for _ in range(n):
                    if coloured:
                        # HextileDecoder.cxx: a coloured subrect also sets
                        # the foreground that later tiles inherit
                        fg = pc.pixel(data[o:o + bp])
                        o += bp
                    xy, wh = data[o], data[o + 1]
                    o += 2
                    sx, sy = xy >> 4, xy & 15
                    sw, sh = (wh >> 4) + 1, (wh & 15) + 1
                    # Upstream only rejects subrects beyond 16x16; parts
                    # outside a partial edge tile are clipped
                    if sx + sw > 16 or sy + sh > 16:
                        raise DecodeError("Invalid Hextile coordinates")
                    sw, sh = min(sw, tw - sx), min(sh, th - sy)
                    if sw > 0 and sh > 0:
                        fb.fill_rect(tx + sx, ty + sy, sw, sh, fg)


# Pixel work is done with bytes.translate() and extended slice assignment,
# which run in C: Python only loops per row or per palette entry, never
# per pixel.

# _BIT_TABLES[bits][k]: byte -> the k-th ``bits``-bit field, MSB first
_BIT_TABLES = {
    bits: [bytes((b >> (8 - bits * (k + 1))) & ((1 << bits) - 1)
                 for b in range(256)) for k in range(8 // bits)]
    for bits in (1, 2, 4)
}


def _unpack_indices(data, pos, w, h, bits):
    """Unpack palette indices packed ``bits`` per pixel, rows byte aligned.
    Returns (one byte per pixel, new pos); IndexError if data is short."""
    rowbytes = (w * bits + 7) // 8
    end = pos + rowbytes * h
    if end > len(data):
        raise IndexError("packed pixels past the end of the data")
    src = bytes(data[pos:end])
    if bits == 8:
        return src, end
    per = 8 // bits
    full = bytearray(len(src) * per)      # incl. each row's padding bits
    for k, table in enumerate(_BIT_TABLES[bits]):
        full[k::per] = src.translate(table)
    padded = rowbytes * per
    if padded == w:
        return full, end
    out = bytearray(w * h)
    for r in range(h):
        out[r * w:(r + 1) * w] = full[r * padded:r * padded + w]
    return out, end


def _palette_to_rgb(indices, palette):
    """Map palette indices to RGB; ``palette`` is a list of 3 byte values.
    Indices past the palette give black."""
    planes = []
    for c in range(3):
        table = bytearray(256)
        for i, p in enumerate(palette[:256]):
            table[i] = p[c]
        planes.append(bytes(table))
    idx = bytes(indices)
    out = bytearray(len(idx) * 3)
    out[0::3] = idx.translate(planes[0])
    out[1::3] = idx.translate(planes[1])
    out[2::3] = idx.translate(planes[2])
    return out


def _rgb_list(rgb, n):
    return [bytes(rgb[i * 3:i * 3 + 3]) for i in range(n)]


class ZRLEDecoder(Decoder):
    def __init__(self):
        self.reset()

    def reset(self):
        self.zs = zlib.decompressobj()

    def decode(self, x, y, w, h, stream, fb, pc):
        length = stream.u32()
        data = self.zs.decompress(stream.read(length))
        pos = 0
        try:
            for ty in range(y, y + h, 64):
                th = min(64, y + h - ty)
                for tx in range(x, x + w, 64):
                    tw = min(64, x + w - tx)
                    pos = self._tile(data, pos, tx, ty, tw, th, fb, pc)
        except IndexError:
            raise DecodeError("ZRLE data ended unexpectedly")

    @staticmethod
    def _tile(data, pos, tx, ty, tw, th, fb, pc):
        cp = pc.cpix
        sub = data[pos]
        pos += 1
        rle = sub & 128
        pal_size = sub & 127
        palette = _rgb_list(pc.cpix_to_rgb(data[pos:pos + pal_size * cp]),
                            pal_size)
        pos += pal_size * cp

        # As ZRLEDecoder.cxx: a one colour palette is a solid tile whether
        # or not the RLE bit is set (so subencoding 129 is solid too)
        if pal_size == 1:
            fb.fill_rect(tx, ty, tw, th, palette[0])
            return pos

        if not rle:
            if pal_size == 0:            # raw cpixels
                n = tw * th * cp
                fb.image_rect(tx, ty, tw, th,
                              pc.cpix_to_rgb(data[pos:pos + n]))
                return pos + n
            bits = (8 if pal_size > 16 else 4 if pal_size > 4 else
                    2 if pal_size > 2 else 1)
            idx, pos = _unpack_indices(data, pos, tw, th, bits)
            fb.image_rect(tx, ty, tw, th, _palette_to_rgb(idx, palette))
            return pos

        total = tw * th
        out = bytearray(total * 3)
        i = 0
        if pal_size == 0:                # plain RLE
            while i < total:
                pix = bytes(pc.cpix_to_rgb(data[pos:pos + cp]))
                pos += cp
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

    MAX_WIDTH = 2048           # TightConstants.h TIGHT_MAX_WIDTH

    def decode(self, x, y, w, h, stream, fb, pc):
        if w > self.MAX_WIDTH:
            raise DecodeError("TightDecoder: too large rectangle (%d "
                              "pixels)" % w)
        ctl = stream.u8()
        for i in range(4):
            if ctl & (1 << i):
                self.zs[i] = zlib.decompressobj()
        ctl >>= 4
        tp = pc.tpix

        if ctl == self.FILL:
            fb.fill_rect(x, y, w, h, bytes(pc.tpix_to_rgb(stream.read(tp))))
            return
        if ctl == self.JPEG:
            data = stream.read(self._compact_length(stream))
            fb.image_rect(x, y, w, h, decode_jpeg(data, w, h))
            return
        if ctl > self.JPEG:
            raise DecodeError("Illegal Tight compression received")

        stream_id = ctl & 3
        filt = self.FILTER_COPY
        if ctl & self.EXPLICIT_FILTER:
            filt = stream.u8()

        palette = []
        if filt == self.FILTER_PALETTE:
            n = stream.u8() + 1
            palette = _rgb_list(pc.tpix_to_rgb(stream.read(n * tp)), n)
            row = (w + 7) // 8 if n <= 2 else w
        elif filt == self.FILTER_COPY:
            row = w * tp
        elif filt == self.FILTER_GRADIENT:
            if tp != 3:
                # TigerVNC asserts on this too: servers never send it
                raise DecodeError("Tight gradient filter is only supported "
                                  "for 24 bit colour")
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
            fb.image_rect(x, y, w, h, pc.tpix_to_rgb(data[:size]))

    @staticmethod
    def _gradient(data, w, h):
        stride = w * 3
        out = bytearray(stride * h)
        prev = bytearray(stride)
        for yy in range(h):
            base = yy * stride
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
                    out[base + xx] = v
                    left = v
                    upleft = up
            prev = out[base:base + stride]
        return out


class JPEGDecoder(Decoder):
    """The standalone JPEG encoding (21), as in TigerVNC JPEGDecoder.cxx.

    The rectangle is a bare JPEG stream with no length prefix: we walk
    its segments up to EOI.  Servers may leave out the Huffman (DHT) and
    quantisation (DQT) tables when they are unchanged, so the last ones
    seen are remembered and put back in before the scan if missing.
    """

    def __init__(self):
        self.reset()

    def reset(self):
        self.huffman = []
        self.quant = []

    def decode(self, x, y, w, h, stream, fb, pc):
        out = bytearray()
        seen_huffman = seen_quant = False
        while True:
            marker, kind = stream.u8(), stream.u8()
            if marker != 0xFF:
                raise DecodeError("Invalid JPEG segment marker: 0x%02x" %
                                  marker)
            if kind == 0x01 or 0xD0 <= kind <= 0xD9:     # no payload
                out += bytes((marker, kind))
                if kind == 0xD9:                         # EOI
                    break
                continue
            length = stream.u16()
            if length < 2:
                raise DecodeError("Invalid JPEG segment length")
            body = stream.read(length - 2)
            seg = bytes((marker, kind)) + length.to_bytes(2, "big") + body
            if kind == 0xC0:                             # start of frame
                seen_huffman = seen_quant = False
            elif kind == 0xC4:                           # DHT
                if not seen_huffman:
                    self.huffman = []
                seen_huffman = True
                self.huffman.append(seg)
            elif kind == 0xDB:                           # DQT
                if not seen_quant:
                    self.quant = []
                seen_quant = True
                self.quant.append(seg)
            elif kind == 0xDA:                           # start of scan
                if not seen_huffman:
                    out += b"".join(self.huffman)
                if not seen_quant:
                    out += b"".join(self.quant)
                out += seg
                out += stream.read_jpeg_entropy()
                continue
            out += seg
        fb.image_rect(x, y, w, h, decode_jpeg(bytes(out), w, h))


def make_decoders():
    return {
        enc.RAW: RawDecoder(),
        enc.COPYRECT: CopyRectDecoder(),
        enc.RRE: RREDecoder(),
        enc.CORRE: RREDecoder(compact=True),
        enc.HEXTILE: HextileDecoder(),
        enc.ZRLE: ZRLEDecoder(),
        enc.TIGHT: TightDecoder(),
        enc.JPEG: JPEGDecoder(),
    }
