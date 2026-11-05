# SagatVNC Viewer tests
# Copyright (C) 2026 SagatVNC Viewer contributors
# SPDX-License-Identifier: GPL-3.0-or-later

import socket
import struct
import threading
import unittest
import zlib

from sagatvnc.rfb import encodings as enc
from sagatvnc.rfb.connection import Connection, ConnectionOptions
from sagatvnc.rfb.decoders import make_decoders
from sagatvnc.rfb.des import (deobfuscate_password, des_decrypt, des_encrypt,
                              obfuscate_password, vnc_key)
from sagatvnc.rfb.framebuffer import FrameBuffer
from sagatvnc.rfb.pixelformat import PixelCodec, PixelFormat
from sagatvnc.rfb.security import AuthFailure, parse_security_types
from sagatvnc.viewer.keysyms import event_to_keysym
from sagatvnc.viewer.parameters import UsageError, parse_args, parse_server

from tests import fakeserver as fs

FULL = PixelCodec(PixelFormat.preferred())


class DESTest(unittest.TestCase):
    def test_known_vector(self):
        key = bytes.fromhex("133457799BBCDFF1")
        pt = bytes.fromhex("0123456789ABCDEF")
        ct = des_encrypt(key, pt)
        self.assertEqual(ct.hex(), "85e813540f0ab405")
        self.assertEqual(des_decrypt(key, ct), pt)

    def test_vnc_key_bit_reversal(self):
        self.assertEqual(vnc_key("\x01\x80"), b"\x80\x01" + b"\0" * 6)

    def test_passwd_file_roundtrip(self):
        self.assertEqual(deobfuscate_password(obfuscate_password("s3cret")),
                         "s3cret")

    def test_passwd_file_key(self):
        # Widely documented: vncpasswd's fixed key in plain DES form
        from sagatvnc.rfb import des
        self.assertEqual(des._PASSWD_FILE_KEY.hex(), "e84ad660c4721ae0")


class ParameterTest(unittest.TestCase):
    def test_parse_server(self):
        self.assertEqual(parse_server("host"), ("host", 5900))
        self.assertEqual(parse_server("host:1"), ("host", 5901))
        self.assertEqual(parse_server("host:5999"), ("host", 5999))
        self.assertEqual(parse_server("host::42"), ("host", 42))
        self.assertEqual(parse_server(":2"), ("localhost", 5902))
        self.assertEqual(parse_server("[::1]:3"), ("::1", 5903))
        with self.assertRaises(ValueError):
            parse_server("host:abc")

    def test_args(self):
        o = parse_args(["-shared", "-CompressLevel", "5", "PreferredEncoding=ZRLE",
                        "--viewonly=0", "example.com:1"])
        self.assertTrue(o.shared)
        self.assertEqual(o.compresslevel, 5)
        self.assertEqual(o.preferredencoding, "ZRLE")
        self.assertFalse(o.viewonly)
        self.assertEqual(o.server, "example.com:1")
        self.assertEqual(parse_args(["-passwd", "f"]).passwordfile, "f")
        with self.assertRaises(UsageError):
            parse_args(["-NoSuchThing"])

    def test_security_types(self):
        self.assertEqual(parse_security_types("VncAuth,none"),
                         [enc.SEC_VNC_AUTH, enc.SEC_NONE])

    def test_security_types_from_tigervnc_file(self):
        # Exactly what a TigerVNC-saved .tigervnc file contained
        text = ("None,VncAuth,RA2ne,RA2ne_256,Plain,DH,MSLogonII,TLSNone,"
                "TLSVnc,TLSPlain,X509None,X509Vnc,X509Plain,RA2,RA2_256")
        logs = []
        types = parse_security_types(text, logs.append)
        self.assertEqual(types, [enc.SEC_NONE, enc.SEC_VNC_AUTH,
                                 enc.SEC_PLAIN, enc.SEC_TLS_NONE,
                                 enc.SEC_TLS_VNC, enc.SEC_TLS_PLAIN,
                                 enc.SEC_X509_NONE, enc.SEC_X509_VNC,
                                 enc.SEC_X509_PLAIN])
        self.assertEqual(len(logs), 1)
        self.assertIn("RA2ne", logs[0])

    def test_security_types_errors(self):
        with self.assertRaises(ValueError):
            parse_security_types("RA2,DH")           # nothing usable
        with self.assertRaises(ValueError):
            parse_security_types("VncAuth,Bogus")    # nobody knows this

    def test_unsupported_preferred_encoding_falls_back(self):
        from types import SimpleNamespace
        from sagatvnc.viewer.app import ViewerApp
        opts = parse_args(["-PreferredEncoding=H.264", "-AutoSelect=0"])
        co = ViewerApp.connection_options(SimpleNamespace(opts=opts))
        self.assertEqual(co.preferred_encoding, enc.TIGHT)
        self.assertFalse(co.auto_select)
        opts = parse_args(["-PreferredEncoding=Nonsense"])
        with self.assertRaises(ValueError):
            ViewerApp.connection_options(SimpleNamespace(opts=opts))


class KeysymTest(unittest.TestCase):
    def test_mapping(self):
        self.assertEqual(event_to_keysym("a", 97, "a"), 97)
        self.assertEqual(event_to_keysym("Return", 0xFF0D, "\r"), 0xFF0D)
        self.assertEqual(event_to_keysym("KP_1", 0xFFB1, "1"), 0xFFB1)
        self.assertEqual(event_to_keysym("c", 99, "\x03"), 99)   # Ctrl+C
        self.assertEqual(event_to_keysym("??", 0, "中"), 0x01004E2D)
        self.assertEqual(event_to_keysym("Win_L", 0, ""), 0xFFEB)


class DecoderTest(unittest.TestCase):
    W, H = 70, 37    # deliberately not tile aligned

    def decode(self, encoding, payload, w=None, h=None, fb=None,
               decoders=None, codec=None):
        w = w or self.W
        h = h or self.H
        fb = fb or FrameBuffer(w, h)
        decoders = decoders or make_decoders()
        s = fs.BytesStream(payload)
        decoders[encoding].decode(0, 0, w, h, s, fb, codec or FULL)
        self.assertEqual(s.pos, len(payload), "decoder left data unread")
        return fb

    def test_pixel_format_roundtrip(self):
        pf = PixelFormat.preferred()
        self.assertEqual(PixelFormat.unpack(pf.pack()), pf)
        self.assertTrue(pf.is888())

    def test_raw(self):
        img = fs.pattern(self.W, self.H)
        fb = self.decode(enc.RAW, fs.encode_raw(img))
        self.assertEqual(bytes(fb.data), img)

    def test_copyrect(self):
        fb = FrameBuffer(8, 8)
        fb.image_rect(0, 0, 8, 8, fs.pattern(8, 8))
        before = fb.get_rect(0, 0, 4, 4)
        s = fs.BytesStream(struct.pack(">HH", 0, 0))
        make_decoders()[enc.COPYRECT].decode(2, 3, 4, 4, s, fb, FULL)
        self.assertEqual(fb.get_rect(2, 3, 4, 4), before)

    def test_rre(self):
        bg, red = b"\x10\x20\x30", b"\xff\0\0"
        payload = struct.pack(">I", 1) + bg + b"\0" + red + b"\0" + \
            struct.pack(">HHHH", 2, 3, 4, 5)
        fb = self.decode(enc.RRE, payload, 10, 10)
        self.assertEqual(fb.get_pixel(0, 0), bg)
        self.assertEqual(fb.get_pixel(2, 3), red)
        self.assertEqual(fb.get_pixel(5, 7), red)
        self.assertEqual(fb.get_pixel(6, 8), bg)

    def test_hextile(self):
        img = fs.pattern(20, 20)
        # tile 0 raw; then tile 1 with bg + 1 coloured subrect;
        # then tiles 2,3 with just bg + fg-coloured subrect
        t = bytearray()
        tile0 = b"".join(img[y * 60:y * 60 + 48] for y in range(16))
        t += b"\x01" + fs.rgbx(tile0)
        t += bytes([2 | 8 | 16]) + b"\x01\x02\x03\0" + b"\x01" + \
            b"\x09\x09\x09\0" + bytes([0x11, 0x00])      # at 1,1 size 1x1
        t += bytes([2 | 4 | 8]) + b"\x05\x05\x05\0" + b"\x07\x07\x07\0" + \
            b"\x01" + bytes([0x00, 0x11])                 # at 0,0 size 2x2
        t += b"\x00"                                      # bg reused
        fb = self.decode(enc.HEXTILE, bytes(t), 20, 20)
        self.assertEqual(fb.get_rect(0, 0, 16, 16), tile0)
        self.assertEqual(fb.get_pixel(16, 0), b"\x01\x02\x03")
        self.assertEqual(fb.get_pixel(17, 1), b"\x09\x09\x09")
        self.assertEqual(fb.get_pixel(0, 16), b"\x07\x07\x07")
        self.assertEqual(fb.get_pixel(1, 17), b"\x07\x07\x07")
        self.assertEqual(fb.get_pixel(2, 18), b"\x05\x05\x05")
        self.assertEqual(fb.get_pixel(19, 19), b"\x05\x05\x05")

    def test_zrle_all_subencodings(self):
        colours = [b"\xff\0\0", b"\0\xff\0", b"\0\0\xff", b"\x80\x80\x80",
                   b"\x10\x20\x30"]
        cases = {
            "raw": fs.pattern(self.W, self.H),
            "solid": b"\x11\x22\x33" * (self.W * self.H),
            "packed": fs.few_colours(self.W, self.H, colours[:2]),
            "plainrle": fs.few_colours(self.W, self.H, colours),
            "palrle": fs.few_colours(self.W, self.H, colours[:3]),
        }
        decoders = make_decoders()
        z = zlib.compressobj()
        for mode, img in cases.items():
            with self.subTest(mode=mode):
                payload = fs.encode_zrle(self.W, self.H, img, mode, z)
                fb = self.decode(enc.ZRLE, payload, decoders=decoders)
                self.assertEqual(bytes(fb.data), img)
        # 4-colour packed palette (2 bits per pixel) and 5-colour (4 bits)
        for n in (4, 5):
            img = fs.few_colours(self.W, self.H, colours[:n])
            payload = fs.encode_zrle(self.W, self.H, img, "packed", z)
            fb = self.decode(enc.ZRLE, payload, decoders=decoders)
            self.assertEqual(bytes(fb.data), img)

    def test_tight(self):
        decoders = make_decoders()
        zs = [zlib.compressobj() for _ in range(4)]
        colours = [b"\xff\0\0", b"\0\xff\0", b"\0\0\xff"]
        cases = [
            ("fill", b"\x01\x02\x03" * (self.W * self.H)),
            ("copy", fs.pattern(self.W, self.H)),
            ("palette", fs.few_colours(self.W, self.H, colours[:2])),
            ("palette", fs.few_colours(self.W, self.H, colours)),
            ("gradient", fs.pattern(self.W, self.H, seed=9)),
            ("copy", fs.pattern(2, 1)),        # < 12 bytes: uncompressed
        ]
        for mode, img in cases:
            with self.subTest(mode=mode, size=len(img)):
                w, h = (2, 1) if len(img) == 6 else (self.W, self.H)
                payload = fs.encode_tight(w, h, img, mode, zs)
                fb = self.decode(enc.TIGHT, payload, w, h, decoders=decoders)
                self.assertEqual(bytes(fb.data), img)


class RecordingHandler:
    def __init__(self, password="pw"):
        self.password = password
        self.updates = []
        self.inited = threading.Event()
        self.updated = threading.Event()
        self.names = []
        self.resizes = []
        self.cut = []
        self.bells = 0

    def log(self, msg):
        pass

    def get_credentials(self, need_username):
        return "", self.password

    def verify_certificate(self, info):
        return False

    def server_init(self, w, h, name):
        self.init = (w, h, name)
        self.inited.set()

    def framebuffer_resized(self, w, h):
        self.resizes.append((w, h))

    def framebuffer_updated(self, rects):
        self.updates.append(rects)
        self.updated.set()

    def bell(self):
        self.bells += 1

    def server_cut_text(self, text):
        self.cut.append(text)

    def name_changed(self, name):
        self.names.append(name)


class EndToEndTest(unittest.TestCase):
    def start(self, password=None, client_password="pw", sec="VncAuth,None"):
        a, b = socket.socketpair()
        self.addCleanup(a.close)
        self.addCleanup(b.close)
        self.server = fs.FakeServer(b, password=password)
        self.handler = RecordingHandler(client_password)
        opts = ConnectionOptions(security_types=parse_security_types(sec))
        self.conn = Connection(a, "test", self.handler, opts)
        self.result = {}

        def run():
            try:
                self.conn.run()
            except Exception as e:
                self.result["error"] = e
        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()

    def finish(self):
        self.conn.close()
        self.thread.join(5)

    def test_session(self):
        self.start(password="pw")
        self.assertTrue(self.server.handshake())
        self.assertTrue(self.handler.inited.wait(5))
        self.assertEqual(self.handler.init, (64, 48, "fake desktop"))
        self.server.wait_for("fbur")
        self.assertEqual(self.server.pixel_format,
                         PixelFormat.preferred().pack())
        # CConnection::updateEncodings() order: pseudo-encodings first,
        # then the preferred encoding, CopyRect, the rest
        e = self.server.encodings
        self.assertEqual(e[0], enc.PSEUDO_CURSOR_WITH_ALPHA)
        i = e.index(enc.TIGHT)
        self.assertEqual(e[i + 1], enc.COPYRECT)
        self.assertNotIn(enc.CORRE, e)
        for p in (enc.PSEUDO_EXTENDED_DESKTOP_SIZE, enc.PSEUDO_FENCE,
                  enc.PSEUDO_CONTINUOUS_UPDATES, enc.PSEUDO_LED_STATE,
                  enc.PSEUDO_QEMU_KEY_EVENT, enc.PSEUDO_EXTENDED_CLIPBOARD):
            self.assertLess(e.index(p), i)

        img = fs.pattern(10, 10)
        name = b"renamed"
        self.server.update([
            self.server.rect(5, 5, 10, 10, enc.RAW, fs.encode_raw(img)),
            self.server.rect(0, 0, 0, 0, enc.PSEUDO_DESKTOP_NAME,
                             struct.pack(">I", len(name)) + name),
        ], last_rect=True)
        m = self.server.wait_for("fbur")
        self.assertEqual(m[1], 1)           # incremental, sent at start
        self.server.sync()
        self.assertEqual(self.conn.fb.get_rect(5, 5, 10, 10), img)
        self.assertEqual(self.handler.names, ["renamed"])

        # Desktop resize via ExtendedDesktopSize
        screens = struct.pack(">B3x", 1) + struct.pack(">IHHHHI", 7, 0, 0,
                                                       80, 60, 0)
        self.server.update([self.server.rect(0, 0, 80, 60,
                                             enc.PSEUDO_EXTENDED_DESKTOP_SIZE,
                                             screens)])
        self.server.sync()
        self.assertEqual(self.handler.resizes, [(80, 60)])
        self.assertTrue(self.conn.supports_set_desktop_size)
        self.conn.set_desktop_size(100, 50)
        self.assertEqual(self.server.wait_for("setsize"), ("setsize", 100, 50))

        # Bell and clipboard
        self.server.send(b"\x02")
        text = b"hello\r\nworld"
        self.server.send(struct.pack(">B3xI", 3, len(text)) + text)

        # Input
        self.conn.key_event(0xFF0D, True)
        self.assertEqual(self.server.wait_for("key"), ("key", 0xFF0D, 1))
        self.conn.pointer_event(500, 3, 1)       # clamped to framebuffer
        self.assertEqual(self.server.wait_for("ptr"), ("ptr", 79, 3, 1))
        self.conn.client_cut_text("café")
        self.assertEqual(self.server.wait_for("cut"), ("cut", "café"))
        self.assertEqual(self.handler.bells, 1)
        self.assertEqual(self.handler.cut, ["hello\nworld"])
        self.finish()
        self.assertNotIn("error", self.result)

    def test_wrong_password(self):
        self.start(password="right", client_password="wrong")
        self.assertFalse(self.server.handshake())
        self.thread.join(5)
        self.assertIsInstance(self.result.get("error"), AuthFailure)
        self.assertIn("Wrong password", str(self.result["error"]))

    def test_server_order_decides_security_type(self):
        # Client lists None first, server prefers VncAuth: as in TigerVNC,
        # the server's order wins.
        self.start(sec="None,VncAuth")
        b = self.server.sock
        b.sendall(b"RFB 003.008\n")
        self.assertEqual(self.server.recv(12), b"RFB 003.008\n")
        b.sendall(b"\x02\x02\x01")
        self.assertEqual(self.server.recv(1), b"\x02")
        self.finish()

    def test_no_auth(self):
        self.start(password=None, sec="None")
        self.assertTrue(self.server.handshake())
        self.assertTrue(self.handler.inited.wait(5))
        self.finish()
        self.assertNotIn("error", self.result)


class PixelHelperTest(unittest.TestCase):
    """The translate/slice helpers against a plain per-pixel reference."""

    @staticmethod
    def reference_unpack(data, pos, w, h, bits):
        rowbytes = (w * bits + 7) // 8
        out = bytearray()
        for r in range(h):
            row = data[pos + r * rowbytes:pos + (r + 1) * rowbytes]
            for x in range(w):
                bit = x * bits
                byte = row[bit // 8]
                out.append((byte >> (8 - bits - bit % 8)) & ((1 << bits) - 1))
        return bytes(out), pos + rowbytes * h

    def test_unpack_indices(self):
        from sagatvnc.rfb.decoders import _unpack_indices
        data = bytes((i * 37 + 11) & 0xFF for i in range(400))
        for bits in (1, 2, 4, 8):
            for w in (1, 3, 7, 8, 9, 17, 64):
                for pos in (0, 3):
                    got, end = _unpack_indices(data, pos, w, 5, bits)
                    self.assertEqual((bytes(got), end),
                                     self.reference_unpack(data, pos, w, 5,
                                                           bits),
                                     (bits, w, pos))
        with self.assertRaises(IndexError):
            _unpack_indices(b"\0" * 3, 0, 8, 4, 1)

    def test_palette_to_rgb(self):
        from sagatvnc.rfb.decoders import _palette_to_rgb
        palette = [b"\x01\x02\x03", b"\xfa\xfb\xfc"]
        self.assertEqual(bytes(_palette_to_rgb(b"\x01\x00\x07", palette)),
                         b"\xfa\xfb\xfc\x01\x02\x03\0\0\0")


if __name__ == "__main__":
    unittest.main()
