# SagatVNC Viewer test helpers
# Copyright (C) 2026 SagatVNC Viewer contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Encoders and a tiny scripted RFB server, used to exercise the viewer."""

import os
import struct
import zlib

from sagatvnc.rfb.des import vnc_auth_response


def rgbx(rgb_pixels):
    """RGB bytes -> wire pixels for PixelFormat.preferred() (R,G,B,X)."""
    out = bytearray()
    for i in range(0, len(rgb_pixels), 3):
        out += rgb_pixels[i:i + 3] + b"\0"
    return bytes(out)


def pattern(w, h, seed=0):
    """Deterministic RGB test image."""
    return bytes(((x * 7 + y * 3 + seed + c * 50) & 0xFF)
                 for y in range(h) for x in range(w) for c in range(3))


def few_colours(w, h, colours):
    return b"".join(colours[(x // 3 + y) % len(colours)]
                    for y in range(h) for x in range(w))


class BytesStream:
    """Stream-compatible reader over a bytes object."""

    def __init__(self, data):
        self.data = data
        self.pos = 0

    def read(self, n):
        if self.pos + n > len(self.data):
            raise EOFError("read past end")
        d = self.data[self.pos:self.pos + n]
        self.pos += n
        return d

    def u8(self):
        return self.read(1)[0]

    def u16(self):
        return struct.unpack(">H", self.read(2))[0]

    def u32(self):
        return struct.unpack(">I", self.read(4))[0]

    def s32(self):
        return struct.unpack(">i", self.read(4))[0]

    def skip(self, n):
        self.read(n)

    @property
    def position(self):
        return self.pos

    def read_jpeg_entropy(self):
        d, i = self.data, self.pos
        while True:
            i = d.index(b"\xff", i)
            nxt = d[i + 1]
            if nxt == 0x00 or 0xD0 <= nxt <= 0xD7:
                i += 2
                continue
            out, self.pos = d[self.pos:i], i
            return out


# -- encoders -----------------------------------------------------------------
def encode_raw(rgb):
    return rgbx(rgb)


def encode_zrle(w, h, rgb, mode, zobj):
    """ZRLE with every tile using one subencoding ``mode``."""
    raw = bytearray()
    for ty in range(0, h, 64):
        th = min(64, h - ty)
        for tx in range(0, w, 64):
            tw = min(64, w - tx)
            px = [rgb[((ty + yy) * w + tx + xx) * 3:
                      ((ty + yy) * w + tx + xx) * 3 + 3]
                  for yy in range(th) for xx in range(tw)]
            raw += _zrle_tile(px, tw, th, mode)
    data = zobj.compress(bytes(raw)) + zobj.flush(zlib.Z_SYNC_FLUSH)
    return struct.pack(">I", len(data)) + data


def _runs(px):
    runs = []
    for p in px:
        if runs and runs[-1][0] == p:
            runs[-1][1] += 1
        else:
            runs.append([p, 1])
    return runs


def _run_len(n):
    n -= 1
    out = bytearray()
    while n >= 255:
        out.append(255)
        n -= 255
    out.append(n)
    return out


def _zrle_tile(px, tw, th, mode):
    palette = list(dict.fromkeys(px))
    if mode == "raw":
        return b"\x00" + b"".join(px)
    if mode == "solid":
        assert len(palette) == 1
        return b"\x01" + palette[0]
    if mode == "packed":
        n = len(palette)
        bits = 1 if n <= 2 else 2 if n <= 4 else 4
        out = bytearray([n]) + b"".join(palette)
        for y in range(th):
            acc = 0
            nb = 0
            for x in range(tw):
                acc = (acc << bits) | palette.index(px[y * tw + x])
                nb += bits
                if nb == 8:
                    out.append(acc)
                    acc = nb = 0
            if nb:
                out.append(acc << (8 - nb))
        return bytes(out)
    if mode == "plainrle":
        out = bytearray(b"\x80")
        for p, n in _runs(px):
            out += p + _run_len(n)
        return bytes(out)
    if mode == "palrle":
        out = bytearray([128 + len(palette)]) + b"".join(palette)
        for p, n in _runs(px):
            i = palette.index(p)
            if n == 1:
                out.append(i)
            else:
                out.append(i | 128)
                out += _run_len(n)
        return bytes(out)
    raise ValueError(mode)


def _compact_len(n):
    out = bytearray([n & 0x7F])
    if n > 0x7F:
        out[0] |= 0x80
        out.append((n >> 7) & 0x7F)
        if n > 0x3FFF:
            out[1] |= 0x80
            out.append((n >> 14) & 0xFF)
    return bytes(out)


def encode_tight(w, h, rgb, mode, zobjs):
    if mode == "fill":
        return bytes([0x80]) + rgb[:3]
    if mode == "copy":
        data = rgb
        head = bytes([0x00])                      # stream 0, no filter
        sid = 0
    elif mode == "palette":
        palette = list(dict.fromkeys(rgb[i:i + 3]
                                     for i in range(0, len(rgb), 3)))
        idx = [palette.index(rgb[i:i + 3]) for i in range(0, len(rgb), 3)]
        if len(palette) <= 2:
            data = bytearray()
            for y in range(h):
                acc = nb = 0
                for x in range(w):
                    acc = (acc << 1) | idx[y * w + x]
                    nb += 1
                    if nb == 8:
                        data.append(acc)
                        acc = nb = 0
                if nb:
                    data.append(acc << (8 - nb))
            data = bytes(data)
        else:
            data = bytes(idx)
        head = bytes([0x50, 1, len(palette) - 1]) + b"".join(palette)
        sid = 1
    elif mode == "gradient":
        stride = w * 3
        data = bytearray(len(rgb))
        for y in range(h):
            for x in range(w):
                for c in range(3):
                    o = y * stride + x * 3 + c
                    left = rgb[o - 3] if x else 0
                    up = rgb[o - stride] if y else 0
                    ul = rgb[o - stride - 3] if x and y else 0
                    p = max(0, min(255, left + up - ul))
                    data[o] = (rgb[o] - p) & 0xFF
        data = bytes(data)
        head = bytes([0x60, 2])
        sid = 2
    else:
        raise ValueError(mode)
    if len(data) < 12:
        return head + data
    z = zobjs[sid].compress(data) + zobjs[sid].flush(zlib.Z_SYNC_FLUSH)
    return head + _compact_len(len(z)) + z


# -- scripted server ----------------------------------------------------------
class FakeServer:
    """Speaks just enough RFB 3.8 for the end-to-end tests.

    Runs on a plain socket in a thread; records what the client sends.
    """

    def __init__(self, sock, width=64, height=48, name="fake desktop",
                 password=None):
        self.sock = sock
        self.width = width
        self.height = height
        self.name = name
        self.password = password
        self.received = []
        self.buf = b""
        self.pixel_format = None
        self.encodings = None
        self.error = None

    def recv(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise EOFError
            self.buf += chunk
        d, self.buf = self.buf[:n], self.buf[n:]
        return d

    def send(self, data):
        self.sock.sendall(data)

    def handshake(self):
        self.send(b"RFB 003.008\n")
        assert self.recv(12) == b"RFB 003.008\n"
        if self.password is None:
            self.send(b"\x01\x01")
            assert self.recv(1) == b"\x01"
        else:
            self.send(b"\x01\x02")
            assert self.recv(1) == b"\x02"
            challenge = os.urandom(16)
            self.send(challenge)
            resp = self.recv(16)
            if resp != vnc_auth_response(self.password, challenge):
                reason = b"Wrong password"
                self.send(struct.pack(">II", 1, len(reason)) + reason)
                return False
        self.send(struct.pack(">I", 0))
        self.shared = self.recv(1)
        name = self.name.encode()
        # Server's own default format: 16bpp, to check the viewer overrides it
        pf = struct.pack(">BBBBHHHBBB3x", 16, 16, 0, 1, 31, 63, 31, 11, 5, 0)
        self.send(struct.pack(">HH", self.width, self.height) + pf +
                  struct.pack(">I", len(name)) + name)
        return True

    def read_client_message(self):
        t = self.recv(1)[0]
        if t == 0:
            self.recv(3)
            self.pixel_format = self.recv(16)
            return ("pf", self.pixel_format)
        if t == 2:
            self.recv(1)
            n = struct.unpack(">H", self.recv(2))[0]
            self.encodings = list(struct.unpack(">%di" % n, self.recv(4 * n)))
            return ("enc", self.encodings)
        if t == 3:
            inc, x, y, w, h = struct.unpack(">BHHHH", self.recv(9))
            return ("fbur", inc, x, y, w, h)
        if t == 4:
            down, _, ks = struct.unpack(">BHI", self.recv(7))
            return ("key", ks, down)
        if t == 5:
            mask, x, y = struct.unpack(">BHH", self.recv(5))
            return ("ptr", x, y, mask)
        if t == 6:
            self.recv(3)
            n = struct.unpack(">i", self.recv(4))[0]
            if n < 0:                      # extended clipboard
                flags = struct.unpack(">I", self.recv(4))[0]
                return ("extclip", flags, self.recv(-n - 4))
            return ("cut", self.recv(n).decode("latin-1"))
        if t == 248:
            self.recv(3)
            flags, n = struct.unpack(">IB", self.recv(5))
            return ("fence", flags, self.recv(n))
        if t == 150:
            enable, x, y, w, h = struct.unpack(">BHHHH", self.recv(9))
            return ("cu", enable, x, y, w, h)
        if t == 255:
            sub, down, ks, kc = struct.unpack(">BHII", self.recv(11))
            return ("qemukey", ks, kc, down)
        if t == 251:
            self.recv(1)
            w, h, n = struct.unpack(">HHBx", self.recv(6))
            self.recv(16 * n)
            return ("setsize", w, h)
        raise ValueError("unknown client message %d" % t)

    def wait_for(self, kind):
        while True:
            m = self.read_client_message()
            self.received.append(m)
            if m[0] == kind:
                return m

    def sync(self):
        """Wait until the client has fully processed what we sent.

        The viewer asks for the next update when an update *starts*
        (TigerVNC framebufferUpdateStart), so seeing that request doesn't
        mean the update was decoded.  A fence request is answered only
        once everything before it has been processed."""
        self.sync_count = getattr(self, "sync_count", 0) + 1
        token = b"sync%d" % self.sync_count
        self.send(struct.pack(">B3xIB", 248, 1 << 31, len(token)) + token)
        while True:
            m = self.wait_for("fence")
            if m[2] == token:
                return m

    @staticmethod
    def rect(x, y, w, h, encoding, payload=b""):
        return struct.pack(">HHHHi", x, y, w, h, encoding) + payload

    def update(self, rects, last_rect=False):
        count = 0xFFFF if last_rect else len(rects)
        msg = struct.pack(">BxH", 0, count) + b"".join(rects)
        if last_rect:
            msg += self.rect(0, 0, 0, 0, -224)
        self.send(msg)
