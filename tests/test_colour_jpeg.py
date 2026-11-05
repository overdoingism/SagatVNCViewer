# SagatVNC Viewer tests
# Copyright (C) 2026 SagatVNC Viewer contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Low colour pixel formats, JPEG decoding and AutoSelect."""

import io
import random
import socket
import struct
import threading
import unittest
import zlib

from sagatvnc.rfb import encodings as enc
from sagatvnc.rfb.connection import Connection, ConnectionOptions
from sagatvnc.rfb.decoders import JPEG_AVAILABLE, make_decoders
from sagatvnc.rfb.framebuffer import FrameBuffer
from sagatvnc.rfb.pixelformat import (LOW_COLOUR, LOW_COLOUR_LEVELS,
                                      MEDIUM_COLOUR, VERY_LOW_COLOUR,
                                      PixelCodec, PixelFormat)
from sagatvnc.rfb.security import parse_security_types
from sagatvnc.rfb.stream import Stream

from tests import fakeserver as fs
from tests.test_rfb import RecordingHandler

MED = PixelCodec(MEDIUM_COLOUR)


def med_rgb(p):
    """Reference: what a bgr233 pixel means in 8 bit RGB."""
    return bytes((((p >> 5) & 7) * 255 // 7, ((p >> 2) & 7) * 255 // 7,
                  (p & 3) * 255 // 3))


def decode(encoding, payload, w, h, codec=MED, decoders=None):
    fb = FrameBuffer(w, h)
    s = fs.BytesStream(payload)
    (decoders or make_decoders())[encoding].decode(0, 0, w, h, s, fb, codec)
    assert s.pos == len(payload), "decoder left data unread"
    return fb


class LowColourTest(unittest.TestCase):
    def setUp(self):
        rnd = random.Random(3)
        self.w, self.h = 37, 21
        self.px = bytes(rnd.randrange(256) for _ in range(self.w * self.h))
        self.rgb = b"".join(med_rgb(p) for p in self.px)

    def test_formats_match_tigervnc(self):
        self.assertEqual(VERY_LOW_COLOUR.pack(), PixelFormat(
            8, 3, False, True, 1, 1, 1, 2, 1, 0).pack())
        self.assertEqual(LOW_COLOUR.depth, 6)
        self.assertEqual((MEDIUM_COLOUR.red_max, MEDIUM_COLOUR.blue_max),
                         (7, 3))

    def test_codec(self):
        self.assertEqual(MED.pixel(b"\xe3"), b"\xff\x00\xff")
        self.assertEqual(bytes(MED.to_rgb(self.px)), self.rgb)
        vl = PixelCodec(VERY_LOW_COLOUR)
        self.assertEqual(vl.pixel(bytes([0b101])), b"\xff\x00\xff")

    def test_raw(self):
        fb = decode(enc.RAW, self.px, self.w, self.h)
        self.assertEqual(bytes(fb.data), self.rgb)

    def test_hextile(self):
        payload = (bytes([2 | 8 | 16]) + b"\x03" + b"\x01" + b"\xe0" +
                   bytes([0x23, 0x11]))           # bg 3, subrect at 2,3 2x2
        fb = decode(enc.HEXTILE, payload, 16, 16)
        self.assertEqual(fb.get_pixel(0, 0), med_rgb(3))
        self.assertEqual(fb.get_pixel(3, 4), med_rgb(0xE0))

    def test_zrle(self):
        z = zlib.compressobj()
        raw = b"\x00" + self.px                    # raw tile, 1 byte cpixels
        pal_rle = bytes([130, 0x1C, 0xE3]) + bytes([0 | 128, 9, 1])
        data = z.compress(raw) + z.flush(zlib.Z_SYNC_FLUSH)
        fb = decode(enc.ZRLE, struct.pack(">I", len(data)) + data,
                    self.w, self.h)
        self.assertEqual(bytes(fb.data), self.rgb)
        data = z.compress(pal_rle) + z.flush(zlib.Z_SYNC_FLUSH)
        dec = make_decoders()
        dec[enc.ZRLE].zs = zlib.decompressobj()
        z2 = zlib.compressobj()
        data = z2.compress(pal_rle) + z2.flush(zlib.Z_SYNC_FLUSH)
        fb = decode(enc.ZRLE, struct.pack(">I", len(data)) + data, 11, 1,
                    decoders=dec)
        self.assertEqual(fb.get_rect(0, 0, 11, 1),
                         med_rgb(0x1C) * 10 + med_rgb(0xE3))

    def test_tight(self):
        dec = make_decoders()
        fb = decode(enc.TIGHT, b"\x80\x1c", 5, 5, decoders=dec)   # fill
        self.assertEqual(fb.get_pixel(4, 4), med_rgb(0x1C))
        z = zlib.compressobj()
        data = z.compress(self.px) + z.flush(zlib.Z_SYNC_FLUSH)
        n = len(data)
        clen = bytes([n & 0x7F | 0x80, (n >> 7) & 0x7F]) if n > 127 \
            else bytes([n])
        fb = decode(enc.TIGHT, b"\x00" + clen + data, self.w, self.h,
                    decoders=dec)                                  # copy
        self.assertEqual(bytes(fb.data), self.rgb)
        # palette of 2, 1 bit per pixel, 8 pixels
        fb = decode(enc.TIGHT, b"\x40\x01\x01\x03\xe0" + b"\xa5", 8, 1,
                    decoders=dec)
        self.assertEqual(fb.get_pixel(0, 0), med_rgb(0xE0))
        self.assertEqual(fb.get_pixel(1, 0), med_rgb(0x03))


def smooth_image(w, h):
    return bytes(((x * 255 // w + c * 60) & 0xFF) if c != 1 else
                 (y * 255 // h) for y in range(h) for x in range(w)
                 for c in range(3))


@unittest.skipUnless(JPEG_AVAILABLE, "Pillow not installed")
class JPEGTest(unittest.TestCase):
    W, H = 48, 32

    def jpeg(self, rgb, quality=95):
        from PIL import Image
        buf = io.BytesIO()
        Image.frombytes("RGB", (self.W, self.H), rgb).save(
            buf, "JPEG", quality=quality)
        return buf.getvalue()

    def assert_close(self, a, b, tolerance=6):
        diff = sum(abs(x - y) for x, y in zip(a, b)) / len(a)
        self.assertLess(diff, tolerance)

    def test_tight_jpeg(self):
        rgb = smooth_image(self.W, self.H)
        data = self.jpeg(rgb)
        clen = fs._compact_len(len(data))
        fb = decode(enc.TIGHT, b"\x90" + clen + data, self.W, self.H,
                    codec=PixelCodec(PixelFormat.preferred()))
        self.assert_close(bytes(fb.data), rgb)

    @staticmethod
    def strip_tables(data):
        """Remove DHT/DQT segments, as servers do when they're unchanged."""
        out, i = bytearray(data[:2]), 2
        while True:
            kind = data[i + 1]
            if kind == 0xDA:
                return bytes(out + data[i:])
            length = int.from_bytes(data[i + 2:i + 4], "big")
            if kind not in (0xC4, 0xDB):
                out += data[i:i + 2 + length]
            i += 2 + length

    def test_jpeg_encoding_reuses_tables(self):
        dec = make_decoders()
        full = PixelCodec(PixelFormat.preferred())
        first = self.jpeg(smooth_image(self.W, self.H))
        second_rgb = bytes(reversed(smooth_image(self.W, self.H)))
        second = self.strip_tables(self.jpeg(second_rgb))
        self.assertLess(len(second), len(self.jpeg(second_rgb)))
        decode(enc.JPEG, first, self.W, self.H, full, dec)
        fb = decode(enc.JPEG, second, self.W, self.H, full, dec)
        self.assert_close(bytes(fb.data), second_rgb)

    def test_entropy_scan_across_reads(self):
        class Trickle:
            def __init__(self, data):
                self.data = data

            def recv(self, n):
                chunk, self.data = self.data[:3], self.data[3:]
                return chunk
        body = b"\x12\xff\x00\x34\xff\xd3\x56\xff"
        s = Stream(Trickle(body + b"\xd9tail"))
        self.assertEqual(s.read_jpeg_entropy(), body[:-1])
        self.assertEqual(s.read(2), b"\xff\xd9")


class AutoSelectTest(unittest.TestCase):
    def conn(self, **opts):
        return Connection(None, "test", RecordingHandler(),
                          ConnectionOptions(**opts))

    def test_slow_line_drops_to_low_colour_and_quality_6(self):
        c = self.conn(low_colour_level=1)
        self.assertEqual(c.pf, PixelFormat.preferred())
        for _ in range(60):                  # ~64 kbit/s for a while
            c._update_bandwidth(8000, 1.0)
            c._auto_select()
        self.assertLess(c.bps_estimate, 256000)
        # CConnection::setPF(): queued for the next update request
        self.assertTrue(c.format_change)
        self.assertEqual(c.next_pf, LOW_COLOUR_LEVELS[1])
        self.assertEqual(c.quality, 6)
        self.assertIn(enc.PSEUDO_QUALITY_LEVEL_0 + 6, c.encodings_list()
                      if c.jpeg_enabled() else
                      [enc.PSEUDO_QUALITY_LEVEL_0 + 6])

    def test_manual_settings(self):
        c = self.conn(auto_select=False, full_colour=False,
                      low_colour_level=0, quality_level=3,
                      preferred_encoding=enc.ZRLE)
        self.assertEqual(c._wanted_pf(), VERY_LOW_COLOUR)
        codes = c.encodings_list()
        i = codes.index(enc.ZRLE)
        self.assertEqual(codes[i + 1], enc.COPYRECT)
        self.assertEqual(codes[i + 2:i + 7], [enc.JPEG, enc.TIGHT,
                                              enc.HEXTILE, enc.RRE, enc.RAW]
                         if c.jpeg_enabled() else
                         [enc.TIGHT, enc.HEXTILE, enc.RRE, enc.RAW])
        if c.jpeg_enabled():
            self.assertEqual(codes[-1], enc.PSEUDO_QUALITY_LEVEL_0 + 3)
        c = self.conn(no_jpeg=True)
        self.assertNotIn(enc.JPEG, c.encodings_list())
        self.assertFalse(any(enc.PSEUDO_QUALITY_LEVEL_0 <= e <=
                             enc.PSEUDO_QUALITY_LEVEL_0 + 9
                             for e in c.encodings_list()))


class SwitchPixelFormatTest(unittest.TestCase):
    def test_switch_takes_effect_between_updates(self):
        a, b = socket.socketpair()
        self.addCleanup(a.close)
        self.addCleanup(b.close)
        server = fs.FakeServer(b, width=8, height=4)
        conn = Connection(a, "test", RecordingHandler(), ConnectionOptions(
            security_types=parse_security_types("None"), auto_select=False))
        t = threading.Thread(target=conn.run, daemon=True)
        t.start()
        server.handshake()
        server.wait_for("fbur")
        # The user picks 256 colours while an update is on its way
        conn.change_settings(full_colour=False, low_colour_level=2)
        server.update([server.rect(0, 0, 8, 4, enc.RAW,
                                   fs.encode_raw(fs.pattern(8, 4)))])
        # Sent when that update starts, then the next request
        m = server.wait_for("pf")
        self.assertEqual(m[1], MEDIUM_COLOUR.pack())
        server.wait_for("fbur")
        server.sync()
        # ... but that update was still decoded in full colour
        self.assertEqual(bytes(conn.fb.data), fs.pattern(8, 4))
        px = bytes(range(32))
        server.update([server.rect(0, 0, 8, 4, enc.RAW, px)])
        server.sync()
        self.assertEqual(bytes(conn.fb.data), b"".join(med_rgb(p)
                                                       for p in px))
        conn.close()
        t.join(5)


if __name__ == "__main__":
    unittest.main()
