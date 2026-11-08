# SagatVNC Viewer tests
# Copyright (C) 2026 SagatVNC Viewer contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""The Rust decoder loops (accel/) must give exactly the pixels, and fail
on exactly the inputs, that the pure-Python code does."""

import os
import random
import struct
import unittest
import zlib

from sagatvnc.rfb import accel
from sagatvnc.rfb import encodings as enc
from sagatvnc.rfb.decoders import DecodeError, make_decoders
from sagatvnc.rfb.framebuffer import FrameBuffer
from sagatvnc.rfb.pixelformat import MEDIUM_COLOUR, PixelCodec, PixelFormat

from tests import fakeserver as fs

FULL = PixelCodec(PixelFormat.preferred())
LOW = PixelCodec(MEDIUM_COLOUR)


def decode(encoding, payload, w, h, pc, native):
    """Decode with or without the library; -> framebuffer bytes or the
    DecodeError message."""
    saved = accel.lib
    if not native:
        accel.lib = None
    try:
        fb = FrameBuffer(w, h)
        fb.fill_rect(0, 0, w, h, b"\x11\x22\x33")
        try:
            make_decoders()[encoding].decode(0, 0, w, h,
                                             fs.BytesStream(payload), fb, pc)
        except DecodeError as e:
            return "error: %s" % e
        return bytes(fb.data)
    finally:
        accel.lib = saved


class LoadedTest(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("SAGATVNC_REQUIRE_ACCEL"),
                         "SAGATVNC_REQUIRE_ACCEL not set")
    def test_library_is_loaded(self):
        """CI and release builds: a missing library is a failure there,
        not a skipped parity test."""
        self.assertIsNotNone(accel.lib, accel.status)


@unittest.skipIf(accel.lib is None, "native library not built: %s" %
                 accel.status)
class ParityTest(unittest.TestCase):
    def setUp(self):
        self.rnd = random.Random(7)

    def same(self, encoding, payload, w, h, pc=FULL, expect_error=None):
        native = decode(encoding, payload, w, h, pc, True)
        python = decode(encoding, payload, w, h, pc, False)
        self.assertEqual(native, python)
        if expect_error is not None:
            self.assertEqual(isinstance(native, str), expect_error,
                             native[:60])

    def image(self, w, h, ncol, maxrun=30):
        cols = [bytes(self.rnd.randrange(256) for _ in range(3))
                for _ in range(ncol)]
        out = bytearray()
        for _ in range(h):
            x = 0
            while x < w:
                n = min(w - x, self.rnd.randrange(1, maxrun))
                out += self.rnd.choice(cols) * n
                x += n
        return bytes(out)

    def test_tight_gradient(self):
        for w, h in ((1, 1), (5, 3), (64, 9), (301, 17)):
            data = bytes(self.rnd.randrange(256) for _ in range(w * h * 3))
            z = zlib.compressobj()
            body = z.compress(data) + z.flush(zlib.Z_SYNC_FLUSH)
            payload = bytes([0x40, 2])            # explicit filter, gradient
            if len(data) < 12:
                payload += data
            else:
                payload += fs._compact_len(len(body)) + body
            self.same(enc.TIGHT, payload, w, h, expect_error=False)

    def test_zrle_rle_tiles(self):
        for mode in ("plainrle", "palrle"):
            for w, h, ncol in ((64, 64, 3), (100, 70, 9), (7, 130, 120)):
                img = self.image(w, h, ncol)
                payload = fs.encode_zrle(w, h, img, mode, zlib.compressobj())
                self.same(enc.ZRLE, payload, w, h, expect_error=False)

    def test_zrle_low_colour(self):
        w, h = 70, 33
        img = bytes(self.rnd.randrange(256) for _ in range(w * h))
        raw = bytearray()
        for ty in range(0, h, 64):
            for tx in range(0, w, 64):
                tw, th = min(64, w - tx), min(64, h - ty)
                raw.append(128)                   # plain RLE, 1 byte cpix
                for yy in range(th):
                    for xx in range(tw):
                        raw += bytes([img[(ty + yy) * w + tx + xx], 0])
        z = zlib.compressobj()
        body = z.compress(bytes(raw)) + z.flush(zlib.Z_SYNC_FLUSH)
        self.same(enc.ZRLE, struct.pack(">I", len(body)) + body, w, h,
                  pc=LOW, expect_error=False)

    def test_zrle_corrupt(self):
        for raw in (bytes([130, 1, 2, 3, 4, 5, 6, 0x85, 255, 255]),  # short
                    bytes([130, 1, 2, 3, 4, 5, 6, 0x81, 255, 255, 255,
                           255, 255, 255, 255, 255, 255, 255, 255, 255,
                           255, 255, 255, 255, 255, 30]),             # long
                    bytes([130, 1, 2, 3, 4, 5, 6, 5]),                # index
                    bytes([128, 1, 2, 3, 255, 255])):                 # short
            z = zlib.compressobj()
            body = z.compress(raw) + z.flush(zlib.Z_SYNC_FLUSH)
            self.same(enc.ZRLE, struct.pack(">I", len(body)) + body,
                      64, 64, expect_error=True)

    def hextile_payload(self, w, h, bp, bad=False):
        rnd = self.rnd
        out = bytearray()
        pix = (lambda: bytes(rnd.randrange(256) for _ in range(bp)))
        for ty in range(0, h, 16):
            for tx in range(0, w, 16):
                tw, th = min(16, w - tx), min(16, h - ty)
                kind = rnd.randrange(5)
                if kind == 0:
                    out += b"\x01" + bytes(rnd.randrange(256)
                                           for _ in range(tw * th * bp))
                    continue
                sub = 0
                body = bytearray()
                if rnd.random() < 0.7:
                    sub |= 2
                    body += pix()
                if kind >= 2:
                    coloured = kind == 4
                    if not coloured and rnd.random() < 0.7:
                        sub |= 4
                        body += pix()
                    sub |= 8 | (16 if coloured else 0)
                    n = rnd.randrange(0, 40)
                    body.append(n)
                    for _ in range(n):
                        if coloured:
                            body += pix()
                        # whole 16x16 range, so edge tiles get clipped
                        sx, sy = rnd.randrange(16), rnd.randrange(16)
                        sw = rnd.randrange(1, 17 - sx)
                        sh = rnd.randrange(1, 17 - sy)
                        if bad and rnd.random() < 0.05:
                            sw = 16
                            sx = max(sx, 1)
                        body += bytes([(sx << 4) | sy,
                                       ((sw - 1) << 4) | (sh - 1)])
                out += bytes([sub]) + body
        return bytes(out)

    def test_hextile(self):
        for pc, bp in ((FULL, 4), (LOW, 1)):
            for w, h in ((16, 16), (37, 21), (100, 50)):
                self.same(enc.HEXTILE, self.hextile_payload(w, h, bp),
                          w, h, pc=pc, expect_error=False)

    def test_hextile_bad_subrect(self):
        payload = bytes([2 | 8, 1, 2, 3, 0, 1, 0x80, 0xF0])   # 9+16 > 16
        self.same(enc.HEXTILE, payload, 16, 16, expect_error=True)
        self.same(enc.HEXTILE, self.hextile_payload(64, 64, 4, bad=True),
                  64, 64)

    def rre_payload(self, w, h, count, bp, compact):
        rnd = self.rnd
        out = bytearray(struct.pack(">I", count))
        out += bytes(rnd.randrange(256) for _ in range(bp))
        for _ in range(count):
            out += bytes(rnd.randrange(256) for _ in range(bp))
            sx, sy = rnd.randrange(w), rnd.randrange(h)
            sw, sh = rnd.randrange(0, w - sx + 1), rnd.randrange(0, h - sy + 1)
            if compact:
                out += bytes([sx, sy, sw, sh])
            else:
                out += struct.pack(">HHHH", sx, sy, sw, sh)
        return bytes(out)

    def test_rre(self):
        for e, compact in ((enc.RRE, False), (enc.CORRE, True)):
            for pc, bp in ((FULL, 4), (LOW, 1)):
                for count in (0, 3, 200):
                    self.same(e, self.rre_payload(90, 60, count, bp,
                                                  compact),
                              90, 60, pc=pc, expect_error=False)

    def test_rre_outside(self):
        payload = struct.pack(">I", 20) + bytes(4) + b"".join(
            bytes(4) + struct.pack(">HHHH", 1, 1, 5, 5) for _ in range(19)) \
            + bytes(4) + struct.pack(">HHHH", 8, 0, 5, 5)
        self.same(enc.RRE, payload, 10, 10, expect_error=True)


class LoaderTest(unittest.TestCase):
    def test_status_is_reported(self):
        self.assertTrue(accel.status)


if __name__ == "__main__":
    unittest.main()
