# SagatVNC Viewer - a Python port of the TigerVNC viewer
# Copyright (C) 2026 SagatVNC Viewer contributors
#
# This is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
"""LogLevel and where the log goes (sagatvnc/logfile.py)."""

import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

from sagatvnc import logfile
from sagatvnc.viewer.parameters import parse_args


class LogFileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        saved = (sys.stderr, sys.stdout, logfile.level, logfile.path,
                 logfile._file, logfile._windowed)

        def restore():
            logfile._close()
            (sys.stderr, sys.stdout, logfile.level, logfile.path,
             logfile._file, logfile._windowed) = saved
            if logfile._devnull is not None:
                logfile._devnull.close()
                logfile._devnull = None
        self.addCleanup(restore)
        self.first = os.path.join(self.tmp, "log")
        self.second = os.path.join(self.tmp, "settings")
        patcher = mock.patch.object(logfile, "log_dirs",
                                    lambda: [self.first, self.second])
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_default_and_clamp(self):
        self.assertEqual(parse_args([]).loglevel, logfile.NORMAL)
        self.assertEqual(parse_args(["-LogLevel=2"]).loglevel, 2)
        self.assertEqual(logfile.clamp(-1), logfile.OFF)
        self.assertEqual(logfile.clamp(9), logfile.DETAILED)

    def test_next_to_the_program(self):
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(sys, "executable",
                                  os.path.join(self.tmp, "Viewer.exe")):
            self.assertEqual(logfile.program_dir(), self.tmp)
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.assertEqual(os.path.normcase(logfile.program_dir()),
                         os.path.normcase(here))

    def test_windowed_writes_and_rotates(self):
        for n in range(logfile.KEEP + 2):
            logfile.setup(True, logfile.NORMAL)
            print("run %d" % n, file=sys.stderr)
            logfile._close()
        names = sorted(os.listdir(self.first))
        self.assertEqual(names, ["sagatvnc.%d.log" % i
                                 for i in range(1, logfile.KEEP)] +
                         ["sagatvnc.log"])
        with open(os.path.join(self.first, "sagatvnc.log")) as f:
            self.assertEqual(f.read(), "run %d\n" % (logfile.KEEP + 1))

    def test_off_closes_and_on_reopens(self):
        logfile.setup(True, logfile.DETAILED)
        self.assertEqual(logfile.path,
                         os.path.join(self.first, "sagatvnc.log"))
        logfile.set_level(logfile.OFF)
        self.assertIsNone(logfile.path)
        print("dropped", file=sys.stderr)        # goes to the null device
        self.assertEqual(logfile.folder(), self.first)
        logfile.set_level(logfile.NORMAL)
        self.assertTrue(os.path.exists(
            os.path.join(self.first, "sagatvnc.1.log")))
        logfile.set_level(logfile.DETAILED)      # same file stays open
        self.assertFalse(os.path.exists(
            os.path.join(self.first, "sagatvnc.2.log")))

    def test_off_at_start_creates_nothing(self):
        logfile.setup(True, logfile.OFF)
        self.assertFalse(os.path.exists(self.first))

    def test_falls_back_when_the_program_folder_is_read_only(self):
        with open(self.first, "w"):              # a file where the dir goes
            pass
        logfile.setup(True, logfile.NORMAL)
        self.assertEqual(logfile.path,
                         os.path.join(self.second, "sagatvnc.log"))

    def test_console_keeps_stderr(self):
        before = sys.stderr
        logfile.setup(False, logfile.NORMAL)
        self.assertIs(sys.stderr, before)
        self.assertIsNone(logfile.path)
        self.assertFalse(os.path.exists(self.first))


if __name__ == "__main__":
    unittest.main()
