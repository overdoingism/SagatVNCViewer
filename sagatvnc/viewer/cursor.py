# SagatVNC Viewer - a Python port of the TigerVNC viewer
# Copyright (C) 2026 SagatVNC Viewer contributors
#
# This is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
"""Show the server's cursor shape as the local mouse cursor.

Like TigerVNC, the viewer asks for the Cursor pseudo-encoding.  The
server then stops drawing its cursor into the desktop image, which a
non-DPI-aware server may draw in the wrong place, and sends the shape
instead.  We show that shape as the real local cursor, so it is always
exactly where the clicks go.

Tk can only use custom cursors from files: ``.cur`` files on Windows
(``cursor="@C:/path/x.cur"``).  Elsewhere we fall back to Tk's built-in
cursors.
"""

import atexit
import collections
import hashlib
import os
import shutil
import struct
import sys
import tempfile

try:
    from PIL import Image
except ImportError:
    Image = None

CUSTOM_SUPPORTED = sys.platform == "win32"


def make_cur(w, h, hot_x, hot_y, rgba):
    """Build a Windows .cur file (32 bpp BGRA DIB with AND mask)."""
    if not (0 < w <= 256 and 0 < h <= 256):
        raise ValueError("cursor size %dx%d" % (w, h))
    xor = bytearray()
    for y in range(h - 1, -1, -1):              # DIBs are bottom-up
        row = rgba[y * w * 4:(y + 1) * w * 4]
        bgra = bytearray(len(row))
        bgra[0::4] = row[2::4]
        bgra[1::4] = row[1::4]
        bgra[2::4] = row[0::4]
        bgra[3::4] = row[3::4]
        xor += bgra
    mask_row = ((w + 31) // 32) * 4
    and_mask = bytearray()
    for y in range(h - 1, -1, -1):
        bits = bytearray(mask_row)
        for x in range(w):
            if rgba[(y * w + x) * 4 + 3] == 0:   # transparent
                bits[x >> 3] |= 0x80 >> (x & 7)
        and_mask += bits
    header = struct.pack("<IiiHHIIiiII", 40, w, h * 2, 1, 32, 0,
                         len(xor) + len(and_mask), 0, 0, 0, 0)
    image = header + bytes(xor) + bytes(and_mask)
    entry = struct.pack("<BBBBHHII", w % 256, h % 256, 0, 0, hot_x, hot_y,
                        len(image), 6 + 16)
    return struct.pack("<HHH", 0, 2, 1) + entry + image


def scale_cursor(w, h, hot_x, hot_y, rgba, factor):
    """Smoothly resample a cursor (needs Pillow).  Only right for colour
    cursors: thin black/white lines turn grey (see shrink_cursor)."""
    if Image is None or abs(factor - 1.0) < 0.01:
        return w, h, hot_x, hot_y, rgba
    nw = max(1, min(256, round(w * factor)))
    nh = max(1, min(256, round(h * factor)))
    im = Image.frombytes("RGBA", (w, h), rgba)
    # Premultiplied resampling avoids dark fringes at the edges
    im = im.convert("RGBa").resize((nw, nh), Image.Resampling.LANCZOS)
    im = im.convert("RGBA")
    return (nw, nh, min(nw - 1, round(hot_x * nw / w)),
            min(nh - 1, round(hot_y * nh / h)), im.tobytes())


# -- keeping cursors visible ---------------------------------------------------
# Windows' I-beam is an XOR (invert) cursor; RFB can't express that, so
# winvnc sends it as a 1px black line with a 1px white outline
# (rfb_win32/DeviceFrameBuffer.cxx).  Smooth resampling turns that into
# a faint grey smear, which is how the text cursor "vanished".

def _luma(r, g, b):
    return (299 * r + 587 * g + 114 * b) // 1000


def _tones(rgba):
    """(has dark, has light, only black/white) over the opaque pixels."""
    dark = light = False
    two_tone = True
    for i in range(0, len(rgba), 4):
        if rgba[i + 3] < 128:
            continue
        y = _luma(rgba[i], rgba[i + 1], rgba[i + 2])
        dark |= y < 96
        light |= y > 160
        if 48 < y < 208:
            two_tone = False
    return dark, light, two_tone


def ensure_contrast(w, h, hot_x, hot_y, rgba):
    """Give a one-tone cursor a 1px outline of the opposite tone, as
    winvnc does for XOR cursors, so it shows on dark and light alike."""
    if not w or not h:
        return w, h, hot_x, hot_y, rgba
    dark, light, _ = _tones(rgba)
    if dark == light:              # both (arrow, hand...) or nothing
        return w, h, hot_x, hot_y, rgba
    edge = b"\xff\xff\xff\xff" if dark else b"\x00\x00\x00\xff"
    nw, nh = w + 2, h + 2
    out = bytearray(nw * nh * 4)
    for y in range(h):             # pass 1: outline every visible pixel
        for x in range(w):
            if rgba[(y * w + x) * 4 + 3] >= 128:
                for dy in (0, 1, 2):
                    o = ((y + dy) * nw + x) * 4
                    out[o:o + 12] = edge * 3
    for y in range(h):             # pass 2: the cursor itself on top
        for x in range(w):
            s = (y * w + x) * 4
            if rgba[s + 3]:
                o = ((y + 1) * nw + x + 1) * 4
                out[o:o + 4] = rgba[s:s + 4]
    return nw, nh, hot_x + 1, hot_y + 1, bytes(out)


def enlarge_nearest(w, h, hot_x, hot_y, rgba, n):
    """Integer nearest-neighbour enlargement: crisp, nothing blurs."""
    if n <= 1:
        return w, h, hot_x, hot_y, rgba
    n = min(n, 256 // max(w, h, 1)) or 1
    out = bytearray()
    for y in range(h):
        row = b"".join(rgba[(y * w + x) * 4:(y * w + x) * 4 + 4] * n
                       for x in range(w))
        out += row * n
    return w * n, h * n, hot_x * n, hot_y * n, bytes(out)


def resize_nearest(w, h, hot_x, hot_y, rgba, factor):
    """Nearest-neighbour resize by any factor: edges stay hard, colours
    stay exactly black/white (for pixel-art cursors when enlarging)."""
    nw = max(1, min(256, round(w * factor)))
    nh = max(1, min(256, round(h * factor)))
    out = bytearray(nw * nh * 4)
    for y in range(nh):
        sy = min(h - 1, int(y * h / nh))
        for x in range(nw):
            sx = min(w - 1, int(x * w / nw))
            s = (sy * w + sx) * 4
            out[(y * nw + x) * 4:(y * nw + x) * 4 + 4] = rgba[s:s + 4]
    return (nw, nh, min(nw - 1, int(hot_x * nw / w)),
            min(nh - 1, int(hot_y * nh / h)), bytes(out))


# Windows draws cursors for 96 DPI at 32x32 and a DPI-unaware server such
# as winvnc only ever sees those; the local system cursor size tells us
# how big that should look here (48 at 150-175%, 64 at 200%).
BASE_CURSOR_SIZE = 32


def cursor_scale_for_display():
    size = system_cursor_size()
    if not size:
        return 1.0
    return max(1.0, size[0] / BASE_CURSOR_SIZE)


def resize_cursor(w, h, hot_x, hot_y, rgba, factor):
    """Resize keeping black/white cursors crisp: nearest when enlarging,
    per-tone pooling when shrinking; colour cursors smoothly."""
    if abs(factor - 1.0) < 0.01:
        return w, h, hot_x, hot_y, rgba
    if factor < 1:
        return shrink_cursor(w, h, hot_x, hot_y, rgba, factor)
    if _tones(rgba)[2]:
        return resize_nearest(w, h, hot_x, hot_y, rgba, factor)
    return scale_cursor(w, h, hot_x, hot_y, rgba, factor)


def shrink_cursor(w, h, hot_x, hot_y, rgba, factor):
    """Shrink a cursor that doesn't fit.

    Colour cursors are resampled smoothly.  Black/white ones (arrow,
    I-beam...) are shrunk per tone with "any pixel in the footprint"
    (max) pooling, dark drawn over light.  So a 1px black line stays a
    black line with its white edge instead of averaging into grey.
    """
    if not _tones(rgba)[2]:
        return scale_cursor(w, h, hot_x, hot_y, rgba, factor)
    nw = max(1, min(256, round(w * factor)))
    nh = max(1, min(256, round(h * factor)))
    dark = [[False] * nw for _ in range(nh)]
    light = [[False] * nw for _ in range(nh)]
    for dy in range(nh):
        y0 = dy * h // nh
        y1 = max(y0 + 1, -(-(dy + 1) * h // nh))
        for dx in range(nw):
            x0 = dx * w // nw
            x1 = max(x0 + 1, -(-(dx + 1) * w // nw))
            for y in range(y0, y1):
                for x in range(x0, x1):
                    i = (y * w + x) * 4
                    if rgba[i + 3] >= 128:
                        if _luma(rgba[i], rgba[i + 1], rgba[i + 2]) < 128:
                            dark[dy][dx] = True
                        else:
                            light[dy][dx] = True

    def near(grid, x, y):
        return any(grid[yy][xx]
                   for yy in range(max(0, y - 1), min(nh, y + 2))
                   for xx in range(max(0, x - 1), min(nw, x + 2)))

    out = bytearray(nw * nh * 4)
    for y in range(nh):
        for x in range(nw):
            o = (y * nw + x) * 4
            if dark[y][x]:
                out[o:o + 4] = b"\0\0\0\xff"
            elif light[y][x] or (near(dark, x, y) and near(light, x, y)):
                # where the light tone was, keep a light edge round the
                # dark lines - the outline that makes them visible on black
                out[o:o + 4] = b"\xff\xff\xff\xff"
    return (nw, nh, min(nw - 1, hot_x * nw // w),
            min(nh - 1, hot_y * nh // h), bytes(out))


def system_cursor_size():
    """The size Windows stretches every loaded cursor to (SM_CXCURSOR /
    SM_CYCURSOR, which already include the DPI scale for a DPI-aware
    process).  LoadCursorFromFile - what Tk's "@file" uses - always loads
    at this size, so a 20x20 cursor becomes 48x48 at 175% with its hotspot
    moved.  Returns None where that doesn't apply."""
    if not CUSTOM_SUPPORTED:
        return None
    import ctypes
    w = ctypes.windll.user32.GetSystemMetrics(13)
    h = ctypes.windll.user32.GetSystemMetrics(14)
    return (w, h) if w > 0 and h > 0 else None


def fit_to_canvas(w, h, hot_x, hot_y, rgba, size):
    """Put the cursor in the top-left of a transparent ``size`` canvas so
    Windows loads it without stretching; shrink it first if too big."""
    cw, ch = size
    if w > cw or h > ch:
        factor = min(cw / w, ch / h)
        w, h, hot_x, hot_y, rgba = shrink_cursor(w, h, hot_x, hot_y, rgba,
                                                 factor)
    # Without Pillow it may still be too big: crop (source stride is w)
    cpw, cph = min(w, cw), min(h, ch)
    out = bytearray(cw * ch * 4)
    for y in range(cph):
        out[y * cw * 4:y * cw * 4 + cpw * 4] = \
            rgba[y * w * 4:y * w * 4 + cpw * 4]
    return cw, ch, min(hot_x, cw - 1), min(hot_y, ch - 1), bytes(out)


class CursorManager:
    """Keeps the canvas cursor in step with the server's cursor shape."""

    CACHE_SIZE = 256           # distinct shapes kept loaded (see _load)

    def __init__(self, widget, always_cursor=False, cursor_type="dot",
                 remote_shape=True, blank_as_ibeam=True):
        self.widget = widget
        self.blank_as_ibeam = blank_as_ibeam
        # With remote_shape off, always the local system arrow.  The server
        # is asked for its cursor shape either way - that is what stops it
        # drawing a (possibly misplaced) cursor into the desktop image.
        self.remote_shape = remote_shape
        self.always_cursor = always_cursor
        self.fallback = "dot" if cursor_type.lower() == "dot" else "arrow"
        self.shape = None          # (w, h, hot_x, hot_y, rgba) from server
        self.scale = 1.0
        self.cache = collections.OrderedDict()  # sha1 -> (spec, path, holder)
        self.dir = None
        self.current_spec = None
        self.inside = True
        self.apply()

    def _tempdir(self):
        if self.dir is None:
            self.dir = tempfile.mkdtemp(prefix="sagatvnc-cursor-")
            atexit.register(shutil.rmtree, self.dir, True)
        return self.dir

    def set_shape(self, w, h, hot_x, hot_y, rgba):
        self.shape = (w, h, hot_x, hot_y, rgba)
        self.apply()

    def set_scale(self, factor):
        """The view scale.  Cursors are sized for the local display
        instead (see apply), so this only records it."""
        self.scale = factor

    def show(self):
        """The pointer is over the desktop: put our cursor back, without
        rebuilding it."""
        self.inside = True
        if self.current_spec:
            self.widget.configure(cursor=self.current_spec)
        else:
            self.apply()

    def outside(self):
        """The pointer is over the black bars: the normal arrow there.
        Shapes arriving meanwhile are kept for when it comes back."""
        self.inside = False
        self.widget.configure(cursor="arrow")

    def _set(self, spec):
        self.current_spec = spec
        if self.inside:
            self.widget.configure(cursor=spec)

    def set_remote_shape(self, on):
        self.remote_shape = on
        self.apply()

    def apply(self):
        if not self.remote_shape:
            self._set("arrow")
            return
        if self.shape is None:
            # No shape from the server yet.  (Upstream shows a blank
            # cursor here; an arrow is less surprising and servers send a
            # shape straight away anyway.)
            self._set("arrow")
            return
        w, h, hx, hy, rgba = self.shape
        if w == 0 or h == 0:
            # The server hides its cursor (e.g. while typing)
            self._set(self.fallback if self.always_cursor else "none")
            return
        if not any(rgba[3::4]):
            # Visible but fully transparent.  TigerVNC's winvnc sends
            # Windows' I-beam like that: it is a colour cursor whose alpha
            # is all 0 (it relies on its AND/XOR masks to invert), and
            # winvnc only looks at the alpha (DeviceFrameBuffer::
            # setCursor).  Show the local I-beam - a real inverting one.
            if self.blank_as_ibeam:
                self._set("xterm")
            else:
                self._set(self.fallback if self.always_cursor else "none")
            return
        if not CUSTOM_SUPPORTED:
            self._set("arrow")
            return
        w, h, hx, hy, rgba = ensure_contrast(w, h, hx, hy, rgba)
        # As big as local cursors (a DPI-unaware server sends 96 DPI
        # cursors); not shrunk with the view, where thin lines would vanish
        w, h, hx, hy, rgba = resize_cursor(w, h, hx, hy, rgba,
                                           cursor_scale_for_display())
        size = system_cursor_size()
        if size:
            w, h, hx, hy, rgba = fit_to_canvas(w, h, hx, hy, rgba, size)
        data = make_cur(w, h, hx, hy, rgba)
        key = hashlib.sha1(data).hexdigest()
        entry = self.cache.get(key)
        if entry is None:
            entry = self._load(key, data)
            if entry is None:
                self._set("arrow")
                return
        else:
            self.cache.move_to_end(key)
        self._set(entry[0])

    def _load(self, key, data):
        """Load a cursor into Tk once and keep it.

        Tk on Windows never destroys a cursor it loaded from a file
        (TkpFreeCursor is empty), so every "@file" it loads costs 6 GDI
        and 2 USER handles for good. A new file per shape ran a long
        session into the 10,000 GDI handle limit: cursors turned black,
        the window froze and Tk crashed. So each distinct shape is loaded
        once, and an unmapped frame holds it, because Tk would otherwise
        forget it as soon as the canvas switches to another one and load
        it again next time. Shapes repeat (arrow, I-beam, hand, the
        frames of a busy animation), so the cache stays small; past
        CACHE_SIZE the least recently used is dropped, which leaks only
        that one."""
        path = os.path.join(self._tempdir(), "c-%s.cur" % key[:20])
        spec = "@" + path.replace("\\", "/")
        try:
            with open(path, "wb") as f:
                f.write(data)
            holder = self._pin(spec)
        except Exception:
            _remove(path)
            return None
        entry = self.cache[key] = (spec, path, holder)
        while len(self.cache) > self.CACHE_SIZE:
            _key, (_spec, old_path, old_holder) = self.cache.popitem(
                last=False)
            try:
                old_holder.destroy()
            except Exception:
                pass
            _remove(old_path)
        return entry

    def _pin(self, spec):
        """A widget that keeps Tk's cursor for *spec* loaded (raises
        TclError if Tk can't load it).  Never mapped."""
        import tkinter
        return tkinter.Frame(self.widget, cursor=spec)


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass
