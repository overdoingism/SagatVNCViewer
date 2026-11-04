# SagatVNC Viewer - a Python port of the TigerVNC viewer
# Copyright (C) 2026 SagatVNC Viewer contributors
# Based on TigerVNC vncviewer/ServerDialog.cxx and AuthDialog.cxx,
# Copyright (C) 2002-2005 RealVNC Ltd., Copyright 2011-2021 Pierre Ossman
# for Cendio AB, and TigerVNC contributors.
#
# This is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
"""Modal Tk dialogs: server selection, authentication, about."""

import os
import tkinter as tk
from tkinter import messagebox, ttk

from .. import __version__

ABOUT_TEXT = (
    "SagatVNC Viewer %s\n\n"
    "A Python port of the TigerVNC viewer.\n\n"
    "Copyright (C) 2026 SagatVNC Viewer contributors\n"
    "Based on TigerVNC, Copyright (C) 1999-2026 TigerVNC team and many\n"
    "others (see the TigerVNC source), and on VNC from RealVNC Ltd and\n"
    "AT&T Laboratories Cambridge.\n\n"
    "This is free software, licensed under the GNU General Public\n"
    "License version 2 or later.  See LICENSE for details." % __version__)


def show_about(parent):
    messagebox.showinfo("About SagatVNC Viewer", ABOUT_TEXT, parent=parent)


class _Modal(tk.Toplevel):
    def __init__(self, parent, title):
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.result = None
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self.bind("<Escape>", lambda e: self.cancel())
        self.bind("<Return>", lambda e: self.ok())

    def run(self):
        self.update_idletasks()
        # Centre on screen
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        x = (self.winfo_screenwidth() - w) // 2
        y = (self.winfo_screenheight() - h) // 3
        self.geometry("+%d+%d" % (x, y))
        self.deiconify()
        self.lift()
        self.attributes("-topmost", True)
        self.after(200, lambda: self.attributes("-topmost", False))
        self.grab_set()
        self.focus_force()
        self.wait_window()
        return self.result

    def ok(self):
        self.destroy()

    def cancel(self):
        self.result = None
        self.destroy()


class ServerDialog(_Modal):
    def __init__(self, parent, history):
        super().__init__(parent, "VNC Viewer: Connection Details")
        frame = ttk.Frame(self, padding=12)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="VNC server:").grid(row=0, column=0, sticky="w")
        self.var = tk.StringVar(value=history[0] if history else "")
        self.entry = ttk.Combobox(frame, textvariable=self.var,
                                  values=history, width=36)
        self.entry.grid(row=0, column=1, columnspan=3, sticky="we",
                        padx=(8, 0))
        buttons = ttk.Frame(frame)
        buttons.grid(row=1, column=0, columnspan=4, sticky="e", pady=(12, 0))
        ttk.Button(buttons, text="About...",
                   command=lambda: show_about(self)).pack(side="left")
        ttk.Button(buttons, text="Cancel",
                   command=self.cancel).pack(side="left", padx=(24, 4))
        ttk.Button(buttons, text="Connect",
                   command=self.ok).pack(side="left")
        self.entry.focus_set()
        self.entry.select_range(0, "end")

    def ok(self):
        self.result = self.var.get().strip()
        if not self.result:
            return
        self.destroy()


class AuthDialog(_Modal):
    def __init__(self, parent, need_username, secure, host,
                 offer_keep=False):
        super().__init__(parent, "VNC authentication")
        frame = ttk.Frame(self, padding=12)
        frame.pack(fill="both", expand=True)
        banner = ("This connection is secure" if secure else
                  "This connection is not secure")
        ttk.Label(frame, text="%s\nServer: %s" % (banner, host)).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        self.user = tk.StringVar(value=os.environ.get("USER") or
                                 os.environ.get("USERNAME") or "")
        self.password = tk.StringVar()
        row = 1
        if need_username:
            ttk.Label(frame, text="Username:").grid(row=row, column=0,
                                                    sticky="w")
            ue = ttk.Entry(frame, textvariable=self.user, width=28)
            ue.grid(row=row, column=1, sticky="we", padx=(8, 0))
            row += 1
        ttk.Label(frame, text="Password:").grid(row=row, column=0, sticky="w")
        pe = ttk.Entry(frame, textvariable=self.password, show="•",
                       width=28)
        pe.grid(row=row, column=1, sticky="we", padx=(8, 0), pady=(4, 0))
        row += 1
        # AuthDialog.cxx: only offered when reconnecting is possible
        self.keep = tk.BooleanVar(value=False)
        if offer_keep:
            ttk.Checkbutton(frame, text="Keep password for reconnect",
                            variable=self.keep).grid(
                row=row, column=0, columnspan=2, sticky="w", pady=(6, 0))
            row += 1
        buttons = ttk.Frame(frame)
        buttons.grid(row=row, column=0, columnspan=2, sticky="e",
                     pady=(12, 0))
        ttk.Button(buttons, text="Cancel", command=self.cancel).pack(
            side="left", padx=(0, 4))
        ttk.Button(buttons, text="OK", command=self.ok).pack(side="left")
        (ue if need_username else pe).focus_set()
        self.need_username = need_username

    def ok(self):
        self.result = (self.user.get() if self.need_username else "",
                       self.password.get(), self.keep.get())
        self.destroy()
