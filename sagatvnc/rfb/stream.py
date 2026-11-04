# SagatVNC Viewer - a Python port of the TigerVNC viewer
# Copyright (C) 2026 SagatVNC Viewer contributors
#
# This is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
"""Blocking buffered reader/writer over a socket (rdr::InStream/OutStream)."""

import collections
import struct
import threading
import time


class ConnectionClosed(Exception):
    pass


class Stream:
    def __init__(self, sock):
        self.sock = sock
        self._buf = bytearray()
        self._pos = 0
        self._wlock = threading.Lock()
        self.bytes_received = 0
        # Recent writes, for diagnosing servers that drop the connection
        self.sent_log = collections.deque(maxlen=12)

    # -- reading ----------------------------------------------------------
    def _fill(self, n):
        while len(self._buf) - self._pos < n:
            if self._pos:
                del self._buf[:self._pos]
                self._pos = 0
            chunk = self.sock.recv(max(65536, n))
            if not chunk:
                raise ConnectionClosed("The connection was closed by the server")
            self.bytes_received += len(chunk)
            self._buf += chunk

    def read(self, n):
        if n == 0:
            return b""
        self._fill(n)
        data = bytes(self._buf[self._pos:self._pos + n])
        self._pos += n
        return data

    @property
    def position(self):
        """Total bytes consumed so far (for bandwidth measurement)."""
        return self.bytes_received - (len(self._buf) - self._pos)

    def has_buffered_data(self):
        return len(self._buf) > self._pos

    def u8(self):
        self._fill(1)
        v = self._buf[self._pos]
        self._pos += 1
        return v

    def u16(self):
        return struct.unpack(">H", self.read(2))[0]

    def u32(self):
        return struct.unpack(">I", self.read(4))[0]

    def s32(self):
        return struct.unpack(">i", self.read(4))[0]

    def skip(self, n):
        self.read(n)

    def read_jpeg_entropy(self):
        """Read JPEG entropy-coded data up to (not including) the next
        marker.  0xFF00 (stuffed byte) and RSTn markers are data."""
        out = bytearray()
        while True:
            self._fill(2)
            buf, pos = self._buf, self._pos
            i = buf.find(b"\xff", pos)
            while i >= 0 and i + 1 < len(buf):
                nxt = buf[i + 1]
                if nxt == 0x00 or 0xD0 <= nxt <= 0xD7:
                    i = buf.find(b"\xff", i + 2)
                    continue
                out += buf[pos:i]
                self._pos = i
                return bytes(out)
            # No marker in what we have (or 0xFF is the last byte): keep
            # everything but a trailing 0xFF, then read more.
            end = len(buf) - 1 if buf.endswith(b"\xff") else len(buf)
            out += buf[pos:end]
            self._pos = end
            self._fill(len(buf) - end + 1)

    # -- writing ----------------------------------------------------------
    def write(self, data):
        with self._wlock:
            self.sock.sendall(data)
            self.sent_log.append((time.monotonic(), data[:1], len(data)))

    def replace_socket(self, sock):
        """Swap the transport (used after a TLS handshake)."""
        assert not self.has_buffered_data()
        self.sock = sock
