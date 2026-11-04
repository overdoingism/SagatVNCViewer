# SagatVNC Viewer tests
# Copyright (C) 2026 SagatVNC Viewer contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Behaviour ported from TigerVNC after the fidelity audit.  Each test
names the upstream function it pins down."""

import socket
import struct
import threading
import unittest
import zlib

from sagatvnc.rfb import encodings as enc
from sagatvnc.rfb.connection import (Connection, ConnectionOptions,
                                     DroppedBeforeSession)
from sagatvnc.rfb.decoders import make_decoders
from sagatvnc.rfb.framebuffer import FrameBuffer
from sagatvnc.rfb.pixelformat import MEDIUM_COLOUR, PixelCodec, PixelFormat
from sagatvnc.rfb.security import AuthFailure, SecurityError, \
    parse_security_types

from tests import fakeserver as fs
from tests.test_rfb import RecordingHandler

FULL = PixelCodec(PixelFormat.preferred())


class Base(unittest.TestCase):
    def start(self, sec="None", password=None, **opts):
        a, b = socket.socketpair()
        self.addCleanup(a.close)
        self.addCleanup(b.close)
        self.server = fs.FakeServer(b, width=16, height=8, password=password)
        self.handler = RecordingHandler()
        self.events = []
        for name in ("set_led_state", "set_cursor", "set_cursor_pos",
                     "set_desktop_size_done"):
            setattr(self.handler, name,
                    (lambda n: lambda *a: self.events.append((n,) + a))(name))
        self.conn = Connection(a, "test", self.handler, ConnectionOptions(
            security_types=parse_security_types(sec), **opts))
        self.result = {}

        def run():
            try:
                self.conn.run()
            except Exception as e:
                self.result["error"] = e
        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()
        self.addCleanup(self.finish)

    def finish(self):
        self.conn.close()
        self.thread.join(5)

    def session(self, **opts):
        self.start(**opts)
        self.assertTrue(self.server.handshake())
        self.server.wait_for("fbur")


class UpdateFlowTest(Base):
    def test_continuous_updates(self):
        """CConnection::endOfContinuousUpdates / framebufferUpdateEnd"""
        self.session(auto_select=False)
        self.server.send(bytes([150]))           # EndOfContinuousUpdates
        self.server.update([])
        m = self.server.wait_for("cu")
        self.assertEqual(m, ("cu", 1, 0, 0, 16, 8))
        self.assertTrue(self.conn.continuous_updates)
        # A format change: disable, SetPixelFormat, enable
        self.conn.change_settings(full_colour=False, low_colour_level=2)
        self.assertEqual(self.server.wait_for("cu")[1], 0)
        self.assertEqual(self.server.wait_for("pf")[1], MEDIUM_COLOUR.pack())
        self.assertEqual(self.server.wait_for("cu")[1], 1)
        self.assertEqual(self.conn.pf, PixelFormat.preferred())  # not yet
        self.server.send(bytes([150]))           # switch marker
        self.server.sync()
        self.assertEqual(self.conn.pf, MEDIUM_COLOUR)
        # No update requests in continuous mode
        self.assertNotIn("fbur", [m[0] for m in self.server.received[-6:]])

    def test_fence_reply(self):
        """CConnection::fence(): only BlockBefore/BlockAfter echoed"""
        self.session()
        flags = enc.FENCE_REQUEST | enc.FENCE_BLOCK_BEFORE | \
            enc.FENCE_SYNC_NEXT
        self.server.send(struct.pack(">B3xIB", 248, flags, 3) + b"abc")
        m = self.server.wait_for("fence")
        self.assertEqual(m, ("fence", enc.FENCE_BLOCK_BEFORE, b"abc"))
        self.assertTrue(self.conn.supports_fence)

    def test_request_sent_at_update_start(self):
        """CConn::framebufferUpdateStart(): the next request goes out
        before the current update is even complete"""
        self.session()
        # Header and first rect header only; the pixels are held back
        self.server.send(struct.pack(">BxH", 0, 1) +
                         struct.pack(">HHHHi", 0, 0, 2, 1, enc.RAW))
        self.assertEqual(self.server.wait_for("fbur")[1], 1)
        self.server.send(b"\0" * 8)
        self.server.sync()

    def test_pre_3_8_never_changes_format(self):
        """CConnection::setPF() ignores pre-3.8 servers"""
        c = Connection(None, "t", RecordingHandler(), ConnectionOptions())
        c.version = (3, 7)
        c.set_pf(MEDIUM_COLOUR)
        self.assertFalse(c.format_change)

    def test_refresh_waits_for_update_in_flight(self):
        """CConnection::refreshFramebuffer() without continuous updates"""
        self.session()
        n = len(self.server.received)
        self.conn.refresh_framebuffer()
        self.server.sync()
        self.assertNotIn("fbur", [m[0] for m in self.server.received[n:]])
        self.server.update([])
        self.assertEqual(self.server.wait_for("fbur")[1], 0)   # full


class PseudoEncodingTest(Base):
    def test_led_state(self):
        self.session()
        self.server.update([
            self.server.rect(0, 0, 0, 0, enc.PSEUDO_LED_STATE, b"\x05"),
            self.server.rect(0, 0, 0, 0, enc.PSEUDO_VMWARE_LED_STATE,
                             struct.pack(">I", 2))])
        self.server.sync()
        self.assertEqual([e for e in self.events if e[0] == "set_led_state"],
                         [("set_led_state", 5), ("set_led_state", 2)])

    def test_cursor_variants(self):
        self.session()
        # XCursor 2x1: fg red, bg blue, data 10, mask 11
        xc = b"\xff\0\0\0\0\xff" + bytes([0b10000000]) + bytes([0b11000000])
        # CursorWithAlpha 1x1, Raw, premultiplied BGRA (half-alpha white)
        ca = struct.pack(">i", enc.RAW) + bytes([128, 128, 128, 128])
        # VMware type 1 (RGBA) 1x1
        vm = b"\x01\x00" + bytes([1, 2, 3, 4])
        self.server.update([
            self.server.rect(0, 0, 2, 1, enc.PSEUDO_X_CURSOR, xc),
            self.server.rect(0, 0, 1, 1, enc.PSEUDO_CURSOR_WITH_ALPHA, ca),
            self.server.rect(0, 0, 1, 1, enc.PSEUDO_VMWARE_CURSOR, vm),
            self.server.rect(7, 3, 0, 0, enc.PSEUDO_VMWARE_CURSOR_POSITION)])
        self.server.sync()
        cur = [e for e in self.events if e[0] == "set_cursor"]
        self.assertEqual(cur[0][5], b"\xff\0\0\xff\0\0\xff\xff")
        self.assertEqual(cur[1][5], bytes([255, 255, 255, 128]))
        self.assertEqual(cur[2][5], bytes([1, 2, 3, 4]))
        self.assertIn(("set_cursor_pos", 7, 3), self.events)

    def test_invalid_desktop_name_ignored(self):
        """CMsgReader::readSetDesktopName()"""
        self.session()
        name = b"x"
        self.server.update([self.server.rect(
            1, 0, 0, 0, enc.PSEUDO_DESKTOP_NAME,
            struct.pack(">I", len(name)) + name)])
        self.server.sync()
        self.assertEqual(self.handler.names, [])

    def test_rect_count_ffff_is_a_count(self):
        """0xFFFF rects is 65535 rects that LastRect may cut short"""
        self.session()
        self.server.update([self.server.rect(0, 0, 1, 1, enc.RAW, b"\0" * 4)],
                           last_rect=True)
        self.server.sync()
        self.assertNotIn("error", self.result)


class KeyboardTest(Base):
    def test_tracking_and_fixups(self):
        """CConnection::sendKeyPress/sendKeyRelease"""
        self.session()
        self.conn.send_key_press(15, 0, 0xFE20)      # ISO_Left_Tab
        self.assertEqual(self.server.wait_for("key"), ("key", 0xFF09, 1))
        self.conn.send_key_release(15)               # same key, not 0xFE20
        self.assertEqual(self.server.wait_for("key"), ("key", 0xFF09, 0))
        n = len(self.server.received)
        self.conn.send_key_release(99)               # never pressed
        self.server.sync()
        self.assertNotIn("key", [m[0] for m in self.server.received[n:]])
        self.conn.send_key_press(1, 0, 0x61)
        self.conn.send_key_press(2, 0, 0x62)
        self.conn.release_all_keys()
        ups = [self.server.wait_for("key") for _ in range(4)][2:]
        self.assertEqual(sorted(u[1] for u in ups), [0x61, 0x62])

    def test_qemu_key_event_after_server_ack(self):
        """CMsgWriter::writeKeyEvent(): QEMU only when supported + code"""
        self.session()
        self.server.update([self.server.rect(0, 0, 0, 0,
                                             enc.PSEUDO_QEMU_KEY_EVENT)])
        self.server.sync()
        self.conn.send_key_press(0x1e, 0x1e, 0x61)
        self.assertEqual(self.server.wait_for("qemukey"),
                         ("qemukey", 0x61, 0x1e, 1))
        self.conn.send_key_press(0x99, 0, 0x62)      # no keycode: classic
        self.assertEqual(self.server.wait_for("key"), ("key", 0x62, 1))


class SecurityTest(unittest.TestCase):
    def run_client(self, server_script, sec):
        a, b = socket.socketpair()
        self.addCleanup(a.close)
        self.addCleanup(b.close)
        conn = Connection(a, "t", RecordingHandler(), ConnectionOptions(
            security_types=parse_security_types(sec)))
        result = {}

        def run():
            try:
                conn.run()
            except Exception as e:
                result["error"] = e
        t = threading.Thread(target=run, daemon=True)
        t.start()
        out = server_script(b)
        t.join(5)
        conn.close()
        return result.get("error"), out

    def test_vencrypt_none_subtype(self):
        """GetEnabledExtSecTypes(): None is a valid VeNCrypt sub-type"""
        def server(b):
            b.sendall(b"RFB 003.008\n")
            b.recv(12)
            b.sendall(b"\x01\x13")                   # VeNCrypt only
            b.recv(1)
            b.sendall(b"\x00\x02")
            b.recv(2)
            b.sendall(b"\x00\x01" + struct.pack(">I", 1))   # ack, [None]
            chosen = struct.unpack(">I", b.recv(4))[0]
            b.sendall(struct.pack(">I", 1))          # security result: fail
            reason = b"no"
            b.sendall(struct.pack(">I", len(reason)) + reason)
            return chosen
        err, chosen = self.run_client(server, "None,X509Plain")
        self.assertEqual(chosen, 1)
        self.assertIsInstance(err, AuthFailure)

    def test_unknown_security_result(self):
        def server(b):
            b.sendall(b"RFB 003.008\n")
            b.recv(12)
            b.sendall(b"\x01\x01")
            b.recv(1)
            b.sendall(struct.pack(">I", 7))
        err, _ = self.run_client(server, "None")
        self.assertIn("Unknown security result", str(err))

    def test_too_many_tries_is_auth_failure(self):
        def server(b):
            b.sendall(b"RFB 003.008\n")
            b.recv(12)
            b.sendall(b"\x01\x01")
            b.recv(1)
            reason = b"Too many security failures"
            b.sendall(struct.pack(">II", 2, len(reason)) + reason)
        err, _ = self.run_client(server, "None")
        self.assertIsInstance(err, AuthFailure)
        self.assertIn("Too many", str(err))

    def test_33_only_none_or_vncauth(self):
        def server(b):
            b.sendall(b"RFB 003.003\n")
            b.recv(12)
            b.sendall(struct.pack(">I", 19))         # VeNCrypt in 3.3
        err, _ = self.run_client(server, "None,TLSNone")
        self.assertIn("3.3", str(err))

    def test_no_match_sends_nothing(self):
        def server(b):
            b.sendall(b"RFB 003.008\n")
            b.recv(12)
            b.sendall(b"\x01\x02")                   # VncAuth only
            b.settimeout(1)
            try:
                return b.recv(1)
            except (socket.timeout, OSError):
                return b""
        err, got = self.run_client(server, "None")
        self.assertIsInstance(err, SecurityError)
        self.assertEqual(got, b"")

    def test_dropped_before_session(self):
        def server(b):
            b.sendall(b"RFB 003.008\n")
            b.recv(12)
            b.sendall(b"\x01\x01")
            b.recv(1)
            b.close()
        err, _ = self.run_client(server, "None")
        self.assertIsInstance(err, DroppedBeforeSession)

    def test_reason_latin1_fallback(self):
        def server(b):
            b.sendall(b"RFB 003.008\n")
            b.recv(12)
            reason = "caf\xe9".encode("latin-1")     # not valid UTF-8
            b.sendall(b"\x00" + struct.pack(">I", len(reason)) + reason)
        err, _ = self.run_client(server, "None")
        self.assertIn("caf\xe9", str(err))


class DecoderFidelityTest(unittest.TestCase):
    def decode(self, encoding, payload, w, h, fb=None):
        fb = fb or FrameBuffer(w, h)
        s = fs.BytesStream(payload)
        make_decoders()[encoding].decode(0, 0, w, h, s, fb, FULL)
        self.assertEqual(s.pos, len(payload))
        return fb

    def zrle(self, raw):
        z = zlib.compressobj()
        data = z.compress(raw) + z.flush(zlib.Z_SYNC_FLUSH)
        return struct.pack(">I", len(data)) + data

    def test_zrle_129_is_solid(self):
        """ZRLEDecoder.cxx: palSize 1 is solid even with the RLE bit"""
        fb = self.decode(enc.ZRLE, self.zrle(bytes([129]) + b"\x01\x02\x03"),
                         5, 3)
        self.assertEqual(fb.get_pixel(4, 2), b"\x01\x02\x03")

    def test_zrle_large_palette_uses_8_bits(self):
        """bppp = 8 for 17-127 palette entries"""
        n, w, h = 20, 6, 2
        pal = b"".join(bytes([i, i, i]) for i in range(n))
        idx = bytes(i % n for i in range(w * h))
        fb = self.decode(enc.ZRLE, self.zrle(bytes([n]) + pal + idx), w, h)
        self.assertEqual(bytes(fb.data), b"".join(bytes([i, i, i])
                                                  for i in idx))

    def test_hextile_coloured_subrect_sets_foreground(self):
        """HextileDecoder.cxx: fg = readPixel() for coloured subrects"""
        t = (bytes([2 | 8 | 16]) + b"\0\0\0\0" + b"\x01" +
             b"\x11\x22\x33\0" + bytes([0x00, 0x00]) +       # tile 1
             bytes([8]) + b"\x01" + bytes([0x00, 0x00]))      # tile 2
        fb = self.decode(enc.HEXTILE, t, 32, 16)
        self.assertEqual(fb.get_pixel(16, 0), b"\x11\x22\x33")

    def test_hextile_subrect_checked_against_16(self):
        """Partial edge tiles: clip instead of failing (only > 16 fails)"""
        t = bytes([2 | 4 | 8]) + b"\0\0\0\0" + b"\x09\x09\x09\0" + b"\x01" + \
            bytes([0x00, 0x77])                      # 8x8 in a 4x4 tile
        fb = self.decode(enc.HEXTILE, t, 4, 4)
        self.assertEqual(fb.get_pixel(3, 3), b"\x09\x09\x09")

    def test_tight_rejects_wide_rects(self):
        from sagatvnc.rfb.decoders import DecodeError
        with self.assertRaises(DecodeError):
            make_decoders()[enc.TIGHT].decode(0, 0, 2049, 1,
                                              fs.BytesStream(b"\x80" + b"\0" * 3),
                                              FrameBuffer(2049, 1), FULL)

    def test_resize_keeps_overlap(self):
        """CConnection::setFramebuffer()"""
        fb = FrameBuffer(4, 2)
        fb.image_rect(0, 0, 4, 2, fs.pattern(4, 2))
        old = fb.get_rect(0, 0, 3, 2)
        fb.resize(3, 5)
        self.assertEqual(fb.get_rect(0, 0, 3, 2), old)
        self.assertEqual(fb.get_pixel(2, 4), b"\0\0\0")


if __name__ == "__main__":
    unittest.main()
