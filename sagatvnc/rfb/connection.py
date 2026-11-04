# SagatVNC Viewer - a Python port of the TigerVNC viewer
# Copyright (C) 2026 SagatVNC Viewer contributors
# Based on TigerVNC common/rfb/CConnection.cxx, CMsgReader.cxx,
# CMsgWriter.cxx and vncviewer/CConn.cxx,
# Copyright (C) 2002-2005 RealVNC Ltd., Copyright 2009-2019 Pierre Ossman
# for Cendio AB, Copyright (C) 2011 D. R. Commander, and TigerVNC
# contributors.
#
# This is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
"""The RFB client connection (protocol state machine and message I/O).

This module has no GUI dependencies.  A Connection is driven by calling
``run()`` (normally on a worker thread); it reports to a *handler* object:

    handler.log(msg)
    handler.get_credentials(need_username) -> (username, password)
    handler.verify_certificate(info) -> bool
    handler.server_init(width, height, name)
    handler.framebuffer_resized(width, height)
    handler.framebuffer_updated(rects)      # list of (x, y, w, h)
    handler.bell()
    handler.server_cut_text(text)
    handler.name_changed(name)

Input methods (``key_event``, ``pointer_event``, ...) may be called from
any thread.
"""

import struct
from dataclasses import dataclass, field

from . import encodings as enc
from .decoders import DecodeError, make_decoders
from .framebuffer import FrameBuffer
from .pixelformat import PixelFormat
from .security import AuthFailure, SecurityError, SecurityHandler
from .stream import ConnectionClosed, Stream


class ProtocolError(Exception):
    pass


@dataclass
class ConnectionOptions:
    shared: bool = False
    security_types: list = field(default_factory=lambda: [
        enc.SEC_NAMES[n] for n in enc.DEFAULT_SECURITY.split(",")])
    x509_ca: str = ""
    known_hosts: str = ""
    auto_select: bool = True
    preferred_encoding: int = enc.TIGHT
    custom_compress_level: bool = False
    compress_level: int = 2


@dataclass
class Screen:
    id: int
    x: int
    y: int
    w: int
    h: int
    flags: int


class Connection:
    def __init__(self, sock, host, handler, options=None):
        self.stream = Stream(sock)
        self.host = host
        self.handler = handler
        self.options = options or ConnectionOptions()
        self.fb = None
        self.server_pf = None
        self.name = ""
        self.version = (3, 8)
        self.security = None
        self.decoders = make_decoders()
        self.supports_set_desktop_size = False
        self.screens = []
        self.closed = False
        self.last_encoding = None

    # -- handler shims used by SecurityHandler -------------------------------
    def log(self, msg):
        self.handler.log(msg)

    def get_credentials(self, need_username):
        return self.handler.get_credentials(need_username)

    def verify_certificate(self, info):
        return self.handler.verify_certificate(info)

    # -- main loop ---------------------------------------------------------
    def run(self):
        try:
            self._handshake()
            self._initialise()
            while not self.closed:
                self._read_message()
        except ConnectionClosed:
            if not self.closed:
                raise
        except OSError:
            if not self.closed:
                raise

    def close(self):
        self.closed = True
        try:
            self.stream.sock.close()
        except OSError:
            pass

    # -- handshake ---------------------------------------------------------
    def _handshake(self):
        s = self.stream
        banner = s.read(12)
        if not (banner.startswith(b"RFB ") and banner[7:8] == b"." and
                banner.endswith(b"\n")):
            raise ProtocolError("Server gave invalid protocol version %r" %
                                banner)
        try:
            major, minor = int(banner[4:7]), int(banner[8:11])
        except ValueError:
            raise ProtocolError("Server gave invalid protocol version %r" %
                                banner)
        self.log("Server supports RFB protocol version %d.%d" % (major, minor))
        if (major, minor) < (3, 3):
            raise ProtocolError("Server gave unsupported RFB protocol "
                                "version %d.%d" % (major, minor))
        if (major, minor) >= (3, 8):
            self.version = (3, 8)
        elif (major, minor) >= (3, 7):
            self.version = (3, 7)
        else:
            self.version = (3, 3)
        s.write(b"RFB %03d.%03d\n" % self.version)
        self.log("Using RFB protocol version %d.%d" % self.version)

        enabled = [t for t in self.options.security_types if t < 256]
        if any(t >= 256 for t in self.options.security_types) and \
                enc.SEC_VENCRYPT not in enabled:
            enabled.append(enc.SEC_VENCRYPT)

        if self.version == (3, 3):
            sec = s.u32()
            if sec == enc.SEC_INVALID:
                raise ProtocolError(self._read_reason())
            if sec not in enabled:
                raise SecurityError("Server security type %s is not enabled"
                                    % enc.sec_name(sec))
        else:
            n = s.u8()
            if n == 0:
                raise ProtocolError(self._read_reason())
            offered = list(s.read(n))
            # Honour the client's preference order, like TigerVNC does.
            sec = next((t for t in enabled if t in offered), None)
            if sec is None:
                s.write(b"\0")
                raise SecurityError(
                    "No matching security types (server offers %s)" %
                    ", ".join(enc.sec_name(t) for t in offered))
            s.write(bytes([sec]))
        self.log("Choosing security type %s (%d)" % (enc.sec_name(sec), sec))

        self.security = SecurityHandler(self)
        self.security.run(sec)

        if sec != enc.SEC_NONE or self.version == (3, 8):
            result = s.u32()
            if result != 0:
                reason = "Authentication failure"
                if self.version == (3, 8):
                    reason = self._read_reason()
                raise AuthFailure(reason)

    def _read_reason(self):
        length = self.stream.u32()
        if length > 1024 * 1024:
            raise ProtocolError("Reason string is too long")
        return self.stream.read(length).decode("utf-8", "replace")

    def _initialise(self):
        s = self.stream
        s.write(bytes([1 if self.options.shared else 0]))
        width, height = s.u16(), s.u16()
        self.server_pf = PixelFormat.unpack(s.read(16))
        name_len = s.u32()
        self.name = s.read(name_len).decode("utf-8", "replace")
        self.log("Desktop name \"%s\"" % self.name)
        self.log("Geometry %dx%d, server pixel format %s" %
                 (width, height, self.server_pf))
        self.fb = FrameBuffer(width, height)
        self.handler.server_init(width, height, self.name)
        self.set_pixel_format(PixelFormat.preferred())
        self.set_encodings()
        self.request_update(incremental=False)

    # -- server messages ---------------------------------------------------
    def _read_message(self):
        s = self.stream
        msg = s.u8()
        if msg == enc.MSG_FRAMEBUFFER_UPDATE:
            self._framebuffer_update()
        elif msg == enc.MSG_SET_COLOUR_MAP_ENTRIES:
            s.skip(1)
            s.u16()
            count = s.u16()
            s.skip(count * 6)
        elif msg == enc.MSG_BELL:
            self.handler.bell()
        elif msg == enc.MSG_SERVER_CUT_TEXT:
            s.skip(3)
            length = s.s32()
            if length < 0:
                # Extended clipboard; we never advertise it
                s.skip(-length)
                return
            if length > 256 * 1024 * 1024:
                raise ProtocolError("Clipboard data too large")
            text = s.read(length).decode("latin-1")
            self.handler.server_cut_text(text.replace("\r\n", "\n"))
        else:
            raise ProtocolError("Unknown message type %d" % msg)

    def _framebuffer_update(self):
        s = self.stream
        s.skip(1)
        count = s.u16()
        damage = []
        i = 0
        while count == 0xFFFF or i < count:
            i += 1
            x, y, w, h = struct.unpack(">HHHH", s.read(8))
            encoding = s.s32()
            if encoding == enc.PSEUDO_LAST_RECT:
                break
            if encoding == enc.PSEUDO_DESKTOP_SIZE:
                self._resize(w, h)
                damage.append((0, 0, w, h))
                continue
            if encoding == enc.PSEUDO_EXTENDED_DESKTOP_SIZE:
                if self._extended_desktop_size(x, y, w, h):
                    damage.append((0, 0, w, h))
                continue
            if encoding == enc.PSEUDO_DESKTOP_NAME:
                length = s.u32()
                self.name = s.read(length).decode("utf-8", "replace")
                self.handler.name_changed(self.name)
                continue
            decoder = self.decoders.get(encoding)
            if decoder is None:
                raise ProtocolError("Unknown encoding %d" % encoding)
            if x + w > self.fb.width or y + h > self.fb.height:
                raise ProtocolError("Rect too big: %dx%d at %d,%d exceeds "
                                    "%dx%d" % (w, h, x, y, self.fb.width,
                                               self.fb.height))
            if encoding != enc.COPYRECT:
                self.last_encoding = encoding
            with self.fb.lock:
                try:
                    decoder.decode(x, y, w, h, s, self.fb)
                except DecodeError as e:
                    raise ProtocolError(str(e))
            damage.append((x, y, w, h))
        self.handler.framebuffer_updated(damage)
        self.request_update(incremental=True)

    def _resize(self, w, h):
        self.fb.resize(w, h)
        self.handler.framebuffer_resized(w, h)

    def _extended_desktop_size(self, reason, status, w, h):
        s = self.stream
        n = s.u8()
        s.skip(3)
        screens = []
        for _ in range(n):
            sid, sx, sy, sw, sh, flags = struct.unpack(">IHHHHI", s.read(16))
            screens.append(Screen(sid, sx, sy, sw, sh, flags))
        self.supports_set_desktop_size = True
        if reason == 1 and status != 0:
            self.log("SetDesktopSize failed: %d" % status)
            return False
        self.screens = screens
        if w != self.fb.width or h != self.fb.height:
            self._resize(w, h)
            return True
        return False

    # -- client messages ---------------------------------------------------
    def set_pixel_format(self, pf):
        self.stream.write(bytes([enc.MSG_SET_PIXEL_FORMAT, 0, 0, 0]) +
                          pf.pack())

    def encodings_list(self):
        o = self.options
        order = [enc.COPYRECT]
        preferred = enc.TIGHT if o.auto_select else o.preferred_encoding
        order.append(preferred)
        order += [e for e in enc.SUPPORTED if e != preferred]
        order += [enc.PSEUDO_DESKTOP_SIZE, enc.PSEUDO_EXTENDED_DESKTOP_SIZE,
                  enc.PSEUDO_LAST_RECT, enc.PSEUDO_DESKTOP_NAME]
        if o.custom_compress_level:
            order.append(enc.PSEUDO_COMPRESS_LEVEL_0 +
                         max(0, min(9, o.compress_level)))
        return order

    def set_encodings(self):
        codes = self.encodings_list()
        self.stream.write(struct.pack(">BxH", enc.MSG_SET_ENCODINGS,
                                      len(codes)) +
                          b"".join(struct.pack(">i", c) for c in codes))

    def request_update(self, incremental=True, rect=None):
        x, y, w, h = rect or (0, 0, self.fb.width, self.fb.height)
        self.stream.write(struct.pack(">BBHHHH",
                                      enc.MSG_FRAMEBUFFER_UPDATE_REQUEST,
                                      1 if incremental else 0, x, y, w, h))

    def key_event(self, keysym, down):
        self.stream.write(struct.pack(">BBxxI", enc.MSG_KEY_EVENT,
                                      1 if down else 0, keysym & 0xFFFFFFFF))

    def pointer_event(self, x, y, button_mask):
        x = max(0, min(x, self.fb.width - 1))
        y = max(0, min(y, self.fb.height - 1))
        self.stream.write(struct.pack(">BBHH", enc.MSG_POINTER_EVENT,
                                      button_mask & 0xFF, x, y))

    def client_cut_text(self, text):
        data = text.replace("\r\n", "\n").encode("latin-1", "replace")
        self.stream.write(struct.pack(">BxxxI", enc.MSG_CLIENT_CUT_TEXT,
                                      len(data)) + data)

    def set_desktop_size(self, w, h):
        """Ask the server to change size (ExtendedDesktopSize servers only)."""
        if not self.supports_set_desktop_size:
            return False
        if len(self.screens) == 1 or not self.screens:
            sid = self.screens[0].id if self.screens else 0
            flags = self.screens[0].flags if self.screens else 0
            layout = [Screen(sid, 0, 0, w, h, flags)]
        else:
            # Keep things simple: one screen covering everything
            layout = [Screen(self.screens[0].id, 0, 0, w, h,
                             self.screens[0].flags)]
        msg = struct.pack(">BxHHBx", enc.MSG_SET_DESKTOP_SIZE, w, h,
                          len(layout))
        for sc in layout:
            msg += struct.pack(">IHHHHI", sc.id, sc.x, sc.y, sc.w, sc.h,
                               sc.flags)
        self.stream.write(msg)
        return True
