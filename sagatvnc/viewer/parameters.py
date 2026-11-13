# SagatVNC Viewer - a Python port of the TigerVNC viewer
# Copyright (C) 2026 SagatVNC Viewer contributors
# Based on TigerVNC vncviewer/parameters.cxx and common/core/Configuration.cxx,
# Copyright (C) 2002-2005 RealVNC Ltd., Copyright 2011 Pierre Ossman for
# Cendio AB, Copyright 2012 Samuel Mannehed for Cendio AB, and TigerVNC
# contributors.
#
# This is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
"""Viewer parameters, parsed TigerVNC style.

Like vncviewer, parameters are case-insensitive and may be given as
``-Name value``, ``-Name=value``, ``--Name=value`` or ``Name=value``.
Boolean parameters given without a value are set to true.
"""

import os
import sys

from ..rfb import encodings as enc


class Param:
    def __init__(self, name, kind, default, help, aliases=()):
        self.name = name
        self.kind = kind          # bool, int or str
        self.default = default
        self.help = help
        self.aliases = aliases

    def convert(self, text):
        if self.kind is bool:
            t = text.strip().lower()
            if t in ("1", "on", "true", "yes"):
                return True
            if t in ("0", "off", "false", "no"):
                return False
            raise ValueError("Invalid boolean value %r for %s" %
                             (text, self.name))
        if self.kind is int:
            return int(text, 0)
        return text


PARAMS = [
    Param("Shared", bool, False,
          "Don't disconnect other viewers upon connection - share the "
          "desktop instead"),
    Param("ViewOnly", bool, False,
          "Don't send any mouse or keyboard events to the server"),
    Param("FullScreen", bool, False, "Enable full screen"),
    Param("Maximize", bool, False, "Maximize viewer window"),
    Param("geometry", str, "",
          "Specify size and position of viewer window (Tk geometry, "
          "e.g. 1024x768+0+0)"),
    Param("RemoteResize", bool, True,
          "Dynamically resize the remote desktop size as the size of the "
          "local client window changes (does not work with all servers; "
          "off: Scaling decides)"),
    Param("TitleStats", bool, True,
          "Show live traffic (UP:/DL:), the remote size, encoding and "
          "color depth in the title bar. SagatVNC only"),
    Param("Scaling", str, "Auto",
          "Scale the remote desktop to the window: Auto (fit the window, "
          "keeping the aspect ratio; Lanczos-3 when shrinking, bicubic "
          "when enlarging) or Off (1:1 with scrollbars). SagatVNC only; "
          "needs Pillow"),
    Param("RemoteCursorShape", bool, True,
          "Show the cursor shape the server sends (Windows only); off: "
          "always the local system arrow. The server is always asked for "
          "the shape, so it never draws its own cursor into the desktop "
          "image. SagatVNC only"),
    Param("BlankCursorAsIBeam", bool, True,
          "Show the local text cursor when the server sends a visible but "
          "fully transparent cursor. TigerVNC's winvnc does that for the "
          "Windows I-beam (a colour cursor without alpha), which otherwise "
          "disappears. Turn off if a server hides the pointer that way "
          "(e.g. during video). SagatVNC only"),
    Param("AlwaysCursor", bool, False,
          "Show local cursor when not provided by the server "
          "(with RemoteCursorShape)"),
    Param("CursorType", str, "Dot",
          "Specify which local cursor type should be used when "
          "AlwaysCursor is active (Dot, System)"),
    Param("RemoteDpiScale", int, 100,
          "Compensate a server that is not DPI aware (e.g. TigerVNC's "
          "winvnc on a Windows desktop scaled to 150%), where the remote "
          "pointer drifts further off towards the bottom right: set it to "
          "the remote display's scale in percent. Only for servers whose "
          "CLICKS land in the wrong place - not for a misplaced cursor "
          "picture, which the local cursor shape fixes. SagatVNC only",
          aliases=("DPI",)),
    Param("LocalInputMethod", bool, False,
          "Use the local input method (IME) in the viewer window. Off: "
          "keys go straight to the server, so use the remote computer's "
          "IME. SagatVNC only, Windows"),
    Param("DesktopSize", str, "",
          "Reconfigure the desktop size on the server to WxH upon connection"),
    Param("SecurityTypes", str, enc.DEFAULT_SECURITY,
          "Specify which security scheme to use (None, VncAuth, Plain, "
          "TLSNone, TLSVnc, TLSPlain, X509None, X509Vnc, X509Plain)"),
    Param("PreferredSecurity", str, "",
          "Security type to use when the server offers several that "
          "SecurityTypes allows (default: the safest). Picked in the login "
          "dialog. SagatVNC only"),
    Param("UserName", str, "",
          "User name for the login dialog (Plain, TLSPlain, X509Plain). "
          "The last name typed there is kept and saved by Save settings. "
          "Not in TigerVNC (RealVNC's name for it); default: the local "
          "user name"),
    Param("PasswordFile", str, "", "Password file for VNC authentication",
          aliases=("passwd",)),
    Param("X509CA", str, "",
          "X509 CA certificate file, trusted besides the system's CAs "
          "(default: x509_ca.pem in the SagatVNC config folder, if it "
          "exists). Certificates that don't verify can be accepted as "
          "an exception"),
    Param("X509CRL", str, "",
          "X509 CRL file (default: x509_crl.pem in the SagatVNC config "
          "folder, if it exists)"),
    Param("AutoSelect", bool, True, "Auto select encoding"),
    Param("PreferredEncoding", str, "Tight",
          "Preferred encoding to use (Tight, JPEG, ZRLE, Hextile, Raw)"),
    Param("FullColor", bool, True, "Use all available colors",
          aliases=("FullColour",)),
    Param("LowColorLevel", int, 2,
          "Color level to use on slow connections. 0 = Very Low (8 "
          "colors), 1 = Low (64 colors), 2 = Medium (256 colors)",
          aliases=("LowColourLevel",)),
    Param("QualityLevel", int, 8,
          "JPEG quality level, 0 = Low, 9 = High (needs Pillow)"),
    Param("NoJPEG", bool, False,
          "Disable lossy JPEG compression in Tight encoding"),
    Param("CustomCompressLevel", bool, False,
          "Use custom compression level as specified by CompressLevel"),
    Param("CompressLevel", int, 2,
          "Use specified compression level, 0 = Low, 9 = High"),
    Param("AcceptClipboard", bool, True, "Accept clipboard changes from the "
          "server"),
    Param("SendClipboard", bool, True, "Send clipboard changes to the server"),
    Param("MaxCutText", int, 256 * 1024,
          "Maximum permitted length of an incoming clipboard update"),
    Param("MenuKey", str, "F8", "The key which brings up the popup menu "
          "(a Tk keysym name, or empty to disable)"),
    Param("SystemKeys", bool, True,
          "Send special keys (Alt+Esc, Ctrl+Esc, Alt+F4, input-language "
          "hotkeys, ...) to the server while the viewer has focus, instead "
          "of letting the local system act on them. Ctrl+Alt+Del and "
          "Win+L always stay local. SagatVNC only (TigerVNC: "
          "FullscreenSystemKeys); Windows"),
    Param("PassPrintScreen", bool, False,
          "Send Print Screen to the server (off: local screenshot). "
          "SagatVNC only; Windows"),
    Param("PassAltTab", bool, False,
          "Send Alt+Tab to the server (off: switch local windows). "
          "SagatVNC only; Windows"),
    Param("PassWinKey", bool, False,
          "Send the Windows key and its combinations to the server (off: "
          "they act locally only). SagatVNC only; Windows"),
    Param("PassCtrlAltEnter", bool, False,
          "Send Ctrl+Alt+Enter to the server (off: it toggles full "
          "screen). SagatVNC only"),
    Param("LogLevel", int, 1,
          "How much to write to the log (log\\sagatvnc.log next to the "
          "program; stderr when run from a console): 0 = nothing, 1 = "
          "normal, 2 = also every key event (what the keyboard hook, the "
          "message queue and Tk saw, and what went to the server) and all "
          "clipboard traffic, for tracking down lost keys. SagatVNC only"),
    Param("PointerEventInterval", int, 17,
          "Time in milliseconds to rate-limit successive pointer events"),
    Param("AlertOnFatalError", bool, True,
          "Give a dialog on connection problems rather than exiting "
          "immediately"),
    Param("ConnectTimeout", int, 10,
          "Seconds to wait for the server while connecting (TCP, then each "
          "answer until the session starts); 0 = forever, as TigerVNC. "
          "SagatVNC only"),
    Param("ReconnectOnError", bool, True,
          "Give a dialog on connection problems with an option to "
          "reconnect"),
    Param("listen", bool, False,
          "Listen for incoming connections from VNC servers"),
]


class Options:
    def __init__(self):
        for p in PARAMS:
            setattr(self, p.name.lower(), p.default)
        self.server = ""
        self.config_file = ""

    def __getattr__(self, name):
        # Allow TigerVNC style CamelCase access as well
        lower = name.lower()
        if lower != name and lower in self.__dict__:
            return self.__dict__[lower]
        raise AttributeError(name)


def _lookup(name):
    lname = name.lower()
    for p in PARAMS:
        if p.name.lower() == lname or lname in (a.lower() for a in p.aliases):
            return p
    return None


class UsageError(Exception):
    pass


def usage(prog="sagatvncviewer"):
    lines = [
        "SagatVNC Viewer - a Python port of the TigerVNC viewer",
        "",
        "Usage: %s [parameters] [host][:displayNum]" % prog,
        "       %s [parameters] [host][::port]" % prog,
        "       %s [parameters] -listen [port]" % prog,
        "",
        "Parameters can be turned on with -<param> or off with -<param>=0",
        "Parameters which take a value can be specified as "
        "-<param> <value>",
        "Other valid forms are <param>=<value> -<param>=<value> "
        "--<param>=<value>",
        "Parameter names are case-insensitive.  The parameters are:",
        "",
    ]
    for p in PARAMS:
        default = p.default
        if p.kind is bool:
            default = "1" if default else "0"
        lines.append("  %-22s - %s (default=%s)" % (p.name, p.help, default))
    return "\n".join(lines)


def parse_args(argv):
    opts = Options()
    i = 0
    while i < len(argv):
        arg = argv[i]
        i += 1
        if arg in ("-h", "-help", "--help", "-?"):
            raise UsageError(usage())
        if arg in ("-v", "-version", "--version"):
            from .. import __version__
            raise UsageError("SagatVNC Viewer %s" % __version__)
        body = None
        if arg.startswith("--"):
            body = arg[2:]
        elif arg.startswith("-"):
            body = arg[1:]
        elif "=" in arg and _lookup(arg.split("=", 1)[0]):
            body = arg
        if body is None:
            if opts.server:
                raise UsageError("Unexpected argument %r\n\n%s" %
                                 (arg, usage()))
            opts.server = arg
            continue
        name, eq, value = body.partition("=")
        p = _lookup(name)
        if p is None:
            raise UsageError("Unrecognized option %r\n\n%s" % (arg, usage()))
        if not eq:
            if p.kind is bool:
                value = "1"
            else:
                if i >= len(argv):
                    raise UsageError("Missing value for %s" % arg)
                value = argv[i]
                i += 1
        try:
            setattr(opts, p.name.lower(), p.convert(value))
        except ValueError as e:
            raise UsageError(str(e))
    return opts


def config_dir():
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base, "SagatVNC")
    base = os.environ.get("XDG_CONFIG_HOME") or \
        os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "sagatvnc")


def parse_server(text, default_port=5900):
    """Split a server string like TigerVNC's getHostAndPort().

    ``host``, ``host:N`` (display N -> port 5900+N, or a port if N >= 100),
    ``host::port`` and ``[ipv6]:N`` are understood.
    """
    # Port of network::getHostAndPort() (common/network/TcpSocket.cxx)
    s = text.lstrip()
    if s.startswith("["):
        end = s.find("]")
        if end < 0:
            raise ValueError("Unmatched [ in hostname")
        host, rest = s[1:end], s[end + 1:]
        if not rest.strip():
            rest = ""
    else:
        host_end = s.rfind(":")
        if host_end < 0:
            host, rest = s, ""
        else:
            if host_end > 0 and s[host_end - 1] == ":":
                host_end -= 1                     # host::port
            port_start = s.find(":")
            if port_start != host_end:
                # More colons in the host: a bare IPv6 address, no port
                host, rest = s, ""
            else:
                host, rest = s[:host_end], s[host_end:]
    host = host.rstrip() or "localhost"
    if not rest:
        return host, default_port
    rest = rest.rstrip()
    try:
        if not rest.startswith(":"):
            raise ValueError
        if rest.startswith("::"):
            port = int(rest[2:])
        else:
            port = int(rest[1:])
            if port < 100:
                port += default_port
    except ValueError:
        raise ValueError("Invalid port specified in %r" % text)
    if not 0 < port < 65536:
        raise ValueError("Invalid port in %r" % text)
    return host, port
