# SagatVNC Viewer - a Python port of the TigerVNC viewer
# Copyright (C) 2026 SagatVNC Viewer contributors
#
# This is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
"""Scale the remote framebuffer to the window, one damaged rect at a time.

Filters (all via Pillow's C resampler, which is separable and does proper
area-weighted filtering when shrinking, so there's no aliasing):

* shrinking: Lanczos-3, sharp and alias-free, good for text
* enlarging: bicubic (Catmull-Rom, a = -0.5), less ringing than Lanczos
* exactly 1:1: no resampling at all, pixel-exact copy

Only damaged areas are rescaled.  A damaged source rect maps to a
destination rect, grown by the filter's reach.  It is resampled from a
source crop padded by the filter support, with Pillow's ``box`` argument
giving the exact sub-pixel source window.  So each patch matches the same
pixels of a full-frame resize (within +-1 from float rounding), and the
patches leave no seams.

Without Pillow, ``available`` is False and the viewer shows 1:1 with
scrollbars instead.
"""

import math

try:
    from PIL import Image
    _LANCZOS = Image.Resampling.LANCZOS
    _BICUBIC = Image.Resampling.BICUBIC
except ImportError:            # optional dependency
    Image = None

LANCZOS_SUPPORT = 3.0
BICUBIC_SUPPORT = 2.0


def fit_size(src_w, src_h, area_w, area_h):
    """Largest size with the source aspect ratio that fits the area."""
    if src_w <= 0 or src_h <= 0 or area_w <= 0 or area_h <= 0:
        return max(1, src_w), max(1, src_h)
    s = min(area_w / src_w, area_h / src_h)
    return max(1, round(src_w * s)), max(1, round(src_h * s))


class Scaler:
    available = Image is not None

    def __init__(self):
        self.configure(1, 1, 1, 1)

    def configure(self, src_w, src_h, dst_w, dst_h):
        self.src_w, self.src_h = src_w, src_h
        self.dst_w, self.dst_h = dst_w, dst_h
        self.sx = dst_w / src_w
        self.sy = dst_h / src_h
        self.identity = (src_w, src_h) == (dst_w, dst_h)
        shrinking = self.sx < 1 or self.sy < 1
        if Image is not None:
            self.filter = _LANCZOS if shrinking else _BICUBIC
        self.support = LANCZOS_SUPPORT if shrinking else BICUBIC_SUPPORT

    # -- coordinate mapping ----------------------------------------------------
    def to_source(self, dx, dy):
        """Destination (window) pixel -> framebuffer pixel, clamped."""
        x = int(dx / self.sx)
        y = int(dy / self.sy)
        return (min(max(x, 0), self.src_w - 1),
                min(max(y, 0), self.src_h - 1))

    def dest_rect(self, x, y, w, h):
        """Destination rect affected by a changed source rect."""
        if self.identity:
            return x, y, w, h
        # A source pixel reaches support * max(1, scale) destination pixels
        mx = math.ceil(self.support * max(1.0, self.sx)) + 1
        my = math.ceil(self.support * max(1.0, self.sy)) + 1
        x0 = max(0, math.floor(x * self.sx) - mx)
        y0 = max(0, math.floor(y * self.sy) - my)
        x1 = min(self.dst_w, math.ceil((x + w) * self.sx) + mx)
        y1 = min(self.dst_h, math.ceil((y + h) * self.sy) + my)
        return x0, y0, max(0, x1 - x0), max(0, y1 - y0)

    # -- resampling ----------------------------------------------------------
    def render(self, fb, dx, dy, dw, dh):
        """RGB bytes for destination rect (dx, dy, dw, dh); hold fb.lock.

        Done as two explicit single-direction passes (horizontal, then
        vertical) rather than one ``Image.resize`` call.  A one-shot resize
        lets Pillow pick its pass order and intermediate size based on the
        input, and the intermediate is clipped to 8 bits.  Patches would
        then differ from the full frame at high-contrast edges such as text.
        With fixed passes, every output pixel goes through identical steps
        whatever patch it is in.
        """
        if self.identity:
            return fb.get_rect(dx, dy, dw, dh)
        sx, sy = self.sx, self.sy
        bx0, bx1 = dx / sx, (dx + dw) / sx
        by0, by1 = dy / sy, (dy + dh) / sy
        # Source pixels the filter can reach, plus slack for rounding
        reach_x = self.support * max(1.0, 1.0 / sx) + 2
        reach_y = self.support * max(1.0, 1.0 / sy) + 2
        cx0 = max(0, math.floor(bx0 - reach_x))
        cx1 = min(self.src_w, math.ceil(bx1 + reach_x))
        cy0 = max(0, math.floor(by0 - reach_y))
        cy1 = min(self.src_h, math.ceil(by1 + reach_y))
        cw, ch = cx1 - cx0, cy1 - cy0
        src = Image.frombytes("RGB", (cw, ch), fb.get_rect(cx0, cy0, cw, ch))
        # Pass 1: horizontal only (height and vertical box unchanged)
        tmp = src.resize((dw, ch), self.filter,
                         box=(bx0 - cx0, 0, bx1 - cx0, ch))
        # Pass 2: vertical only
        out = tmp.resize((dw, dh), self.filter,
                         box=(0, by0 - cy0, dw, by1 - cy0))
        return out.tobytes()

    def render_full(self, fb):
        return self.render(fb, 0, 0, self.dst_w, self.dst_h)
