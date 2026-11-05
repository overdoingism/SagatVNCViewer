# SagatVNC Viewer - a Python port of the TigerVNC viewer
# Copyright (C) 2026 SagatVNC Viewer contributors
# Based on TigerVNC common/rfb/encodings.h,
# Copyright (C) 2002-2005 RealVNC Ltd. and TigerVNC contributors.
#
# This is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
"""RFB encoding, security type and message type numbers."""

RAW = 0
COPYRECT = 1
RRE = 2
CORRE = 4
HEXTILE = 5
TIGHT = 7
ZRLE = 16
JPEG = 21

PSEUDO_CURSOR = -239
PSEUDO_X_CURSOR = -240
PSEUDO_CURSOR_WITH_ALPHA = -314
PSEUDO_VMWARE_CURSOR = 0x574D5664
PSEUDO_VMWARE_CURSOR_POSITION = 0x574D5666
PSEUDO_LED_STATE = -261
PSEUDO_VMWARE_LED_STATE = 0x574D5668
PSEUDO_FENCE = -312
PSEUDO_CONTINUOUS_UPDATES = -313
PSEUDO_QEMU_KEY_EVENT = -258

# LED state bits (rfb/ledStates.h)
LED_SCROLL_LOCK = 1 << 0
LED_NUM_LOCK = 1 << 1
LED_CAPS_LOCK = 1 << 2
LED_UNKNOWN = -1

# Fence flags (rfb/fenceTypes.h)
FENCE_BLOCK_BEFORE = 1 << 0
FENCE_BLOCK_AFTER = 1 << 1
FENCE_SYNC_NEXT = 1 << 2
FENCE_REQUEST = 1 << 31
PSEUDO_DESKTOP_SIZE = -223
PSEUDO_LAST_RECT = -224
PSEUDO_EXTENDED_DESKTOP_SIZE = -308
PSEUDO_DESKTOP_NAME = -307
PSEUDO_COMPRESS_LEVEL_0 = -256
PSEUDO_QUALITY_LEVEL_0 = -32
PSEUDO_EXTENDED_CLIPBOARD = 0xC0A1E5CE - (1 << 32)   # signed on wire

# Extended clipboard flags (TigerVNC common/rfb/clipboardTypes.h)
CLIP_UTF8 = 1 << 0
CLIP_FORMAT_MASK = 0x0000FFFF
CLIP_CAPS = 1 << 24
CLIP_REQUEST = 1 << 25
CLIP_PEEK = 1 << 26
CLIP_NOTIFY = 1 << 27
CLIP_PROVIDE = 1 << 28
CLIP_ACTION_MASK = 0xFF000000

NAMES = {
    "Raw": RAW, "CopyRect": COPYRECT, "RRE": RRE, "CoRRE": CORRE,
    "Hextile": HEXTILE, "Tight": TIGHT, "ZRLE": ZRLE, "JPEG": JPEG,
}

# Encodings TigerVNC's PreferredEncoding accepts but this port cannot
# decode.
UNSUPPORTED_NAMES = {"H.264": 50, "H264": 50}

# Encodings advertised, like TigerVNC Decoder::supported() (CoRRE is
# decoded if a server sends it anyway, but not advertised upstream).
SUPPORTED = [TIGHT, ZRLE, JPEG, HEXTILE, RRE, RAW]

# PreferredEncoding values upstream accepts (vncviewer/parameters.cxx)
PREFERRED_NAMES = ("Tight", "JPEG", "ZRLE", "Hextile", "Raw")


def encoding_name(num):
    for name, value in NAMES.items():
        if value == num:
            return name
    return "[unknown encoding %d]" % num


# Security types
SEC_INVALID = 0
SEC_NONE = 1
SEC_VNC_AUTH = 2
SEC_VENCRYPT = 19
SEC_PLAIN = 256
SEC_TLS_NONE = 257
SEC_TLS_VNC = 258
SEC_TLS_PLAIN = 259
SEC_X509_NONE = 260
SEC_X509_VNC = 261
SEC_X509_PLAIN = 262

SEC_NAMES = {
    "None": SEC_NONE, "VncAuth": SEC_VNC_AUTH, "VeNCrypt": SEC_VENCRYPT,
    "Plain": SEC_PLAIN, "TLSNone": SEC_TLS_NONE, "TLSVnc": SEC_TLS_VNC,
    "TLSPlain": SEC_TLS_PLAIN, "X509None": SEC_X509_NONE,
    "X509Vnc": SEC_X509_VNC, "X509Plain": SEC_X509_PLAIN,
}

# Security types TigerVNC knows (common/rfb/Security.cxx) that this port
# does not implement.  They may appear in SecurityTypes, e.g. in .tigervnc
# files, and are skipped rather than rejected.
SEC_UNSUPPORTED_NAMES = {
    "Tight": 16, "RA2": 5, "RA2ne": 6, "RA2_256": 129, "RA2ne_256": 130,
    "SSPI": 7, "SSPIne": 8, "DH": 30, "MSLogonII": 113,
}

# TigerVNC's default is every supported scheme (SecurityClient.cxx) - the
# server's order then decides.  RA2/RA2ne/RA2_256/RA2ne_256/DH/MSLogonII
# are not implemented in this port.
DEFAULT_SECURITY = ("None,VncAuth,Plain,TLSNone,TLSVnc,TLSPlain,"
                    "X509None,X509Vnc,X509Plain")


def sec_name(num):
    for name, value in SEC_NAMES.items():
        if value == num:
            return name
    return "[unknown secType %d]" % num


# Server -> client messages
MSG_FRAMEBUFFER_UPDATE = 0
MSG_SET_COLOUR_MAP_ENTRIES = 1
MSG_BELL = 2
MSG_SERVER_CUT_TEXT = 3
MSG_END_OF_CONTINUOUS_UPDATES = 150
MSG_SERVER_FENCE = 248

# Client -> server messages
MSG_SET_PIXEL_FORMAT = 0
MSG_SET_ENCODINGS = 2
MSG_FRAMEBUFFER_UPDATE_REQUEST = 3
MSG_KEY_EVENT = 4
MSG_POINTER_EVENT = 5
MSG_CLIENT_CUT_TEXT = 6
MSG_SET_DESKTOP_SIZE = 251
MSG_ENABLE_CONTINUOUS_UPDATES = 150
MSG_CLIENT_FENCE = 248
MSG_QEMU_CLIENT = 255
QEMU_EXTENDED_KEY_EVENT = 0
