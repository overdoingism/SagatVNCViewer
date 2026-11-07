# SagatVNC Viewer tests
# Copyright (C) 2026 SagatVNC Viewer contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Cursor pseudo-encoding, .cur generation and the extended clipboard."""

import socket
import struct
import threading
import unittest
import zlib

from sagatvnc.rfb import encodings as enc
from sagatvnc.rfb.connection import Connection, ConnectionOptions
from sagatvnc.rfb.security import parse_security_types
from sagatvnc.viewer.cursor import make_cur

from tests import fakeserver as fs
from tests.test_rfb import RecordingHandler


class CurFileTest(unittest.TestCase):
    def test_layout(self):
        w, h = 3, 2
        rgba = bytes([255, 0, 0, 255, 0, 255, 0, 0, 0, 0, 255, 255,
                      1, 2, 3, 255, 4, 5, 6, 255, 7, 8, 9, 0])
        data = make_cur(w, h, 1, 0, rgba)
        reserved, kind, count = struct.unpack("<HHH", data[:6])
        self.assertEqual((reserved, kind, count), (0, 2, 1))   # 2 = cursor
        bw, bh, _, _, hx, hy, size, off = struct.unpack("<BBBBHHII",
                                                        data[6:22])
        self.assertEqual((bw, bh, hx, hy, off), (3, 2, 1, 0, 22))
        self.assertEqual(size, len(data) - 22)
        hdr = struct.unpack("<IiiHH", data[22:38])
        self.assertEqual(hdr, (40, 3, 4, 1, 32))     # height doubled, 32bpp
        pixels = data[62:62 + w * h * 4]
        # bottom-up BGRA: first stored row is image row 1
        self.assertEqual(pixels[:4], bytes([3, 2, 1, 255]))
        self.assertEqual(pixels[12:16], bytes([0, 0, 255, 255]))
        mask = data[62 + w * h * 4:]
        self.assertEqual(len(mask), 8)               # 2 rows x 4 bytes
        self.assertEqual(mask[0], 0b00100000)        # row 1: px 2 clear
        self.assertEqual(mask[4], 0b01000000)        # row 0: px 1 clear


class FitToCanvasTest(unittest.TestCase):
    """Windows stretches loaded cursors to SM_CXCURSOR; we pad instead."""

    def test_pad_keeps_pixels_and_hotspot(self):
        from sagatvnc.viewer.cursor import fit_to_canvas
        rgba = bytes(range(2 * 3 * 4))                 # 2x3 cursor
        w, h, hx, hy, out = fit_to_canvas(2, 3, 1, 2, rgba, (4, 4))
        self.assertEqual((w, h, hx, hy), (4, 4, 1, 2))
        self.assertEqual(out[0:8], rgba[0:8])          # row 0
        self.assertEqual(out[16:24], rgba[8:16])       # row 1
        self.assertEqual(out[8:16], b"\0" * 8)         # transparent pad

    def test_too_big_without_pillow_is_cropped(self):
        from sagatvnc.viewer import cursor
        saved, cursor.Image = cursor.Image, None
        try:
            rgba = bytes(range(256)) * 1               # 8x8 cursor
            w, h, hx, hy, out = cursor.fit_to_canvas(8, 8, 7, 7, rgba,
                                                     (4, 4))
        finally:
            cursor.Image = saved
        self.assertEqual((w, h, hx, hy), (4, 4, 3, 3))
        self.assertEqual(out[16:32], rgba[32:48])      # row 1, cols 0-3

    @staticmethod
    def ibeam(w=9, h=20):
        """winvnc's I-beam: 1px black bar with a 1px white outline"""
        rgba = bytearray(w * h * 4)
        for y in range(1, h - 1):
            for x in (3, 4, 5):
                o = (y * w + x) * 4
                rgba[o:o + 4] = b"\0\0\0\xff" if x == 4 and 1 < y < h - 2 \
                    else b"\xff\xff\xff\xff"
        return bytes(rgba)

    def middle_row(self, w, h, rgba):
        r = h // 2
        return [rgba[(r * w + x) * 4] for x in range(w)
                if rgba[(r * w + x) * 4 + 3]]

    def test_shrunk_ibeam_keeps_black_line_and_white_edge(self):
        from sagatvnc.viewer.cursor import shrink_cursor
        for f in (0.8, 0.62, 0.5):
            w, h, hx, hy, d = shrink_cursor(9, 20, 4, 10, self.ibeam(), f)
            row = self.middle_row(w, h, d)
            self.assertIn(0, row, "black line lost at %.2f" % f)
            self.assertEqual((row[0], row[-1]), (255, 255),
                             "white edge lost at %.2f" % f)
            self.assertTrue(set(row) <= {0, 255})       # no grey smear

    def test_one_tone_cursor_gets_outline(self):
        from sagatvnc.viewer.cursor import ensure_contrast
        bare = bytearray(5 * 5 * 4)
        bare[(2 * 5 + 2) * 4:(2 * 5 + 2) * 4 + 4] = b"\0\0\0\xff"
        w, h, hx, hy, d = ensure_contrast(5, 5, 2, 2, bytes(bare))
        self.assertEqual((w, h, hx, hy), (7, 7, 3, 3))
        self.assertEqual(self.middle_row(w, h, d), [255, 0, 255])
        # two-tone cursors (like the I-beam above) are left alone
        self.assertEqual(ensure_contrast(9, 20, 4, 10, self.ibeam())[:2],
                         (9, 20))

    def test_enlarge_nearest_is_crisp(self):
        from sagatvnc.viewer.cursor import enlarge_nearest
        w, h, hx, hy, d = enlarge_nearest(9, 20, 4, 10, self.ibeam(), 2)
        self.assertEqual((w, h, hx, hy), (18, 40, 8, 20))
        self.assertEqual(self.middle_row(w, h, d), [255, 255, 0, 0, 255, 255])

    def test_transparent_cursor_becomes_local_ibeam(self):
        """winvnc sends Windows' I-beam fully transparent (colour cursor
        with zero alpha); 0x0 means really hidden"""
        from sagatvnc.viewer.cursor import CursorManager

        class Widget:
            cursor = None

            def configure(self, cursor):
                self.cursor = cursor
        wdg = Widget()
        m = CursorManager(wdg)
        m.set_shape(1, 1, 0, 0, b"\0\0\0\0")
        self.assertEqual(wdg.cursor, "xterm")
        m.set_shape(0, 0, 0, 0, b"")
        self.assertEqual(wdg.cursor, "none")
        m = CursorManager(wdg, blank_as_ibeam=False)
        m.set_shape(1, 1, 0, 0, b"\0\0\0\0")
        self.assertEqual(wdg.cursor, "none")

    def test_enlarge_two_tone_by_1_5_stays_crisp(self):
        from sagatvnc.viewer.cursor import resize_cursor
        w, h, hx, hy, d = resize_cursor(9, 20, 4, 10, self.ibeam(), 1.5)
        self.assertEqual((w, h), (14, 30))
        self.assertEqual((hx, hy), (6, 15))
        row = self.middle_row(w, h, d)
        self.assertIn(0, row)
        self.assertTrue(set(row) <= {0, 255})           # no grey
        alphas = set(d[3::4])
        self.assertTrue(alphas <= {0, 255})

    def test_dpi_alias_in_tigervnc_file(self):
        import os
        import tempfile
        from sagatvnc.viewer import configfile
        from sagatvnc.viewer.parameters import Options
        fd, path = tempfile.mkstemp(suffix=".tigervnc")
        os.write(fd, b"TigerVNC Configuration file Version 1.0\n\n"
                     b"ServerName=host:1\nDPI=150\n")
        os.close(fd)
        self.addCleanup(os.remove, path)
        opts = Options()
        self.assertEqual(configfile.load(path, opts), "host:1")
        self.assertEqual(opts.remotedpiscale, 150)


class Session(unittest.TestCase):
    def start(self, **opts):
        a, b = socket.socketpair()
        self.addCleanup(a.close)
        self.addCleanup(b.close)
        self.server = fs.FakeServer(b, width=16, height=8)
        self.handler = RecordingHandler()
        self.handler.cursors = []
        self.handler.set_cursor = lambda *a: self.handler.cursors.append(a)
        self.handler.got_cut = threading.Event()
        orig = self.handler.server_cut_text

        def cut(text):
            orig(text)
            self.handler.got_cut.set()
        self.handler.server_cut_text = cut
        self.conn = Connection(a, "test", self.handler, ConnectionOptions(
            security_types=parse_security_types("None"), **opts))
        self.thread = threading.Thread(target=self.conn.run, daemon=True)
        self.thread.start()
        self.server.handshake()
        self.server.wait_for("fbur")
        self.addCleanup(self.finish)

    def finish(self):
        self.conn.close()
        self.thread.join(5)

    def ext(self, flags, payload=b""):
        self.server.send(struct.pack(">B3xi", 3, -(4 + len(payload))) +
                         struct.pack(">I", flags) + payload)


class CursorTest(Session):
    def test_cursor_pseudo_encoding(self):
        self.start()
        self.assertIn(enc.PSEUDO_CURSOR, self.server.encodings)
        # 3x2 cursor, hotspot 1,1; mask: row0 = 110, row1 = 011
        pix = fs.rgbx(bytes(range(18)))
        mask = bytes([0b11000000, 0b01100000])
        self.server.update([self.server.rect(1, 1, 3, 2, enc.PSEUDO_CURSOR,
                                             pix + mask)])
        self.server.sync()
        w, h, hx, hy, rgba = self.handler.cursors[-1]
        self.assertEqual((w, h, hx, hy), (3, 2, 1, 1))
        self.assertEqual(rgba[0:4], bytes([0, 1, 2, 255]))
        self.assertEqual(rgba[11], 0)          # pixel (2,0) transparent
        self.assertEqual(rgba[12:16], bytes([9, 10, 11, 0]))  # (0,1) clear
        self.assertEqual(rgba[19], 255)        # (1,1) visible


class ExtendedClipboardTest(Session):
    ALL = (enc.CLIP_CAPS | enc.CLIP_UTF8 | enc.CLIP_REQUEST |
           enc.CLIP_PEEK | enc.CLIP_NOTIFY | enc.CLIP_PROVIDE)

    def provide(self, text):
        data = text.encode("utf-8") + b"\0"
        z = zlib.compressobj()
        return z.compress(struct.pack(">I", len(data)) + data) + \
            z.flush(zlib.Z_SYNC_FLUSH)

    def handshake_caps(self, utf8_size=0):
        self.ext(self.ALL, struct.pack(">I", utf8_size))
        m = self.server.wait_for("extclip")
        self.assertTrue(m[1] & enc.CLIP_CAPS and m[1] & enc.CLIP_UTF8)
        self.assertEqual(m[2], struct.pack(">I", 0))

    def test_chinese_from_server(self):
        self.start()
        self.assertIn(enc.PSEUDO_EXTENDED_CLIPBOARD, self.server.encodings)
        self.handshake_caps()
        self.ext(enc.CLIP_NOTIFY | enc.CLIP_UTF8)
        m = self.server.wait_for("extclip")
        self.assertEqual(m[1], enc.CLIP_REQUEST | enc.CLIP_UTF8)
        self.ext(enc.CLIP_PROVIDE | enc.CLIP_UTF8,
                 self.provide("中文\r\n測試 ✓"))
        self.assertTrue(self.handler.got_cut.wait(5))
        self.assertEqual(self.handler.cut[-1], "中文\n測試 ✓")

    def test_chinese_to_server_via_notify(self):
        self.start()
        self.handshake_caps(utf8_size=0)     # server wants notify first
        self.conn.announce_clipboard("你好\n世界")
        m = self.server.wait_for("extclip")
        self.assertEqual(m[1], enc.CLIP_NOTIFY | enc.CLIP_UTF8)
        self.ext(enc.CLIP_REQUEST | enc.CLIP_UTF8)
        m = self.server.wait_for("extclip")
        self.assertEqual(m[1], enc.CLIP_PROVIDE | enc.CLIP_UTF8)
        data = zlib.decompressobj().decompress(m[2])
        n = struct.unpack(">I", data[:4])[0]
        self.assertEqual(data[4:4 + n], "你好\r\n世界".encode("utf-8") + b"\0")

    def test_unsolicited_when_server_allows(self):
        self.start()
        self.handshake_caps(utf8_size=1024)
        self.conn.announce_clipboard("直接送")
        m = self.server.wait_for("extclip")
        self.assertEqual(m[1], enc.CLIP_PROVIDE | enc.CLIP_UTF8)

    def test_notify_only_fetched_when_viewer_wants_it(self):
        # A viewer without focus ignores the notify (TigerVNC Viewport),
        # so the server is never asked to read its clipboard.
        self.start()
        announced = []
        self.handler.clipboard_announce = announced.append
        self.handshake_caps()
        self.ext(enc.CLIP_NOTIFY | enc.CLIP_UTF8)
        self.ext(enc.CLIP_PEEK)                  # marker: what comes next?
        m = self.server.wait_for("extclip")
        self.assertEqual(m[1], enc.CLIP_NOTIFY)  # the peek reply, no request
        self.assertEqual(announced, [True])

    def test_legacy_cut_text_goes_through_announce(self):
        self.start()
        announced = []
        self.handler.clipboard_announce = announced.append
        text = "plain".encode("latin-1")
        self.server.send(struct.pack(">B3xI", 3, len(text)) + text)
        self.server.update([])
        self.server.wait_for("fbur")
        self.assertEqual(announced, [True])
        self.assertEqual(self.handler.cut, [])   # not applied by itself
        self.conn.request_clipboard()            # what a focused GUI does
        self.assertEqual(self.handler.cut, ["plain"])

    def test_gui_ignores_announce_without_focus(self):
        from types import SimpleNamespace
        from sagatvnc.viewer.app import ViewerApp
        calls = []
        app = SimpleNamespace(
            view_only=lambda: False, has_focus=False,
            opts=SimpleNamespace(acceptclipboard=True),
            conn=SimpleNamespace(request_clipboard=lambda: calls.append(1)),
            trace_clipboard=lambda text: None)
        ViewerApp.clipboard_announce(app, True)
        self.assertEqual(calls, [])
        app.has_focus = True
        ViewerApp.clipboard_announce(app, True)
        self.assertEqual(calls, [1])

    def test_peek(self):
        self.start()
        self.handshake_caps()
        self.ext(enc.CLIP_PEEK)
        m = self.server.wait_for("extclip")
        self.assertEqual(m[1], enc.CLIP_NOTIFY)          # nothing yet

    def test_key_log_shows_clipboard_traffic(self):
        from sagatvnc.rfb.connection import describe_text
        self.start()
        lines = []
        self.conn.trace = lines.append
        self.handshake_caps()
        self.conn.announce_clipboard("ours")
        self.server.wait_for("extclip")                  # our notify
        self.ext(enc.CLIP_REQUEST | enc.CLIP_UTF8)
        self.server.wait_for("extclip")                  # our provide
        self.ext(enc.CLIP_NOTIFY | enc.CLIP_UTF8)
        self.server.wait_for("extclip")                  # our request
        self.ext(enc.CLIP_REQUEST | enc.CLIP_UTF8)       # we have none now
        self.ext(enc.CLIP_PROVIDE | enc.CLIP_UTF8, self.provide("theirs"))
        self.assertTrue(self.handler.got_cut.wait(5))
        self.assertEqual(lines, [
            "-> our clipboard changed: told the server (%s)"
            % describe_text("ours"),
            "server asks for our clipboard: sent %s" % describe_text("ours"),
            "server: its clipboard changed",
            "-> asking the server for its clipboard",
            "server asks for our clipboard: ignored, we haven't announced "
            "one",
            "server sent its clipboard (%s)" % describe_text("theirs"),
        ])

    def test_legacy_server_gets_latin1(self):
        self.start()
        self.conn.announce_clipboard("café")
        self.assertEqual(self.server.wait_for("cut"), ("cut", "café"))


if __name__ == "__main__":
    unittest.main()
