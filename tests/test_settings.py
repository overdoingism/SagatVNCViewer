# SagatVNC Viewer tests
# Copyright (C) 2026 SagatVNC Viewer contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Saving settings from the F8 menu, the encoding choices, the icon."""

import base64
import os
import shutil
import struct
import sys
import tempfile
import unittest

from sagatvnc.rfb import encodings as enc
from sagatvnc.viewer import configfile, icon_data
from sagatvnc.viewer.parameters import Options, parse_args

from tests.test_upstream_fidelity import Base

# Real encodings (VMware pseudo-encodings are positive numbers too)
REAL = set(enc.NAMES.values())
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class SaveTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.path = os.path.join(self.dir, "x.tigervnc")

    def test_keeps_parameters_it_does_not_know(self):
        """A file shared with TigerVNC keeps its TigerVNC-only lines."""
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(configfile.IDENTIFIER_STRING + "\n\n"
                    "ServerName=old:1\n"
                    "# my comment\n"
                    "FullscreenSystemKeys=0\n"
                    "DPI=150\n"
                    "QualityLevel=3\n")
        opts = Options()
        configfile.load(self.path, opts)
        opts.qualitylevel = 7
        configfile.save(self.path, "new:2", opts)
        with open(self.path, encoding="utf-8") as f:
            lines = f.read().splitlines()
        self.assertEqual(lines[0], configfile.IDENTIFIER_STRING)
        self.assertIn("ServerName=new:2", lines)
        self.assertNotIn("ServerName=old:1", lines)
        self.assertIn("FullscreenSystemKeys=0", lines)
        self.assertIn("# my comment", lines)
        # Known parameters are rewritten, aliases under the real name
        self.assertIn("RemoteDpiScale=150", lines)
        self.assertNotIn("DPI=150", lines)
        self.assertEqual([x for x in lines if x.startswith("Quality")],
                         ["QualityLevel=7"])
        self.assertEqual(os.listdir(self.dir), ["x.tigervnc"])  # no .tmp

    def test_failed_save_raises(self):
        bad = os.path.join(self.dir, "missing", "x.tigervnc")
        with self.assertRaises(configfile.ConfigFileError):
            configfile.save(bad, "h", Options())
        self.assertFalse(os.path.exists(os.path.dirname(bad)))

    def test_menu_settings_roundtrip(self):
        opts = parse_args(["-PreferredEncoding=Hextile", "-NoJPEG",
                           "-CustomCompressLevel", "-CompressLevel=9",
                           "-AutoSelect=0", "-Scaling=Off"])
        configfile.save(self.path, "h:1", opts)
        loaded = Options()
        configfile.load(self.path, loaded)
        for name in ("preferredencoding", "nojpeg", "customcompresslevel",
                     "compresslevel", "autoselect", "scaling"):
            self.assertEqual(getattr(loaded, name), getattr(opts, name),
                             name)


class EncodingChoiceTest(Base):
    def test_menu_encoding_change(self):
        """CConn::handleOptions() -> CConnection::updateEncodings(): the
        preferred encoding goes first (after the pseudo-encodings)."""
        self.session(auto_select=True)
        self.conn.change_settings(
            auto_select=False, preferred_encoding=enc.ZRLE, no_jpeg=True,
            custom_compress_level=True, compress_level=9)
        self.server.update([])
        self.server.sync()
        codes = self.server.encodings
        real = [c for c in codes if c in REAL]
        self.assertEqual(real[0], enc.ZRLE)
        self.assertEqual(real[1], enc.COPYRECT)
        self.assertIn(enc.PSEUDO_COMPRESS_LEVEL_0 + 9, codes)
        self.assertFalse([c for c in codes
                          if enc.PSEUDO_QUALITY_LEVEL_0 <= c <=
                          enc.PSEUDO_QUALITY_LEVEL_0 + 9])

    def test_auto_select_always_tight(self):
        """CConn::autoSelectFormatAndEncoding(): "Always use Tight" """
        self.session(auto_select=False, preferred_encoding=enc.RAW)
        self.assertEqual([c for c in self.server.encodings if c in REAL][0],
                         enc.RAW)
        self.conn.change_settings(auto_select=True)
        self.server.update([])
        self.server.sync()
        self.assertEqual([c for c in self.server.encodings if c in REAL][0],
                         enc.TIGHT)


class TitleStatsTest(Base):
    def test_format_rate(self):
        from sagatvnc.viewer.app import format_rate
        self.assertEqual(format_rate(0), "0.0 KiB/s")
        self.assertEqual(format_rate(1536), "1.5 KiB/s")
        self.assertEqual(format_rate(1024 * 1024 - 1), "1024.0 KiB/s")
        self.assertEqual(format_rate(3 * 1024 * 1024), "3.0 MiB/s")

    def test_update_encodings_summary(self):
        self.session()
        self.assertEqual(self.conn.last_update_encodings, "")
        f = self.server
        sent = self.conn.stream.bytes_sent
        self.assertGreater(sent, 0)          # SetEncodings, update request
        f.update([f.rect(0, 0, 4, 4, enc.TIGHT, bytes([0x80, 1, 2, 3])),
                  f.rect(4, 0, 4, 4, enc.RAW, bytes(64)),
                  f.rect(8, 0, 4, 4, enc.COPYRECT, bytes(4))])
        f.sync()
        self.assertEqual(self.conn.last_update_encodings, "Tight/Raw")
        f.update([f.rect(8, 0, 4, 4, enc.COPYRECT, bytes(4))])
        f.sync()
        self.assertEqual(self.conn.last_update_encodings, "CopyRect")
        # An update that draws nothing keeps the last summary
        f.update([])
        f.sync()
        self.assertEqual(self.conn.last_update_encodings, "CopyRect")
        self.assertGreater(self.conn.stream.bytes_sent, sent)

    def test_rust_switch(self):
        from sagatvnc.rfb import accel
        was = accel.lib
        self.addCleanup(accel.set_enabled, was is not None)
        accel.set_enabled(False)
        self.assertIsNone(accel.lib)
        accel.set_enabled(True)
        self.assertEqual(accel.lib is not None, accel.available())


class IconTest(unittest.TestCase):
    def test_pngs_have_their_sizes(self):
        for size, data in icon_data.PNGS.items():
            png = base64.b64decode("".join(data))
            self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
            self.assertEqual(struct.unpack(">II", png[16:24]), (size, size))

    def test_module_matches_the_drawing(self):
        """icon_data.py is regenerated after make_icon.py changes"""
        sys.path.insert(0, os.path.join(ROOT, "packaging"))
        try:
            import make_icon
        finally:
            sys.path.pop(0)
        self.assertEqual(tuple(sorted(icon_data.PNGS)), make_icon.APP_SIZES)
        for size in (16, 24):
            self.assertEqual(base64.b64decode("".join(icon_data.PNGS[size])),
                             make_icon.png(size))


if __name__ == "__main__":
    unittest.main()
