# SagatVNC Viewer tests
# Copyright (C) 2026 SagatVNC Viewer contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Servers that accept the connection but never answer must not leave the
viewer waiting forever (ConnectTimeout; upstream has no limit)."""

import socket
import threading
import time
import unittest

from sagatvnc.rfb.connection import (Connection, ConnectionOptions,
                                     ServerTimeout)
from sagatvnc.rfb.security import connect_socket, parse_security_types

from tests.test_rfb import RecordingHandler


class SilentServerTest(unittest.TestCase):
    def run_against(self, script, timeout=0.3):
        a, b = socket.socketpair()
        self.addCleanup(a.close)
        self.addCleanup(b.close)
        status = []
        handler = RecordingHandler()
        handler.status = status.append
        conn = Connection(a, "t", handler, ConnectionOptions(
            security_types=parse_security_types("None"),
            handshake_timeout=timeout))
        threading.Thread(target=script, args=(b,), daemon=True).start()
        t = time.monotonic()
        with self.assertRaises(ServerTimeout) as cm:
            conn.run()
        return str(cm.exception), time.monotonic() - t, status

    def test_no_greeting(self):
        msg, took, status = self.run_against(lambda b: None)
        self.assertIn("did not answer within", msg)
        self.assertIn("the server's greeting", msg)
        self.assertLess(took, 3)
        self.assertEqual(status, ["Waiting for the server to answer..."])

    def test_stuck_after_the_version(self):
        def server(b):
            b.sendall(b"RFB 003.008\n")
            b.recv(12)             # then nothing: no security types
        msg, _, status = self.run_against(server)
        self.assertIn("the list of security types", msg)
        self.assertEqual(status[-1], "Negotiating security...")

    def test_no_timeout_once_the_session_runs(self):
        """Sessions may sit idle for hours."""
        from tests import fakeserver as fs
        a, b = socket.socketpair()
        self.addCleanup(a.close)
        self.addCleanup(b.close)
        conn = Connection(a, "t", RecordingHandler(), ConnectionOptions(
            security_types=parse_security_types("None"),
            handshake_timeout=0.2))
        server = fs.FakeServer(b, width=8, height=8)
        t = threading.Thread(target=conn.run, daemon=True)
        t.start()
        server.handshake()
        server.wait_for("fbur")
        time.sleep(0.6)            # three times the handshake timeout
        self.assertTrue(t.is_alive())
        self.assertIsNone(a.gettimeout())
        conn.close()
        t.join(5)


class ConnectSocketTest(unittest.TestCase):
    def test_refused_port_fails_at_once(self):
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()                  # nobody listens there now
        t = time.monotonic()
        with self.assertRaises(OSError):
            connect_socket("127.0.0.1", port, timeout=5)
        self.assertLess(time.monotonic() - t, 5)

    def test_socket_is_registered_for_cancelling(self):
        srv = socket.create_server(("127.0.0.1", 0))
        self.addCleanup(srv.close)
        seen = []
        sock = connect_socket("127.0.0.1", srv.getsockname()[1], timeout=5,
                              register=seen.append)
        self.addCleanup(sock.close)
        self.assertEqual(seen, [sock])
        self.assertIsNone(sock.gettimeout())


if __name__ == "__main__":
    unittest.main()
