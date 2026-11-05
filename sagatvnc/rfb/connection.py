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

A port of TigerVNC's CConnection/CMsgReader/CMsgWriter plus the protocol
parts of vncviewer's CConn.  Comments name the upstream function a piece
of code corresponds to; where this port deliberately differs, the comment
says so and why.

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
    # optional:
    handler.clipboard_announce(available)
    handler.set_cursor(width, height, hot_x, hot_y, rgba)
    handler.set_cursor_pos(x, y)
    handler.set_led_state(state)
    handler.set_desktop_size_done(result)

Input methods (``send_key_press``, ``pointer_event``, ...) may be called
from any thread.
"""

import struct
import threading
import time
import zlib
from dataclasses import dataclass, field

from . import encodings as enc
from .decoders import JPEG_AVAILABLE, DecodeError, make_decoders
from .framebuffer import FrameBuffer
from .pixelformat import LOW_COLOUR_LEVELS, PixelCodec, PixelFormat
from .security import AuthFailure, SecurityError, SecurityHandler
from .stream import ConnectionClosed, Stream


class ProtocolError(Exception):
    pass


class DroppedBeforeSession(ProtocolError):
    """CConn: "The connection was dropped by the server before the
    session could be established." """


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
    full_colour: bool = True
    low_colour_level: int = 2      # 0 = 8, 1 = 64, 2 = 256 colours
    quality_level: int = 8         # JPEG quality 0-9 (when not AutoSelect)
    no_jpeg: bool = False
    max_cut_text: int = 256 * 1024     # TigerVNC MaxCutText


MAX_CURSOR_SIZE = 256          # CMsgReader maxCursorSize

# Bandwidth estimation, as in TigerVNC vncviewer/CConn.cxx
BPS_ESTIMATE_WINDOW_MS = 1000
FULL_COLOUR_MIN_BPS = 256000       # below this AutoSelect drops to low colour
HIGH_QUALITY_MIN_BPS = 16000000    # above this (LAN) AutoSelect uses q8

REASON_CLIENT = 1              # ExtendedDesktopSize reason: our request


@dataclass
class Screen:
    id: int
    x: int
    y: int
    w: int
    h: int
    flags: int


def _decode_text(data):
    """Strings from the server: UTF-8, falling back to Latin-1
    (CConnection::processSecurityReasonMsg, processServerInitMsg)."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1")


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
        self.closed = False
        self.last_encoding = None
        # What the last update that drew anything used, e.g. "Tight+JPEG"
        # or "ZRLE/Raw" (for the title bar)
        self.last_update_encodings = ""
        self.session_start = None
        self.update_count = 0

        # ServerParams: what the server told us it supports
        self.supports_set_desktop_size = False
        self.supports_fence = False
        self.supports_continuous_updates = False
        self.supports_qemu_key_event = False
        self.server_led_state = enc.LED_UNKNOWN
        self.screens = []

        # CConnection update/format state (requestNewUpdate & friends).
        # self.pf is the format updates are currently decoded with.
        self.pf = PixelFormat.preferred()
        self.codec = PixelCodec(self.pf)
        self.next_pf = None
        self.format_change = False
        self.pending_pf = None
        self.pending_pf_change = False
        self.encoding_change = False
        self.force_nonincremental = True
        self.pending_update = False
        self.continuous_updates = False
        self.first_update = True
        self.update_lock = threading.RLock()

        # CConn AutoSelect state
        self.bps_estimate = 20000000       # TigerVNC's optimistic start
        self.quality = self._wanted_quality()
        self._update_start = None

        # Keys pressed and the keysym/keycode they sent (CConnection
        # downKeys), keyed by "system key code"
        self.down_keys = {}
        self.key_lock = threading.Lock()

        # Extended clipboard state (CConnection.cxx)
        self.server_clip_flags = 0
        self.server_clip_sizes = {}
        self.local_clipboard = None        # text we may provide on request
        self.server_clipboard = ""
        self.has_remote_clipboard = False
        self.unsolicited_attempt = False

    # -- handler shims used by SecurityHandler -------------------------------
    def log(self, msg):
        self.handler.log(msg)

    def get_credentials(self, need_username):
        return self.handler.get_credentials(need_username)

    def verify_certificate(self, info):
        return self.handler.verify_certificate(info)

    def _call(self, name, *args):
        func = getattr(self.handler, name, None)
        if func is not None:
            return func(*args)
        return None

    # -- main loop ---------------------------------------------------------
    def run(self):
        try:
            self._handshake()
            self._initialise()
            while not self.closed:
                self._read_message()
        except (ConnectionClosed, OSError):
            if self.closed:
                return
            if self.session_start is None:
                raise DroppedBeforeSession(
                    "The connection was dropped by the server before the "
                    "session could be established.")
            raise

    CLIENT_MSG_NAMES = {0: "SetPixelFormat", 2: "SetEncodings",
                        3: "FramebufferUpdateRequest", 4: "KeyEvent",
                        5: "PointerEvent", 6: "ClientCutText",
                        150: "EnableContinuousUpdates", 248: "ClientFence",
                        251: "SetDesktopSize", 255: "QEMUClientMessage"}

    def session_summary(self):
        """One paragraph for the log when a connection ends unexpectedly."""
        if self.session_start is None:
            return "Connection ended before the session started"
        now = time.monotonic()
        lines = ["Session lasted %.1f s, %d updates, %d bytes received, "
                 "pixel format %s, line speed estimate %d kbit/s" %
                 (now - self.session_start, self.update_count,
                  self.stream.bytes_received, self.pf,
                  self.bps_estimate // 1000)]
        sent = list(self.stream.sent_log)
        if sent:
            lines.append("Last messages sent to the server (newest last):")
            for t, kind, n in sent:
                name = self.CLIENT_MSG_NAMES.get(kind[0] if kind else -1,
                                                 "type %r" % kind)
                if self.session_start <= t:
                    lines.append("  -%.2f s  %s (%d bytes)" %
                                 (now - t, name, n))
        return "\n".join(lines)

    def close(self):
        self.closed = True
        try:
            self.stream.sock.close()
        except OSError:
            pass

    # -- handshake (CConnection::processVersionMsg ... processInitMsg) -------
    def _enabled_sec_types(self):
        """Security::GetEnabledSecTypes(): VeNCrypt first if any VeNCrypt
        sub-type is enabled, then the plain (< 256) types in order."""
        types = self.options.security_types
        result = []
        if any(t >= 0x100 for t in types):
            result.append(enc.SEC_VENCRYPT)
        result += [t for t in types if t < 0x100 and t not in result]
        return result

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

        enabled = self._enabled_sec_types()
        if self.version == (3, 3):
            # processSecurityTypesMsg: the server decides; only None and
            # VncAuth exist in 3.3
            sec = s.u32()
            if sec == enc.SEC_INVALID:
                raise ProtocolError(self._read_reason())
            if sec not in (enc.SEC_NONE, enc.SEC_VNC_AUTH):
                raise ProtocolError("Unknown 3.3 security type %d" % sec)
            if sec not in enabled:
                raise SecurityError("Server security type %s is not enabled"
                                    % enc.sec_name(sec))
        else:
            n = s.u8()
            if n == 0:
                raise ProtocolError(self._read_reason())
            offered = list(s.read(n))
            # The server's order specifies priority
            sec = next((t for t in offered if t in enabled), None)
            if sec is None:
                raise SecurityError(
                    "No matching security types (server offers %s)" %
                    ", ".join(enc.sec_name(t) for t in offered))
            s.write(bytes([sec]))
        self.log("Choosing security type %s (%d)" % (enc.sec_name(sec), sec))

        self.security = SecurityHandler(self)
        self.security.run(sec)

        # processSecurityResultMsg
        if self.version < (3, 8) and sec == enc.SEC_NONE:
            return
        self.log("Waiting for the security result")
        result = s.u32()
        if result == 0:
            return
        if result not in (1, 2):
            raise ProtocolError("Unknown security result from server")
        if self.version < (3, 8):
            raise AuthFailure("Authentication failed")
        raise AuthFailure(self._read_reason())

    def _read_reason(self):
        length = self.stream.u32()
        if length > 1024 * 1024:
            raise ProtocolError("Reason string is too long")
        return _decode_text(self.stream.read(length))

    def _initialise(self):
        s = self.stream
        s.write(bytes([1 if self.options.shared else 0]))
        width, height = s.u16(), s.u16()
        self.server_pf = PixelFormat.unpack(s.read(16))
        name_len = s.u32()
        self.name = _decode_text(s.read(name_len))
        self.log("Desktop name \"%s\"" % self.name)
        self.log("Geometry %dx%d, server pixel format %s" %
                 (width, height, self.server_pf))
        self.fb = FrameBuffer(width, height)
        self.handler.server_init(width, height, self.name)
        self.session_start = time.monotonic()
        # Deliberate difference: upstream can decode any server format and
        # only calls setPF() (which it skips for pre-3.8 servers).  This
        # port's decoders only handle the formats it asks for, so it always
        # sends one SetPixelFormat here; set_pf() still refuses later
        # changes on pre-3.8 servers.
        self.pf = self._wanted_pf()
        self.codec = PixelCodec(self.pf)
        self.log("Using pixel format %s" % self.pf)
        self.stream.write(bytes([enc.MSG_SET_PIXEL_FORMAT, 0, 0, 0]) +
                          self.pf.pack())
        self.update_encodings()
        self.request_new_update()

    # -- server messages (CMsgReader::readMsg) --------------------------------
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
            self._read_server_cut_text()
        elif msg == enc.MSG_END_OF_CONTINUOUS_UPDATES:
            self._end_of_continuous_updates()
        elif msg == enc.MSG_SERVER_FENCE:
            self._read_fence()
        else:
            raise ProtocolError("Unknown message type %d" % msg)

    def _read_server_cut_text(self):
        s = self.stream
        s.skip(3)
        length = s.s32()
        if length < 0:
            self._read_extended_clipboard(-length)
            return
        if length > self.options.max_cut_text:
            s.skip(length)
            self.log("Clipboard too large (%d bytes)" % length)
            return
        text = s.read(length).decode("latin-1")
        # CConnection::serverCutText(): keep it, then let the viewer
        # decide (via announce -> request_clipboard) whether to use it
        self.local_clipboard = None
        self.server_clipboard = text.replace("\r\n", "\n")
        self.has_remote_clipboard = True
        self._clipboard_announce(True)

    def _read_fence(self):
        """CConnection::fence(): answer requests, synchronously."""
        s = self.stream
        s.skip(3)
        flags = s.u32()
        length = s.u8()
        if length > 64:
            raise ProtocolError("Too large fence payload")
        data = s.read(length)
        self.supports_fence = True
        if flags & enc.FENCE_REQUEST:
            flags &= enc.FENCE_BLOCK_BEFORE | enc.FENCE_BLOCK_AFTER
            self.stream.write(struct.pack(">BxxxIB", enc.MSG_CLIENT_FENCE,
                                          flags, len(data)) + data)

    def _end_of_continuous_updates(self):
        """CConnection::endOfContinuousUpdates()"""
        self.supports_continuous_updates = True
        with self.update_lock:
            if self.pending_pf_change:
                self._activate_pending_pf()
                if self.format_change:
                    self.request_new_update()

    # -- framebuffer updates -------------------------------------------------
    def _framebuffer_update(self):
        s = self.stream
        s.skip(1)
        count = s.u16()
        self._framebuffer_update_start(s.position - 4)
        damage = []
        used = []
        for _ in range(count):
            x, y, w, h = struct.unpack(">HHHH", s.read(8))
            encoding = s.s32()
            if encoding == enc.PSEUDO_LAST_RECT:
                break
            if self._pseudo_rect(encoding, x, y, w, h, damage):
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
                    decoder.decode(x, y, w, h, s, self.fb, self.codec)
                except DecodeError as e:
                    raise ProtocolError(str(e))
            damage.append((x, y, w, h))
            name = enc.encoding_name(encoding)
            if encoding == enc.TIGHT and decoder.last_jpeg:
                name = "Tight+JPEG"
            if name not in used:
                used.append(name)
        if used:
            # Tight+JPEG covers Tight; CopyRect only if nothing else
            if "Tight+JPEG" in used and "Tight" in used:
                used.remove("Tight")
            if len(used) > 1 and "CopyRect" in used:
                used.remove("CopyRect")
            self.last_update_encodings = "/".join(used)
        self.handler.framebuffer_updated(damage)
        self._framebuffer_update_end()

    def _framebuffer_update_start(self, start_pos):
        """CConn/CConnection::framebufferUpdateStart(): ask for the next
        update now, so it is sent while we decode this one."""
        self._update_start = (time.monotonic(), start_pos)
        with self.update_lock:
            self.pending_update = False
            self.request_new_update()

    def _framebuffer_update_end(self):
        """CConnection::framebufferUpdateEnd() + CConn's AutoSelect."""
        with self.update_lock:
            # Past the update with the old format: switch now
            if self.pending_pf_change and not self.continuous_updates:
                self._activate_pending_pf()
            if self.first_update:
                if self.supports_continuous_updates:
                    self.log("Enabling continuous updates")
                    self.continuous_updates = True
                    self._write_enable_cu(True)
                self.first_update = False
        self.update_count += 1
        t0, p0 = self._update_start
        self._update_bandwidth(self.stream.position - p0,
                               time.monotonic() - t0)
        if self.options.auto_select:
            self._auto_select()

    def _pseudo_rect(self, encoding, x, y, w, h, damage):
        """Pseudo-encodings (CMsgReader::readRect); True if handled."""
        s = self.stream
        if encoding == enc.PSEUDO_DESKTOP_SIZE:
            self._set_desktop_size(w, h)
            damage.append((0, 0, w, h))
        elif encoding == enc.PSEUDO_EXTENDED_DESKTOP_SIZE:
            if self._extended_desktop_size(x, y, w, h):
                damage.append((0, 0, self.fb.width, self.fb.height))
        elif encoding == enc.PSEUDO_DESKTOP_NAME:
            name = s.read(s.u32())
            if x or y or w or h:
                self.log("Invalid desktop name received")
                return True
            try:
                self.name = name.decode("utf-8")
            except UnicodeDecodeError:
                self.log("Invalid desktop name received")
                return True
            self.handler.name_changed(self.name)
        elif encoding == enc.PSEUDO_CURSOR:
            self._read_set_cursor(x, y, w, h)
        elif encoding == enc.PSEUDO_X_CURSOR:
            self._read_set_x_cursor(x, y, w, h)
        elif encoding == enc.PSEUDO_CURSOR_WITH_ALPHA:
            self._read_set_cursor_with_alpha(x, y, w, h)
        elif encoding == enc.PSEUDO_VMWARE_CURSOR:
            self._read_set_vmware_cursor(x, y, w, h)
        elif encoding == enc.PSEUDO_VMWARE_CURSOR_POSITION:
            self._call("set_cursor_pos", x, y)
        elif encoding == enc.PSEUDO_LED_STATE:
            self._set_led_state(s.u8())
        elif encoding == enc.PSEUDO_VMWARE_LED_STATE:
            self._set_led_state(s.u32())    # same bit definitions
        elif encoding == enc.PSEUDO_QEMU_KEY_EVENT:
            self.supports_qemu_key_event = True
            self.log("Server supports QEMU extended key events")
        else:
            return False
        return True

    def _set_led_state(self, state):
        self.server_led_state = state
        self._call("set_led_state", state)

    # -- cursors (CMsgReader::readSetCursor & co.) ----------------------------
    def _check_cursor_size(self, w, h):
        if w > MAX_CURSOR_SIZE or h > MAX_CURSOR_SIZE:
            raise ProtocolError("Received cursor is too large")

    def _read_set_cursor(self, hot_x, hot_y, w, h):
        """Cursor: pixels in the current format, then a 1 bit mask."""
        self._check_cursor_size(w, h)
        pc = self.codec
        pixels = self.stream.read(w * h * pc.bypp)
        mask = self.stream.read(((w + 7) // 8) * h)
        rgb = pc.to_rgb(pixels)
        rgba = bytearray(w * h * 4)
        rgba[0::4] = rgb[0::3]
        rgba[1::4] = rgb[1::3]
        rgba[2::4] = rgb[2::3]
        self._apply_bit_mask(rgba, mask, w, h)
        self._call("set_cursor", w, h, hot_x, hot_y, bytes(rgba))

    @staticmethod
    def _apply_bit_mask(rgba, mask, w, h):
        rowbytes = (w + 7) // 8
        for y in range(h):
            row = mask[y * rowbytes:(y + 1) * rowbytes]
            for x in range(w):
                rgba[(y * w + x) * 4 + 3] = \
                    255 if row[x >> 3] & (0x80 >> (x & 7)) else 0

    def _read_set_x_cursor(self, hot_x, hot_y, w, h):
        """XCursor: foreground/background colour, bitmap and mask."""
        self._check_cursor_size(w, h)
        rgba = bytearray(w * h * 4)
        if w * h:
            colours = self.stream.read(6)
            fg, bg = colours[:3], colours[3:]
            rowbytes = (w + 7) // 8
            data = self.stream.read(rowbytes * h)
            mask = self.stream.read(rowbytes * h)
            for y in range(h):
                for x in range(w):
                    bit = 0x80 >> (x & 7)
                    o = (y * w + x) * 4
                    byte = y * rowbytes + (x >> 3)
                    rgba[o:o + 3] = fg if data[byte] & bit else bg
                    rgba[o + 3] = 255 if mask[byte] & bit else 0
        self._call("set_cursor", w, h, hot_x, hot_y, bytes(rgba))

    def _read_set_cursor_with_alpha(self, hot_x, hot_y, w, h):
        """CursorWithAlpha: an encoding number, then a rect in 32 bpp
        B,G,R,A with premultiplied alpha.  TigerVNC servers always use Raw
        (SMsgWriter::writeSetCursorWithAlphaRect); that is all we take."""
        self._check_cursor_size(w, h)
        encoding = self.stream.s32()
        if encoding != enc.RAW:
            raise ProtocolError("Unsupported CursorWithAlpha encoding %d" %
                                encoding)
        bgra = self.stream.read(w * h * 4)
        rgba = bytearray(w * h * 4)
        for i in range(w * h):
            b, g, r, a = bgra[i * 4:i * 4 + 4]
            div = a or 1                   # undo premultiplied alpha
            rgba[i * 4:i * 4 + 4] = bytes((min(255, r * 255 // div),
                                           min(255, g * 255 // div),
                                           min(255, b * 255 // div), a))
        self._call("set_cursor", w, h, hot_x, hot_y, bytes(rgba))

    def _read_set_vmware_cursor(self, hot_x, hot_y, w, h):
        self._check_cursor_size(w, h)
        kind = self.stream.u8()
        self.stream.skip(1)
        if kind == 0:
            pc = self.codec
            n = w * h * pc.bypp
            and_mask = self.stream.read(n)
            xor_mask = self.stream.read(n)
            bp = pc.bypp
            rgba = bytearray(w * h * 4)
            for i in range(w * h):
                a = and_mask[i * bp:(i + 1) * bp]
                x = xor_mask[i * bp:(i + 1) * bp]
                if not any(a):                       # opaque
                    rgba[i * 4:i * 4 + 4] = pc.pixel(x) + b"\xff"
                elif not any(x):                     # transparent
                    rgba[i * 4:i * 4 + 4] = b"\0\0\0\0"
                else:                                # inverted: black
                    rgba[i * 4:i * 4 + 4] = b"\0\0\0\xff"
        elif kind == 1:
            rgba = bytearray(self.stream.read(w * h * 4))
        else:
            raise ProtocolError("Unknown cursor type")
        self._call("set_cursor", w, h, hot_x, hot_y, bytes(rgba))

    # -- extended clipboard (CMsgReader/CConnection) -------------------------
    def _read_extended_clipboard(self, length):
        s = self.stream
        if length < 4:
            raise ProtocolError("Invalid extended clipboard message")
        if length > self.options.max_cut_text:
            s.skip(length)
            self.log("Clipboard too large (%d bytes)" % length)
            return
        flags = s.u32()
        body = s.read(length - 4)
        action = flags & enc.CLIP_ACTION_MASK
        if action & enc.CLIP_CAPS:
            sizes = {}
            pos = 0
            for i in range(16):
                if flags & (1 << i):
                    if pos + 4 > len(body):
                        raise ProtocolError("Invalid extended clipboard "
                                            "message")
                    sizes[1 << i] = struct.unpack(">I", body[pos:pos + 4])[0]
                    pos += 4
            self.server_clip_flags = flags
            self.server_clip_sizes = sizes
            self.log("Server supports extended clipboard (UTF-8 %s)" %
                     ("yes" if flags & enc.CLIP_UTF8 else "no"))
            # CConnection::clipboardCaps(): plain text, size 0
            self._write_clipboard(enc.CLIP_UTF8 | enc.CLIP_REQUEST |
                                  enc.CLIP_PEEK | enc.CLIP_NOTIFY |
                                  enc.CLIP_PROVIDE | enc.CLIP_CAPS,
                                  struct.pack(">I", 0))
        elif action == enc.CLIP_PROVIDE:
            self._clipboard_provide(flags, body)
        elif action == enc.CLIP_REQUEST:
            # CConnection::clipboardRequest()
            if not flags & enc.CLIP_UTF8:
                self.log("Ignoring clipboard request for unsupported "
                         "formats 0x%x" % flags)
            elif self.local_clipboard is None:
                self.log("Ignoring unexpected clipboard request")
            else:
                self._send_clipboard_data(self.local_clipboard)
        elif action == enc.CLIP_PEEK:
            if self.server_clip_flags & enc.CLIP_NOTIFY:
                self._write_clipboard(
                    enc.CLIP_NOTIFY | (enc.CLIP_UTF8 if self.local_clipboard
                                       is not None else 0))
        elif action == enc.CLIP_NOTIFY:
            # CConnection::clipboardNotify(): the server's clipboard
            # changed; the viewer decides whether to fetch it (TigerVNC's
            # Viewport only does when it has focus).
            self.has_remote_clipboard = False
            if flags & enc.CLIP_UTF8:
                self.local_clipboard = None
                self._clipboard_announce(True)
            else:
                self._clipboard_announce(False)
        else:
            self.log("Unknown extended clipboard action 0x%x" % action)

    def _clipboard_provide(self, flags, body):
        try:
            data = zlib.decompressobj().decompress(body)
        except zlib.error as e:
            raise ProtocolError("Invalid extended clipboard data: %s" % e)
        pos = 0
        text = None
        for i in range(16):
            if not flags & (1 << i):
                continue
            if pos + 4 > len(data):
                raise ProtocolError("Invalid extended clipboard message")
            n = struct.unpack(">I", data[pos:pos + 4])[0]
            chunk = data[pos + 4:pos + 4 + n]
            pos += 4 + n
            if n > self.options.max_cut_text:
                self.log("Clipboard too large (%d bytes)" % n)
                continue
            if (1 << i) == enc.CLIP_UTF8:
                text = chunk
        if text is None:
            return
        try:
            text = text.split(b"\0", 1)[0].decode("utf-8")
        except UnicodeDecodeError:
            self.log("Invalid UTF-8 sequence in clipboard")
            return
        # CConnection::clipboardProvide()
        self.server_clipboard = text.replace("\r\n", "\n")
        self.has_remote_clipboard = True
        self.handler.server_cut_text(self.server_clipboard)

    def _clipboard_announce(self, available):
        """CConn::handleClipboardAnnounce -> the viewer (GUI) side.

        A handler without ``clipboard_announce`` behaves like a viewer that
        always has focus: it fetches straight away.
        """
        announce = getattr(self.handler, "clipboard_announce", None)
        if announce is not None:
            announce(available)
        elif available:
            self.request_clipboard()

    def request_clipboard(self):
        """CConnection::requestClipboard(); callable from any thread."""
        if self.has_remote_clipboard:
            self.handler.server_cut_text(self.server_clipboard)
            return
        if self.server_clip_flags & enc.CLIP_REQUEST:
            self._write_clipboard(enc.CLIP_REQUEST | enc.CLIP_UTF8)

    def _write_clipboard(self, flags, payload=b""):
        self.stream.write(struct.pack(">BxxxiI", enc.MSG_CLIENT_CUT_TEXT,
                                      -(4 + len(payload)), flags) + payload)

    def _send_clipboard_data(self, text):
        """CConnection::sendClipboardData()"""
        if self.server_clip_flags & enc.CLIP_PROVIDE:
            data = text.replace("\r\n", "\n").replace("\n", "\r\n")
            data = data.encode("utf-8") + b"\0"
            if self.unsolicited_attempt:
                self.unsolicited_attempt = False
                if len(data) > self.server_clip_sizes.get(enc.CLIP_UTF8, 0):
                    if self.server_clip_flags & enc.CLIP_NOTIFY:
                        self._write_clipboard(enc.CLIP_NOTIFY |
                                              enc.CLIP_UTF8)
                    return
            z = zlib.compressobj()
            packed = z.compress(struct.pack(">I", len(data)) + data) + \
                z.flush(zlib.Z_SYNC_FLUSH)
            self._write_clipboard(enc.CLIP_PROVIDE | enc.CLIP_UTF8, packed)
        else:
            self.client_cut_text(text)

    def announce_clipboard(self, text):
        """CConnection::announceClipboard(true) - the local clipboard
        changed (call from any thread)."""
        self.local_clipboard = text
        self.unsolicited_attempt = False
        flags = self.server_clip_flags
        if not flags & enc.CLIP_CAPS:
            self.client_cut_text(text)          # classic Latin-1 cut text
            return
        if self.server_clip_sizes.get(enc.CLIP_UTF8, 0) > 0 and \
                flags & enc.CLIP_PROVIDE:
            self.unsolicited_attempt = True
            self._send_clipboard_data(text)
            return
        if flags & enc.CLIP_NOTIFY:
            self._write_clipboard(enc.CLIP_NOTIFY | enc.CLIP_UTF8)
            return
        self._send_clipboard_data(text)

    # -- desktop size (CConnection::setDesktopSize/setExtendedDesktopSize) ---
    def _set_desktop_size(self, w, h):
        self.fb.resize(w, h)
        if self.continuous_updates:
            self._write_enable_cu(True)
        self.handler.framebuffer_resized(w, h)

    def _extended_desktop_size(self, reason, result, w, h):
        s = self.stream
        n = s.u8()
        s.skip(3)
        screens = []
        for _ in range(n):
            sid, sx, sy, sw, sh, flags = struct.unpack(">IHHHHI", s.read(16))
            screens.append(Screen(sid, sx, sy, sw, sh, flags))
        self.supports_set_desktop_size = True
        changed = False
        if reason != REASON_CLIENT or result == 0:
            self.screens = screens
            self._set_desktop_size(w, h)
            changed = True
        else:
            self.log("Failed to resize remote session: %d" % result)
        if reason == REASON_CLIENT:
            self._call("set_desktop_size_done", result)
        return changed

    # -- update requests and formats (CConnection) ---------------------------
    def request_new_update(self):
        """CConnection::requestNewUpdate()"""
        with self.update_lock:
            if self.format_change and not self.pending_pf_change:
                # Switch the decoding format at a safe time: after the
                # update in flight (classic), or at EndOfContinuousUpdates
                self.pending_pf_change = True
                self.pending_pf = self.next_pf
                if self.continuous_updates:
                    self._write_enable_cu(False)
                self.log("Using pixel format %s" % self.pending_pf)
                self.stream.write(bytes([enc.MSG_SET_PIXEL_FORMAT, 0, 0, 0])
                                  + self.pending_pf.pack())
                if self.continuous_updates:
                    self._write_enable_cu(True)
                self.format_change = False
            if self.encoding_change:
                self.update_encodings()
                self.encoding_change = False
            if self.force_nonincremental or not self.continuous_updates:
                self.pending_update = True
                incremental = not self.force_nonincremental
                self.stream.write(struct.pack(
                    ">BBHHHH", enc.MSG_FRAMEBUFFER_UPDATE_REQUEST,
                    1 if incremental else 0, 0, 0, self.fb.width,
                    self.fb.height))
            self.force_nonincremental = False

    def _activate_pending_pf(self):
        self.pf = self.pending_pf
        self.codec = PixelCodec(self.pf)
        self.pending_pf_change = False

    def _write_enable_cu(self, enable):
        x, y, w, h = (0, 0, self.fb.width, self.fb.height) if enable \
            else (0, 0, 0, 0)
        self.stream.write(struct.pack(">BBHHHH",
                                      enc.MSG_ENABLE_CONTINUOUS_UPDATES,
                                      1 if enable else 0, x, y, w, h))

    def set_pf(self, pf):
        """CConnection::setPF()"""
        if self.version < (3, 8):
            # Xvnc from TightVNC 1.2.9 sends cursors asynchronously, so a
            # format change can't be done safely; upstream doesn't try
            return
        with self.update_lock:
            current = self.pending_pf if self.pending_pf_change else self.pf
            if current == pf and not self.format_change:
                return
            self.next_pf = pf
            self.format_change = True

    def refresh_framebuffer(self):
        """CConnection::refreshFramebuffer(): a full update, but without
        continuous updates it waits for the update in flight."""
        with self.update_lock:
            self.force_nonincremental = True
            if self.continuous_updates:
                self.request_new_update()

    # Kept for callers that predate the port of requestNewUpdate()
    def request_update(self, incremental=True, rect=None):
        if incremental:
            self.request_new_update()
        else:
            self.refresh_framebuffer()

    # -- colour, quality and AutoSelect (TigerVNC CConn.cxx) ----------------
    def jpeg_enabled(self):
        return JPEG_AVAILABLE and not self.options.no_jpeg

    def _wanted_quality(self):
        """CConn::updateQualityLevel()"""
        if not self.options.auto_select:
            return max(0, min(9, self.options.quality_level))
        return 8 if self.bps_estimate > HIGH_QUALITY_MIN_BPS else 6

    def _wanted_pf(self):
        """CConn::updatePixelFormat()"""
        o = self.options
        full = o.full_colour
        if o.auto_select:
            full = self.bps_estimate > FULL_COLOUR_MIN_BPS
        if full:
            return PixelFormat.preferred()
        level = max(0, min(len(LOW_COLOUR_LEVELS) - 1, o.low_colour_level))
        return LOW_COLOUR_LEVELS[level]

    def _update_bandwidth(self, nbytes, elapsed):
        elapsed_us = max(1, int(elapsed * 1000000))
        bps = nbytes * 8 * 1000000 // elapsed_us
        # Longer updates weigh more, up to 20% of the new value (weight is
        # in millionths; elapsed in us, the window in ms, as in CConn.cxx)
        weight = min(200000, elapsed_us * 1000 // BPS_ESTIMATE_WINDOW_MS)
        self.bps_estimate = ((self.bps_estimate * (1000000 - weight)) +
                             bps * weight) // 1000000

    def _auto_select(self):
        """CConn::framebufferUpdateEnd() with AutoSelect on."""
        quality = self._wanted_quality()
        if quality != self.quality:
            self.log("Throughput %d kbit/s - changing to quality %d" %
                     (self.bps_estimate // 1000, quality))
            self.quality = quality
            self.encoding_change = True
        pf = self._wanted_pf()
        if pf != self.pf and not (self.pending_pf_change and
                                  pf == self.pending_pf):
            if self.version >= (3, 8):
                self.log("Throughput %d kbit/s - full color is now %s" %
                         (self.bps_estimate // 1000,
                          "enabled" if pf == PixelFormat.preferred()
                          else "disabled"))
            self.set_pf(pf)

    def change_settings(self, **changes):
        """Change ConnectionOptions from any thread (e.g. the F8 menu),
        like CConn::handleOptions().  Takes effect with the next update
        request."""
        with self.update_lock:
            for k, v in changes.items():
                setattr(self.options, k, v)
            self.quality = self._wanted_quality()
            self.encoding_change = True
            self.set_pf(self._wanted_pf())
            if self.continuous_updates:
                self.request_new_update()

    def set_pixel_format(self, pf):
        """Request a pixel format; it takes effect after this update."""
        self.set_pf(pf)

    def encodings_list(self):
        """CConnection::updateEncodings(), in the same order."""
        o = self.options
        order = [enc.PSEUDO_CURSOR_WITH_ALPHA, enc.PSEUDO_VMWARE_CURSOR,
                 enc.PSEUDO_CURSOR, enc.PSEUDO_X_CURSOR,
                 enc.PSEUDO_VMWARE_CURSOR_POSITION,
                 enc.PSEUDO_DESKTOP_SIZE, enc.PSEUDO_EXTENDED_DESKTOP_SIZE,
                 enc.PSEUDO_LED_STATE, enc.PSEUDO_VMWARE_LED_STATE,
                 enc.PSEUDO_DESKTOP_NAME, enc.PSEUDO_LAST_RECT,
                 enc.PSEUDO_EXTENDED_CLIPBOARD,
                 enc.PSEUDO_CONTINUOUS_UPDATES, enc.PSEUDO_FENCE,
                 enc.PSEUDO_QEMU_KEY_EVENT]
        # Not advertised: QEMUAudio and ExtendedMouseButtons (not
        # implemented here)
        preferred = enc.TIGHT if o.auto_select else o.preferred_encoding
        supported = [e for e in enc.SUPPORTED
                     if e != enc.JPEG or self.jpeg_enabled()]
        if preferred in supported:
            order.append(preferred)
        order.append(enc.COPYRECT)
        order += sorted((e for e in supported if e != preferred),
                        reverse=True)
        if o.custom_compress_level:
            order.append(enc.PSEUDO_COMPRESS_LEVEL_0 +
                         max(0, min(9, o.compress_level)))
        # Tight JPEG is enabled by setting a quality level
        if self.jpeg_enabled():
            order.append(enc.PSEUDO_QUALITY_LEVEL_0 + self.quality)
        return order

    def update_encodings(self):
        codes = self.encodings_list()
        self.stream.write(struct.pack(">BxH", enc.MSG_SET_ENCODINGS,
                                      len(codes)) +
                          b"".join(struct.pack(">i", c) for c in codes))

    set_encodings = update_encodings

    # -- keyboard (CConnection::sendKeyPress/sendKeyRelease) -----------------
    _KEYSYM_FIXUPS = {
        0xFFED: 0xFFEB,      # Hyper_L -> Super_L
        0xFFEE: 0xFFEC,      # Hyper_R -> Super_R
        0xFE20: 0xFF09,      # ISO_Left_Tab -> Tab (RFB says normal Tab)
    }

    def send_key_press(self, system_key_code, key_code, keysym):
        keysym = self._KEYSYM_FIXUPS.get(keysym, keysym)
        with self.key_lock:
            # Remember what this physical key sent, so the release matches
            self.down_keys[system_key_code] = (key_code, keysym)
        self._write_key_event(keysym, key_code, True)

    def send_key_release(self, system_key_code):
        with self.key_lock:
            entry = self.down_keys.pop(system_key_code, None)
        if entry is None:
            return              # "Unexpected release of key code"
        key_code, keysym = entry
        self._write_key_event(keysym, key_code, False)

    def release_all_keys(self):
        while True:
            with self.key_lock:
                if not self.down_keys:
                    return
                code = next(iter(self.down_keys))
            self.send_key_release(code)

    def _write_key_event(self, keysym, key_code, down):
        """CMsgWriter::writeKeyEvent()"""
        if not self.supports_qemu_key_event or not key_code:
            if not keysym:
                return
            self.stream.write(struct.pack(">BBxxI", enc.MSG_KEY_EVENT,
                                          1 if down else 0,
                                          keysym & 0xFFFFFFFF))
        else:
            self.stream.write(struct.pack(">BBHII", enc.MSG_QEMU_CLIENT,
                                          enc.QEMU_EXTENDED_KEY_EVENT,
                                          1 if down else 0,
                                          keysym & 0xFFFFFFFF, key_code))

    def key_event(self, keysym, down):
        """A bare keysym event, not tracked (tests, simple callers)."""
        self._write_key_event(keysym, 0, down)

    # -- other client messages ------------------------------------------------
    def pointer_event(self, x, y, button_mask):
        x = max(0, min(x, self.fb.width - 1))
        y = max(0, min(y, self.fb.height - 1))
        self.stream.write(struct.pack(">BBHH", enc.MSG_POINTER_EVENT,
                                      button_mask & 0xFF, x, y))

    def client_cut_text(self, text):
        """CMsgWriter::writeClientCutText(): Latin-1, LF line endings."""
        data = text.replace("\r\n", "\n").encode("latin-1", "replace")
        self.stream.write(struct.pack(">BxxxI", enc.MSG_CLIENT_CUT_TEXT,
                                      len(data)) + data)

    def set_desktop_size(self, w, h):
        """Ask the server to change size (ExtendedDesktopSize servers only)."""
        if not self.supports_set_desktop_size:
            return False
        if self.screens:
            first = self.screens[0]
            layout = [Screen(first.id, 0, 0, w, h, first.flags)]
        else:
            layout = [Screen(0, 0, 0, w, h, 0)]
        msg = struct.pack(">BxHHBx", enc.MSG_SET_DESKTOP_SIZE, w, h,
                          len(layout))
        for sc in layout:
            msg += struct.pack(">IHHHHI", sc.id, sc.x, sc.y, sc.w, sc.h,
                               sc.flags)
        self.stream.write(msg)
        return True
