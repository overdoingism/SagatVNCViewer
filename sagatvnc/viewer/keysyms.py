# SagatVNC Viewer - a Python port of the TigerVNC viewer
# Copyright (C) 2026 SagatVNC Viewer contributors
#
# This is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
"""Translate Tk key events to X11 keysyms (what RFB KeyEvent carries).

Tk already reports X keysyms in ``event.keysym_num`` on every platform,
so most keys pass straight through.  The exceptions handled here are
Tk-specific names (Win_L, App, ...) and characters outside Latin-1, which
become Unicode keysyms (0x01000000 + code point), as in TigerVNC's
keysym2ucs/ucs2keysym.
"""

XK_VoidSymbol = 0xFFFFFF

# Tk keysym names that do not correspond to the X keysym an RFB server
# expects.
NAME_OVERRIDES = {
    "Win_L": 0xFFEB,      # Super_L
    "Win_R": 0xFFEC,      # Super_R
    "App": 0xFF67,        # Menu
    "Option_L": 0xFFE9,   # Alt_L (macOS)
    "Option_R": 0xFFEA,   # Alt_R (macOS)
    "Meta_L": 0xFFE7,
    "Meta_R": 0xFFE8,
    "Cancel": 0xFF69,
    "ISO_Left_Tab": 0xFE20,
}

NAMED = {
    "Control_L": 0xFFE3, "Control_R": 0xFFE4,
    "Alt_L": 0xFFE9, "Alt_R": 0xFFEA,
    "Shift_L": 0xFFE1, "Shift_R": 0xFFE2,
    "Delete": 0xFFFF, "F8": 0xFFC5,
}


def event_to_keysym(keysym_name, keysym_num, char):
    """Return the X keysym for a Tk key event, or None if unknown."""
    if keysym_name in NAME_OVERRIDES:
        return NAME_OVERRIDES[keysym_name]
    ks = keysym_num or 0
    # Function, cursor, keypad and modifier keys: trust Tk's keysym
    if 0xFE00 <= ks <= 0xFFFF:
        return ks
    if char and len(char) == 1:
        o = ord(char)
        if o >= 0x20 and o != 0x7F:
            if o < 0x100:
                return o
            return 0x01000000 | o
    if ks and ks != XK_VoidSymbol:
        return ks
    return None
