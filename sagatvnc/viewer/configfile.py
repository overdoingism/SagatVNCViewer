# SagatVNC Viewer - a Python port of the TigerVNC viewer
# Copyright (C) 2026 SagatVNC Viewer contributors
# Based on TigerVNC vncviewer/parameters.cxx (loadViewerParameters),
# Copyright (C) 2002-2005 RealVNC Ltd., Copyright 2011 Pierre Ossman for
# Cendio AB, Copyright 2012 Samuel Mannehed for Cendio AB, and TigerVNC
# contributors.
#
# This is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
"""Reading and writing TigerVNC ``.tigervnc`` configuration files.

Format::

    TigerVNC Configuration file Version 1.0

    ServerName=host:1
    SecurityTypes=VncAuth
    FullScreen=1

Values escape newline, carriage return and backslash as ``\\n``, ``\\r``
and ``\\\\``.  Parameters this port does not implement are ignored (and
logged), so files saved by any TigerVNC version can be opened.
"""

import os

from .parameters import _lookup

IDENTIFIER_STRING = "TigerVNC Configuration file Version 1.0"
_LINE_MAX = 255       # TigerVNC reads lines into a 256 byte buffer


class ConfigFileError(Exception):
    pass


def looks_like_config_file(arg):
    """TigerVNC treats a server argument containing a path separator as a
    configuration file; we also accept an existing ``*.tigervnc`` file."""
    if "/" in arg or "\\" in arg:
        return True
    return arg.lower().endswith(".tigervnc") and os.path.isfile(arg)


def decode_value(value):
    out = []
    i = 0
    while i < len(value):
        c = value[i]
        if c == "\\":
            i += 1
            if i >= len(value):
                raise ValueError("Invalid format or too large value")
            esc = value[i]
            if esc == "n":
                out.append("\n")
            elif esc == "r":
                out.append("\r")
            elif esc == "\\":
                out.append("\\")
            else:
                raise ValueError("Invalid format or too large value")
        else:
            out.append(c)
        i += 1
    return "".join(out)


def encode_value(value):
    return (value.replace("\\", "\\\\").replace("\n", "\\n")
            .replace("\r", "\\r"))


def load(path, opts, log=lambda msg: None):
    """Apply the parameters in ``path`` to ``opts``; return the server name.

    Like TigerVNC, a bad line is logged and skipped rather than fatal, but
    a missing file or a wrong identifier line raises ConfigFileError.
    """
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError as e:
        raise ConfigFileError("Failed to open \"%s\": %s" %
                              (path, e.strerror or e))
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    if text.startswith("\ufeff"):
        text = text[1:]
    lines = text.splitlines()
    if not lines or not lines[0].startswith(IDENTIFIER_STRING):
        raise ConfigFileError("Configuration file %s is in an invalid "
                              "format" % path)

    server = ""
    for nr, line in enumerate(lines[1:], start=2):
        line = line.rstrip("\r\n")
        if not line or line.startswith("#") or line.startswith("\0"):
            continue
        where = "Failed to read line %d in file \"%s\"" % (nr, path)
        if len(line) >= _LINE_MAX:
            log("%s: Line too long" % where)
            continue
        name, eq, value = line.partition("=")
        if not eq:
            log("%s: Invalid format" % where)
            continue
        try:
            value = decode_value(value)
            if name.strip().lower() == "servername":
                server = value
                continue
            param = _lookup(name.strip())
            if param is None:
                log("%s: Parameter %s is not supported by SagatVNC, "
                    "ignored" % (where, name.strip()))
                continue
            setattr(opts, param.name.lower(), param.convert(value))
        except ValueError as e:
            log("%s: %s" % (where, e))
    return server


def _foreign_lines(path):
    """Lines of an existing .tigervnc file that this port doesn't read:
    parameters it doesn't implement (e.g. TigerVNC-only ones) and
    comments.  Saving over the file keeps them, so a file shared with
    TigerVNC loses nothing.  Not in upstream, which only knows its own
    parameters and rewrites the whole file."""
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        return []
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    if text.startswith("\ufeff"):
        text = text[1:]
    lines = text.splitlines()
    if not lines or not lines[0].startswith(IDENTIFIER_STRING):
        return []
    keep = []
    for line in lines[1:]:
        if line.startswith("#"):
            keep.append(line)
            continue
        name, eq, _ = line.partition("=")
        name = name.strip()
        if eq and name.lower() != "servername" and _lookup(name) is None:
            keep.append(line)
    return keep


def save(path, server, opts):
    """Write ``server`` and non-default parameters as a .tigervnc file,
    like saveViewerParameters() (which also skips defaults).

    Unknown parameters and comments already in ``path`` are kept.  The
    file is replaced atomically, so a failed save leaves the old one.
    """
    from .parameters import PARAMS
    lines = [IDENTIFIER_STRING, "", "ServerName=%s" % encode_value(server)]
    for p in PARAMS:
        if p.name == "listen":
            continue
        value = getattr(opts, p.name.lower())
        if value == p.default:
            continue
        if p.kind is bool:
            value = "1" if value else "0"
        lines.append("%s=%s" % (p.name, encode_value(str(value))))
    lines += _foreign_lines(path)
    tmp = "%s.%d.tmp" % (path, os.getpid())
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(lines) + "\n")
        os.replace(tmp, path)
    except OSError as e:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise ConfigFileError("Failed to save \"%s\": %s" %
                              (path, e.strerror or e))
