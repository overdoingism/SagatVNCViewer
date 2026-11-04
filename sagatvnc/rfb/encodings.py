# SagatVNC Viewer - a Python port of the TigerVNC viewer
# Copyright (C) 2026 SagatVNC Viewer contributors
# Based on TigerVNC common/rfb/encodings.h,
# Copyright (C) 2002-2005 RealVNC Ltd. and TigerVNC contributors.
#
# This is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
"""RFB encoding, security type and message type numbers."""

RAW = 0
COPYRECT = 1
RRE = 2
CORRE = 4
HEXTILE = 5
TIGHT = 7
ZRLE = 16

PSEUDO_CURSOR = -239
PSEUDO_DESKTOP_SIZE = -223
PSEUDO_LAST_RECT = -224
PSEUDO_EXTENDED_DESKTOP_SIZE = -308
PSEUDO_DESKTOP_NAME = -307
PSEUDO_COMPRESS_LEVEL_0 = -256

NAMES = {
    "Raw": RAW, "CopyRect": COPYRECT, "RRE": RRE, "CoRRE": CORRE,
    "Hextile": HEXTILE, "Tight": TIGHT, "ZRLE": ZRLE,
}

# Encodings this viewer can decode, best first (what "auto" prefers).
SUPPORTED = [TIGHT, ZRLE, HEXTILE, RRE, CORRE, RAW]


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

# Same default order as TigerVNC (minus types this port lacks: RA2, DH, ...)
DEFAULT_SECURITY = ("VeNCrypt,X509Plain,TLSPlain,X509Vnc,TLSVnc,"
                    "X509None,TLSNone,VncAuth,None")


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

# Client -> server messages
MSG_SET_PIXEL_FORMAT = 0
MSG_SET_ENCODINGS = 2
MSG_FRAMEBUFFER_UPDATE_REQUEST = 3
MSG_KEY_EVENT = 4
MSG_POINTER_EVENT = 5
MSG_CLIENT_CUT_TEXT = 6
MSG_SET_DESKTOP_SIZE = 251
