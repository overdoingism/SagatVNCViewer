# SagatVNC Viewer - a Python port of the TigerVNC viewer
# Copyright (C) 2026 SagatVNC Viewer contributors
# Based on TigerVNC vncviewer/ServerDialog.cxx and AuthDialog.cxx,
# Copyright (C) 2002-2005 RealVNC Ltd., Copyright 2011-2021 Pierre Ossman
# for Cendio AB, and TigerVNC contributors.
#
# This is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
"""Modal Tk dialogs: server selection, authentication, about."""

import os
import tkinter as tk
from tkinter import font as tkfont, ttk

from .. import __version__
from ..rfb.security import describe as describe_security
from . import icon
from .win32 import bring_to_front

ABOUT_TEXT = (
    "SagatVNC Viewer %s\n\n"
    "A Python port of the TigerVNC viewer.\n\n"
    "Copyright (C) 2026 SagatVNC Viewer contributors\n"
    "Based on TigerVNC, Copyright (C) 1999-2026 TigerVNC team and many\n"
    "others (see the TigerVNC source), and on VNC from RealVNC Ltd and\n"
    "AT&T Laboratories Cambridge.\n\n"
    "This is free software, licensed under the GNU General Public\n"
    "License version 3 or later.  See LICENSE for details." % __version__)


def show_about(parent):
    AboutDialog(parent).run()


# Layout (in Tk "points" scaled by DPI awareness: pixels at 100%)
PAD = 20              # window margin
GAP = 10              # between rows
HEADER_ICON = 48      # header logo size (one of icon_data.PNGS)
ENTRY_WIDTH = 34      # characters
SECURE_COLOUR = "#1e7b34"
CAUTION_COLOUR = "#9a6700"
INSECURE_COLOUR = "#c5221f"
HINT_COLOUR = "#5f6368"
CERT_TEXT_WIDTH = 400  # certificate question, in points


def _styles(widget):
    """Fonts derived from the system's (Segoe UI on Windows)."""
    style = ttk.Style(widget)
    base = tkfont.nametofont("TkDefaultFont").actual()
    style.configure("Heading.TLabel",
                    font=(base["family"], base["size"] + 4, "bold"))
    style.configure("Hint.TLabel", foreground=HINT_COLOUR)
    style.configure("Secure.TLabel", foreground=SECURE_COLOUR)
    style.configure("Caution.TLabel", foreground=CAUTION_COLOUR)
    style.configure("Insecure.TLabel", foreground=INSECURE_COLOUR)
    style.configure("Bar.TFrame", background=_bar_colour(widget))
    style.configure("Bar.TButton", padding=(14, 4))
    return style


def _bar_colour(widget):
    """The button bar: a shade darker than the dialog background."""
    bg = ttk.Style(widget).lookup("TFrame", "background")
    r, g, b = (v // 257 for v in widget.winfo_rgb(bg or "SystemButtonFace"))
    return "#%02x%02x%02x" % (max(0, r - 12), max(0, g - 12), max(0, b - 12))


class _Modal(tk.Toplevel):
    """A dialog: header (logo, title, subtitle), a body, a button bar."""

    def __init__(self, parent, title):
        super().__init__(parent)
        self.withdraw()
        self.title(title)
        self.resizable(False, False)
        self.result = None
        self.initial_focus = None    # the field the caret starts in
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self.bind("<Escape>", lambda e: self.cancel())
        self.bind("<Return>", lambda e: self.ok())
        _styles(self)
        self.outer = ttk.Frame(self, padding=(PAD, PAD, PAD, PAD - 4))
        self.outer.pack(fill="both", expand=True)
        self.outer.columnconfigure(0, weight=1)

    def header(self, heading, subtitle=None, subtitle_style="Hint.TLabel"):
        head = ttk.Frame(self.outer)
        head.grid(row=0, column=0, sticky="we", pady=(0, PAD - 4))
        try:
            self.logo = icon.photo(self, HEADER_ICON)
            ttk.Label(head, image=self.logo).grid(row=0, column=0, rowspan=2,
                                                  sticky="nw",
                                                  padx=(0, 14))
        except tk.TclError:
            self.logo = None
        ttk.Label(head, text=heading, style="Heading.TLabel").grid(
            row=0, column=1, sticky="sw")
        self.subtitle = None
        if subtitle:
            self.subtitle = ttk.Label(head, text=subtitle,
                                      style=subtitle_style)
            self.subtitle.grid(row=1, column=1, sticky="nw", pady=(2, 0))
        return head

    def body(self):
        frame = ttk.Frame(self.outer)
        frame.grid(row=1, column=0, sticky="nsew")
        frame.columnconfigure(1, weight=1)
        return frame

    def button_bar(self, buttons, left=()):
        """``buttons``: (text, command) right-aligned, the last one is the
        default (Return).  ``left``: extra buttons on the left."""
        bar = ttk.Frame(self, style="Bar.TFrame", padding=(PAD, 12))
        ttk.Separator(self, orient="horizontal").pack(fill="x")
        bar.pack(fill="x")
        for text, command in left:
            ttk.Button(bar, text=text, command=command,
                       style="Bar.TButton").pack(side="left")
        self.buttons = {}
        for i, (text, command) in enumerate(reversed(buttons)):
            b = ttk.Button(bar, text=text, command=command,
                           style="Bar.TButton",
                           default="active" if i == 0 else "normal")
            b.pack(side="right", padx=(8, 0) if i else 0)
            self.buttons[text] = b
        return bar

    def place(self):
        """Centre on the screen, a bit above the middle."""
        self.update_idletasks()
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        x = (self.winfo_screenwidth() - w) // 2
        y = (self.winfo_screenheight() - h) // 3
        self.geometry("+%d+%d" % (x, y))

    def run(self):
        self.place()
        self.deiconify()
        self.lift()
        self.attributes("-topmost", True)
        self.after(200, lambda: self.attributes("-topmost", False))
        # Focus only sticks once the window is really on screen; before,
        # Tk and Windows may hand it to the toplevel itself, leaving no
        # caret in the text field
        self.wait_visibility()
        bring_to_front(self)
        self.grab_set()
        self._focus_initial()
        # Windows may activate the window a moment later and reset focus
        self.after(150, self._focus_initial)
        self.wait_window()
        return self.result

    def _focus_initial(self):
        """Put the caret in the initial field (not on the toplevel, which
        is what focus_force() on the dialog itself did)."""
        widget = self.initial_focus or self
        try:
            if not self.winfo_exists():
                return
            widget.focus_force()
            if isinstance(widget, (ttk.Entry, tk.Entry)):
                widget.icursor("end")
        except tk.TclError:
            pass

    def ok(self):
        self.destroy()

    def cancel(self):
        self.result = None
        self.destroy()


class AboutDialog(_Modal):
    """about_vncviewer(), with our logo instead of a plain message box."""

    def __init__(self, parent):
        super().__init__(parent, "About SagatVNC Viewer")
        frame = self.body()
        try:
            self.big_logo = icon.photo(self, 128)
            ttk.Label(frame, image=self.big_logo).grid(
                row=0, column=0, sticky="n", padx=(0, PAD))
        except tk.TclError:
            self.big_logo = None
        name, rest = ABOUT_TEXT.split("\n\n", 1)
        text = ttk.Frame(frame)
        text.grid(row=0, column=1, sticky="nw")
        ttk.Label(text, text=name, style="Heading.TLabel").pack(anchor="w")
        ttk.Label(text, text=rest, justify="left").pack(anchor="w",
                                                        pady=(GAP, 0))
        self.button_bar([("Close", self.ok)])


class ConnectingWindow(_Modal):
    """Shown while connecting, until the desktop appears (not in TigerVNC,
    which shows nothing): where it is, and a way to give up.  Not modal;
    the viewer hides it while it asks for a password or a certificate."""

    def __init__(self, parent, host, on_cancel):
        super().__init__(parent, "SagatVNC Viewer")
        self.on_cancel = on_cancel
        self.header("Connecting to %s" % host, "Connecting...")
        frame = self.body()
        self.bar = ttk.Progressbar(frame, mode="indeterminate", length=360)
        self.bar.grid(row=0, column=0, columnspan=2, sticky="we",
                      pady=(0, GAP))
        self.button_bar([("Cancel", self.cancel)])

    def set_status(self, text):
        self.subtitle.configure(text=text)

    def show(self):
        if self.state() == "normal":
            return
        self.place()
        self.deiconify()
        self.lift()
        self.bar.start(15)

    def hide(self):
        self.bar.stop()
        self.withdraw()

    def ok(self):                  # Return does nothing here
        pass

    def cancel(self):
        self.on_cancel()


class ServerDialog(_Modal):
    """ServerDialog.cxx: where to connect."""

    def __init__(self, parent, history):
        super().__init__(parent, "SagatVNC Viewer: Connection Details")
        self.header("Connect to a VNC server",
                    "host:display, host::port, or a .tigervnc file")
        frame = self.body()
        ttk.Label(frame, text="VNC server:").grid(row=0, column=0,
                                                  sticky="w", padx=(0, 12))
        self.var = tk.StringVar(value=history[0] if history else "")
        self.entry = ttk.Combobox(frame, textvariable=self.var,
                                  values=history, width=ENTRY_WIDTH + 6)
        self.entry.grid(row=0, column=1, sticky="we", ipady=3)
        self.button_bar([("Cancel", self.cancel), ("Connect", self.ok)],
                        left=[("About...", lambda: show_about(self))])
        self.entry.select_range(0, "end")
        self.initial_focus = self.entry

    def ok(self):
        self.result = self.var.get().strip()
        if not self.result:
            return
        self.destroy()


CERT_TITLES = {
    "authority": "Unknown certificate issuer",
    "not_yet_valid": "Certificate is not yet valid",
    "expired": "Expired certificate",
    "algorithm": "Insecure certificate algorithm",
    "hostname": "Certificate hostname mismatch",
    "crl": "Certificate revocation unknown",
}
_CERT_NEW = {
    "authority": "This certificate has been signed by an unknown "
                 "authority:",
    "not_yet_valid": "This certificate is not yet valid:",
    "expired": "This certificate has expired:",
    "algorithm": "This certificate uses an insecure algorithm:",
    "hostname": "The specified hostname \"%s\" does not match the "
                "certificate provided by the server:",
    # Not in upstream (see security.CERT_PROBLEMS)
    "crl": "Whether this certificate was revoked can't be checked: the "
           "revocation list (X509CRL) has no list from its issuer:",
}
_CERT_CHANGED = {
    "authority": "has been signed by an unknown authority",
    "not_yet_valid": "is not yet valid",
    "expired": "has expired",
    "algorithm": "uses an insecure algorithm",
    "hostname": "does not match the hostname \"%s\"",
    "crl": "can't be checked for revocation",
}


def certificate_question(info):
    """(title, text) for a certificate the user may make an exception for,
    as CConn::verifyCertificate() words it.  ``info``: host, fingerprint,
    changed, problem (kind, OpenSSL's message) and, if they could be read,
    subject, issuer, valid, names."""
    kind, detail = info.get("problem") or ("authority", "")
    host = info["host"]
    if info.get("changed"):
        title = "Unexpected server certificate"
        lead = ("This host is previously known with a different "
                "certificate, and the new certificate %s:" %
                _CERT_CHANGED.get(kind, "has a problem"))
    else:
        title = CERT_TITLES.get(kind, "Server certificate problem")
        lead = _CERT_NEW.get(kind, "This certificate has a problem:")
    if "%s" in lead:
        lead = lead % host
    cert = ["%s: %s" % (label, info[key]) for key, label in (
        ("subject", "Subject"), ("issuer", "Issuer"), ("valid", "Valid"),
        ("names", "Names")) if info.get(key)]
    fp = info["fingerprint"]
    cert.append("SHA-256: %s" % " ".join(fp[i:i + 16]
                                         for i in range(0, len(fp), 16)))
    if detail:
        cert.append("(%s)" % detail)
    text = "\n\n".join((
        lead, "\n".join(cert),
        "Someone could be trying to impersonate the site and you should "
        "not continue.",
        "Do you want to make an exception for this server?"))
    return title, text


class CertificateDialog(_Modal):
    """CConn::verifyCertificate()'s question: Cancel or Add exception
    (Cancel is the default, as nothing should be accepted by a stray
    Return)."""

    def __init__(self, parent, info):
        title, text = certificate_question(info)
        super().__init__(parent, title)
        self.header(title, info["host"], subtitle_style="Insecure.TLabel")
        frame = self.body()
        lead, cert, rest = text.split("\n\n", 2)
        # Points, so the width follows the DPI like the fonts do
        wrap = "%dp" % CERT_TEXT_WIDTH
        ttk.Label(frame, text=lead, wraplength=wrap, justify="left").grid(
            row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(frame, text=cert, justify="left",
                  font=("Consolas", 9), wraplength=wrap).grid(
            row=1, column=0, columnspan=2, sticky="w", pady=GAP)
        ttk.Label(frame, text=rest, wraplength=wrap, justify="left").grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(0, GAP))
        self.button_bar([("Add exception", self.accept),
                         ("Cancel", self.cancel)])
        self.initial_focus = self.buttons["Cancel"]

    def accept(self):
        self.result = True
        self.destroy()

    def ok(self):
        """Return: the focused button, which starts out as Cancel."""
        if self.focus_get() is self.buttons["Add exception"]:
            self.accept()
        else:
            self.cancel()


def security_choice_label(name, num):
    """How a security type reads in the login dialog's list."""
    encryption, _ = describe_security(num)
    what = {"x509": "encrypted, server verified",
            "tls": "encrypted, server not verified"}.get(encryption,
                                                         "not encrypted")
    return "%s  (%s)" % (name, what)


def security_banner(secure, security):
    """(label style, text) for the login dialog.

    ``secure``: the server's identity was verified (upstream's
    isSecure()); ``security``: (type name, TLS version or None).
    """
    tls = bool(security and security[1])
    if secure:
        return ("Secure.TLabel",
                "\u2714  Encrypted, server identity verified")
    if tls:
        # Anonymous TLS: nobody can read along, but nobody checked who is
        # at the other end either
        return ("Caution.TLabel",
                "\u26a0  Encrypted, but the server's identity is not "
                "verified")
    return ("Insecure.TLabel",
            "\u26a0  Not encrypted: the screen and your keys travel in "
            "plain text")


class AuthDialog(_Modal):
    """AuthDialog.cxx: username and password."""

    def __init__(self, parent, need_username, secure, host,
                 offer_keep=False, security=None, choices=(), current=None):
        """``choices``: (number, name) of the security types this server
        offered that we could use, safest first; ``current`` is the one in
        use.  Picking another makes OK reconnect with it (SagatVNC)."""
        super().__init__(parent, "VNC authentication")
        self.header("Password required" if not need_username else
                    "Sign in", "Server: %s" % host)
        frame = self.body()
        self.user = tk.StringVar(value=os.environ.get("USER") or
                                 os.environ.get("USERNAME") or "")
        self.password = tk.StringVar()
        row = 0
        if need_username:
            ttk.Label(frame, text="Username:").grid(row=row, column=0,
                                                    sticky="w", padx=(0, 12))
            ue = ttk.Entry(frame, textvariable=self.user, width=ENTRY_WIDTH)
            ue.grid(row=row, column=1, sticky="we", ipady=3, pady=(0, GAP))
            row += 1
        ttk.Label(frame, text="Password:").grid(row=row, column=0,
                                                sticky="w", padx=(0, 12))
        pe = ttk.Entry(frame, textvariable=self.password, show="\u2022",
                       width=ENTRY_WIDTH)
        pe.grid(row=row, column=1, sticky="we", ipady=3)
        row += 1
        # AuthDialog.cxx: only offered when reconnecting is possible
        self.keep = tk.BooleanVar(value=False)
        if offer_keep:
            ttk.Checkbutton(frame, text="Keep password for reconnect",
                            variable=self.keep).grid(
                row=row, column=1, sticky="w", pady=(GAP, 0))
            row += 1
        # The security in use, and the others this server offers
        self.choices = list(choices)
        self.current = current
        self.secure = secure
        self.security = security
        self.choice = tk.StringVar()
        if len(self.choices) > 1:
            ttk.Label(frame, text="Security:").grid(
                row=row, column=0, sticky="w", padx=(0, 12),
                pady=(GAP, 0))
            labels = [security_choice_label(name, num)
                      for num, name in self.choices]
            combo = ttk.Combobox(frame, values=labels, state="readonly",
                                 textvariable=self.choice,
                                 width=ENTRY_WIDTH)
            nums = [num for num, _ in self.choices]
            combo.current(nums.index(current) if current in nums else 0)
            combo.grid(row=row, column=1, sticky="we", pady=(GAP, 0))
            combo.bind("<<ComboboxSelected>>",
                       lambda e: self._choice_changed())
            self.combo = combo
            row += 1
        else:
            self.combo = None
        # Upstream shows a coloured "secure / not secure" banner; here it
        # tells the three cases apart
        style, text = security_banner(secure, security)
        self.banner = ttk.Label(frame, style=style, text=text)
        self.banner.grid(row=row, column=0, columnspan=2, sticky="w",
                         pady=(PAD - 4, 0))
        row += 1
        self.detail = ttk.Label(frame, style="Hint.TLabel",
                                text=self._detail_text())
        if self.detail["text"]:
            self.detail.grid(row=row, column=0, columnspan=2, sticky="w",
                             pady=(2, 0))
        self.button_bar([("Cancel", self.cancel), ("OK", self.ok)])
        # The username field only when it still needs typing (it is
        # prefilled with the local user name); otherwise the password
        self.initial_focus = ue if need_username and \
            not self.user.get() else pe
        self.need_username = need_username

    def _detail_text(self):
        if self.security and self.security[0]:
            return " \u00b7 ".join(p for p in self.security if p)
        return ""

    def selected_security(self):
        """The security type picked in the list, if it isn't the one in
        use (then OK reconnects with it)."""
        if not self.combo:
            return None
        num = self.choices[self.combo.current()][0]
        return None if num == self.current else num

    def _choice_changed(self):
        """Show what the picked type would give, and that OK reconnects."""
        other = self.selected_security()
        if other is None:
            style, text = security_banner(self.secure, self.security)
            detail, ok = self._detail_text(), "OK"
        else:
            encryption, _ = describe_security(other)
            style, text = security_banner(encryption == "x509",
                                          ("", "TLS" if encryption else None))
            name = dict(self.choices)[other]
            detail = "OK reconnects with %s" % name
            ok = "Reconnect"
        self.banner.configure(style=style, text=text)
        self.detail.configure(text=detail)
        if detail:
            self.detail.grid()
        self.buttons["OK"].configure(text=ok)

    def ok(self):
        self.result = (self.user.get() if self.need_username else "",
                       self.password.get(), self.keep.get(),
                       self.selected_security())
        self.destroy()
