# SagatVNC Viewer tests
# Copyright (C) 2026 SagatVNC Viewer contributors
# SPDX-License-Identifier: GPL-2.0-or-later

import os
import tempfile
import unittest

from sagatvnc.viewer import configfile
from sagatvnc.viewer.parameters import Options, parse_args

# What TigerVNC 1.x on Windows writes from "Save As..." (CRLF line ends),
# including parameters this port does not implement.
TIGERVNC_FILE = (
    "TigerVNC Configuration file Version 1.0\r\n"
    "\r\n"
    "ServerName=myhost.example:1\r\n"
    "SecurityTypes=VncAuth,None\r\n"
    "FullScreen=1\r\n"
    "CompressLevel=6\r\n"
    "CustomCompressLevel=1\r\n"
    "FullScreenMode=All\r\n"
    "CursorType=Dot\r\n"
    "# a comment\r\n"
    "garbage line\r\n"
    r"DesktopSize=\\odd\nvalue" "\r\n"
)


class ConfigFileTest(unittest.TestCase):
    def write(self, text, name="test.tigervnc"):
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: [os.remove(os.path.join(d, f))
                                 for f in os.listdir(d)] and os.rmdir(d))
        path = os.path.join(d, name)
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        return path

    def test_load_tigervnc_file(self):
        path = self.write(TIGERVNC_FILE)
        opts = Options()
        logs = []
        server = configfile.load(path, opts, log=logs.append)
        self.assertEqual(server, "myhost.example:1")
        self.assertEqual(opts.securitytypes, "VncAuth,None")
        self.assertTrue(opts.fullscreen)
        self.assertEqual(opts.compresslevel, 6)
        self.assertTrue(opts.customcompresslevel)
        self.assertEqual(opts.desktopsize, "\\odd\nvalue")
        self.assertEqual(len(logs), 3)    # 2 unsupported + 1 bad line

    def test_file_overrides_command_line(self):
        path = self.write(TIGERVNC_FILE)
        opts = parse_args(["-CompressLevel=1", path])
        self.assertTrue(configfile.looks_like_config_file(opts.server))
        configfile.load(opts.server, opts)
        self.assertEqual(opts.compresslevel, 6)

    def test_bad_identifier(self):
        path = self.write("ServerName=foo\n")
        with self.assertRaises(configfile.ConfigFileError):
            configfile.load(path, Options())

    def test_missing_file(self):
        with self.assertRaises(configfile.ConfigFileError):
            configfile.load(os.path.join(tempfile.gettempdir(),
                                         "nope", "x.tigervnc"), Options())

    def test_looks_like_config_file(self):
        self.assertFalse(configfile.looks_like_config_file("host:1"))
        self.assertTrue(configfile.looks_like_config_file(r"C:\a\b.tigervnc"))
        self.assertTrue(configfile.looks_like_config_file("./b"))

    def test_roundtrip(self):
        opts = parse_args(["-Shared", "-PreferredEncoding=ZRLE"])
        path = self.write("")
        configfile.save(path, "srv\\x\nnl", opts)
        loaded = Options()
        self.assertEqual(configfile.load(path, loaded), "srv\\x\nnl")
        self.assertTrue(loaded.shared)
        self.assertEqual(loaded.preferredencoding, "ZRLE")


if __name__ == "__main__":
    unittest.main()
