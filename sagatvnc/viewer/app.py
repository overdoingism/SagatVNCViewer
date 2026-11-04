# SagatVNC Viewer - a Python port of the TigerVNC viewer
# Copyright (C) 2026 SagatVNC Viewer contributors
# Based on TigerVNC vncviewer/vncviewer.cxx, CConn.cxx, DesktopWindow.cxx
# and Viewport.cxx, Copyright (C) 2002-2005 RealVNC Ltd., Copyright
# 2009-2021 Pierre Ossman for Cendio AB, Copyright (C) 2011 D. R.
# Commander, and TigerVNC contributors.
#
# This is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
"""The Tk viewer application: desktop window, input and the F8 menu."""

import os
import queue
import socket
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox

from .. import __version__
from ..rfb import encodings as enc
from ..rfb.connection import Connection, ConnectionOptions
from ..rfb.des import deobfuscate_password
from ..rfb.security import AuthFailure, connect_socket, parse_security_types
from . import dialogs
from .keysyms import NAMED, event_to_keysym
from .parameters import config_dir, parse_server

POLL_MS = 10
MAX_RECTS_PER_PAINT = 32
HISTORY_FILE = "history"


def log(msg):
    print("%s: %s" % (time.strftime("%H:%M:%S"), msg), file=sys.stderr,
          flush=True)


class Cancelled(Exception):
    pass


# ---------------------------------------------------------------------------
# Bridge between the network thread and the Tk thread
# ---------------------------------------------------------------------------
class ConnectionHandler:
    """Receives Connection callbacks on the network thread and forwards
    them to the GUI through a queue."""

    def __init__(self, app):
        self.app = app
        self.events = queue.Queue()
        self.damage = []
        self.damage_lock = threading.Lock()

    def _call_gui(self, func, *args):
        """Run ``func`` on the Tk thread and wait for its result."""
        done = threading.Event()
        box = {}

        def run():
            try:
                box["value"] = func(*args)
            except BaseException as e:   # pass to network thread
                box["error"] = e
            done.set()
        self.events.put(("call", run))
        done.wait()
        if "error" in box:
            raise box["error"]
        return box["value"]

    def log(self, msg):
        log(msg)

    def get_credentials(self, need_username):
        return self._call_gui(self.app.ask_credentials, need_username)

    def verify_certificate(self, info):
        return self._call_gui(self.app.ask_certificate, info)

    def server_init(self, width, height, name):
        self.events.put(("init", width, height, name))

    def framebuffer_resized(self, width, height):
        with self.damage_lock:
            self.damage = []
        self.events.put(("resize", width, height))

    def framebuffer_updated(self, rects):
        if rects:
            with self.damage_lock:
                self.damage.extend(rects)

    def take_damage(self):
        with self.damage_lock:
            d, self.damage = self.damage, []
        return d

    def bell(self):
        self.events.put(("bell",))

    def server_cut_text(self, text):
        self.events.put(("cuttext", text))

    def name_changed(self, name):
        self.events.put(("name", name))


# ---------------------------------------------------------------------------
# The application
# ---------------------------------------------------------------------------
class ViewerApp:
    def __init__(self, options):
        self.opts = options
        self.root = tk.Tk(className="SagatVNC")
        self.root.withdraw()
        self.root.title("SagatVNC Viewer")
        self.conn = None
        self.handler = None
        self.thread = None
        self.image = None
        self.canvas = None
        self.host = ""
        self.port = 0
        self.exit_code = 0
        self.fullscreen = False
        self.down_keys = {}
        self.button_mask = 0
        self.pending_pointer = None
        self.last_pointer_time = 0.0
        self.pointer_job = None
        self.resize_job = None
        self.last_clipboard = None
        self.ctrl_var = tk.BooleanVar(value=False)
        self.alt_var = tk.BooleanVar(value=False)
        self.fullscreen_var = tk.BooleanVar(value=False)
        self.viewonly_var = tk.BooleanVar(value=bool(options.viewonly))
        self.desktop_size_sent = False
        self.menu = None
        self.offset = (0, 0)
        self.last_pointer_pos = (0, 0)

    # -- history ------------------------------------------------------------
    def _history_path(self):
        return os.path.join(config_dir(), HISTORY_FILE)

    def load_history(self):
        try:
            with open(self._history_path(), encoding="utf-8") as f:
                return [line.strip() for line in f if line.strip()][:20]
        except OSError:
            return []

    def save_history(self, server):
        hist = [server] + [h for h in self.load_history() if h != server]
        try:
            os.makedirs(config_dir(), exist_ok=True)
            with open(self._history_path(), "w", encoding="utf-8") as f:
                f.write("\n".join(hist[:20]) + "\n")
        except OSError as e:
            log("Failed to save history: %s" % e)

    # -- start up -----------------------------------------------------------
    def run(self):
        try:
            if self.opts.listen:
                sock = self._listen()
            else:
                server = self.opts.server
                if not server:
                    server = dialogs.ServerDialog(self.root,
                                                  self.load_history()).run()
                    if not server:
                        return 1
                self.save_history(server)
                self.host, self.port = parse_server(server)
                log("Connecting to %s port %d" % (self.host, self.port))
                sock = connect_socket(self.host, self.port)
        except (OSError, ValueError) as e:
            self.fatal("Failed to connect to \"%s\":\n\n%s" %
                       (self.opts.server or self.host, e), reconnect=False)
            return 1
        self.start_connection(sock)
        self.root.mainloop()
        return self.exit_code

    def _listen(self):
        port = 5500
        if self.opts.server:
            port = int(self.opts.server)
        srv = socket.create_server(("", port), family=socket.AF_INET6,
                                   dualstack_ipv6=True) \
            if socket.has_dualstack_ipv6() else \
            socket.create_server(("", port))
        log("Listening on port %d" % port)
        srv.settimeout(None)
        sock, addr = srv.accept()
        srv.close()
        self.host, self.port = addr[0], addr[1]
        log("Accepted connection from %s" % (addr[0],))
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        return sock

    def connection_options(self):
        o = self.opts
        co = ConnectionOptions()
        co.shared = bool(o.shared)
        co.security_types = parse_security_types(o.securitytypes)
        co.x509_ca = o.x509ca
        co.known_hosts = os.path.join(config_dir(), "x509_known_hosts")
        co.auto_select = bool(o.autoselect)
        pe = o.preferredencoding
        for name, value in enc.NAMES.items():
            if name.lower() == pe.lower():
                co.preferred_encoding = value
                break
        else:
            raise ValueError("Unknown encoding %r" % pe)
        co.custom_compress_level = bool(o.customcompresslevel)
        co.compress_level = o.compresslevel
        return co

    def start_connection(self, sock):
        self.handler = ConnectionHandler(self)
        self.conn = Connection(sock, self.host, self.handler,
                               self.connection_options())
        self.thread = threading.Thread(target=self._network_thread,
                                       name="rfb", daemon=True)
        self.thread.start()
        self.root.after(POLL_MS, self.poll)

    def _network_thread(self):
        try:
            self.conn.run()
            self.handler.events.put(("closed", None))
        except Cancelled:
            self.handler.events.put(("closed", "cancelled"))
        except AuthFailure as e:
            self.handler.events.put(("error", "Authentication failed: %s" %
                                     e))
        except Exception as e:     # report anything else to the user
            if self.conn.closed:
                self.handler.events.put(("closed", None))
            else:
                self.handler.events.put(("error", "%s: %s" %
                                         (type(e).__name__, e)))

    # -- callbacks run on the GUI thread --------------------------------------
    def ask_credentials(self, need_username):
        if not need_username and self.opts.passwordfile:
            try:
                with open(os.path.expanduser(self.opts.passwordfile),
                          "rb") as f:
                    return "", deobfuscate_password(f.read(8))
            except OSError as e:
                log("Failed to read password file: %s" % e)
        secure = bool(self.conn.security and self.conn.security.secure)
        res = dialogs.AuthDialog(self.root, need_username, secure,
                                 self.host).run()
        if res is None:
            raise Cancelled()
        return res

    def ask_certificate(self, info):
        if info["changed"]:
            msg = ("The certificate of %s has CHANGED since you last "
                   "connected!\n\nSHA-256 fingerprint:\n%s\n\nSomeone may "
                   "be intercepting the connection.  Accept the new "
                   "certificate anyway?" % (info["host"], info["fingerprint"]))
            icon = "warning"
        else:
            msg = ("The server %s presented a certificate that is not "
                   "known yet.\n\nSHA-256 fingerprint:\n%s\n\nDo you want to "
                   "trust it and continue?" % (info["host"],
                                              info["fingerprint"]))
            icon = "question"
        return messagebox.askyesno("Unknown certificate", msg, icon=icon,
                                   parent=self.root)

    # -- event pump ----------------------------------------------------------
    def poll(self):
        try:
            while True:
                ev = self.handler.events.get_nowait()
                kind = ev[0]
                if kind == "call":
                    ev[1]()
                elif kind == "init":
                    self.create_desktop(*ev[1:])
                elif kind == "resize":
                    self.resize_framebuffer(*ev[1:])
                elif kind == "bell":
                    self.root.bell()
                elif kind == "cuttext":
                    self.server_cut_text(ev[1])
                elif kind == "name":
                    self.set_title(ev[1])
                elif kind == "error":
                    self.fatal(ev[1])
                    return
                elif kind == "closed":
                    if ev[1] != "cancelled":
                        log("The connection was closed")
                    self.quit()
                    return
        except queue.Empty:
            pass
        self.paint()
        self.root.after(POLL_MS, self.poll)

    def fatal(self, msg, reconnect=True):
        log(msg)
        self.exit_code = 1
        if self.conn:
            self.conn.close()
        if self.opts.alertonfatalerror:
            if reconnect and self.opts.reconnectonerror and not \
                    self.opts.listen:
                if messagebox.askyesno("Connection error",
                                       msg + "\n\nAttempt to reconnect?",
                                       icon="error", parent=self.root):
                    self.reconnect()
                    return
            else:
                messagebox.showerror("Connection error", msg,
                                     parent=self.root)
        self.quit()

    def reconnect(self):
        try:
            sock = connect_socket(self.host, self.port)
        except OSError as e:
            self.fatal("Failed to connect to %s:%d:\n\n%s" %
                       (self.host, self.port, e))
            return
        self.exit_code = 0
        self.desktop_size_sent = False
        self.start_connection(sock)

    def quit(self):
        if self.conn:
            self.conn.close()
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    # -- desktop window ------------------------------------------------------
    def set_title(self, name):
        self.root.title("%s - SagatVNC Viewer" % name)

    def create_desktop(self, width, height, name):
        self.set_title(name)
        if self.canvas is None:
            root = self.root
            root.configure(background="black")
            self.canvas = tk.Canvas(root, highlightthickness=0, borderwidth=0,
                                    background="black", cursor="dot",
                                    takefocus=True)
            self.hbar = tk.Scrollbar(root, orient="horizontal",
                                     command=self.canvas.xview)
            self.vbar = tk.Scrollbar(root, orient="vertical",
                                     command=self.canvas.yview)
            self.canvas.configure(xscrollcommand=self.hbar.set,
                                  yscrollcommand=self.vbar.set)
            self.canvas.grid(row=0, column=0, sticky="nsew")
            root.grid_rowconfigure(0, weight=1)
            root.grid_columnconfigure(0, weight=1)
            self.image = tk.PhotoImage(width=width, height=height)
            self.image_item = self.canvas.create_image(0, 0, anchor="nw",
                                                       image=self.image)
            self.bind_input()
            root.protocol("WM_DELETE_WINDOW", self.quit)
            self.build_menu()
        self.resize_framebuffer(width, height, initial=True)
        self.root.deiconify()
        if self.opts.maximize:
            try:
                self.root.state("zoomed")
            except tk.TclError:
                self.root.attributes("-zoomed", True)
        if self.opts.fullscreen:
            self.set_fullscreen(True)
        self.canvas.focus_set()

    def resize_framebuffer(self, width, height, initial=False):
        self.image.configure(width=width, height=height)
        self.canvas.configure(scrollregion=(0, 0, width, height))
        if not self.fullscreen and not self.opts.maximize:
            if initial and self.opts.geometry:
                self.root.geometry(self.opts.geometry)
            else:
                sw = self.root.winfo_screenwidth()
                sh = self.root.winfo_screenheight()
                self.root.geometry("%dx%d" % (min(width, sw - 40),
                                              min(height, sh - 80)))
        with self.handler.damage_lock:
            self.handler.damage = [(0, 0, width, height)]
        self.update_scrollbars()

    def update_scrollbars(self):
        if not self.canvas or not self.conn or not self.conn.fb:
            return
        fbw, fbh = self.conn.fb.width, self.conn.fb.height
        cw = self.root.winfo_width()
        ch = self.root.winfo_height()
        need_h = cw < fbw and not self.fullscreen
        need_v = ch < fbh and not self.fullscreen
        if need_h:
            self.hbar.grid(row=1, column=0, sticky="we")
        else:
            self.hbar.grid_remove()
        if need_v:
            self.vbar.grid(row=0, column=1, sticky="ns")
        else:
            self.vbar.grid_remove()
        # Centre the desktop when the window is larger than it
        ox = max(0, (self.canvas.winfo_width() - fbw) // 2)
        oy = max(0, (self.canvas.winfo_height() - fbh) // 2)
        self.canvas.coords(self.image_item, ox, oy)
        self.offset = (ox, oy)

    def paint(self):
        if self.image is None or self.handler is None:
            return
        self.check_initial_desktop_size()
        fb = self.conn.fb
        if fb is None or not fb.lock.acquire(blocking=False):
            return
        try:
            rects = self.handler.take_damage()
            if not rects:
                return
            if len(rects) > MAX_RECTS_PER_PAINT:
                x0 = min(r[0] for r in rects)
                y0 = min(r[1] for r in rects)
                x1 = max(r[0] + r[2] for r in rects)
                y1 = max(r[1] + r[3] for r in rects)
                rects = [(x0, y0, x1 - x0, y1 - y0)]
            for x, y, w, h in rects:
                x = max(0, x)
                y = max(0, y)
                w = min(w, fb.width - x)
                h = min(h, fb.height - y)
                if w <= 0 or h <= 0:
                    continue
                ppm = b"P6 %d %d 255\n" % (w, h) + fb.get_rect(x, y, w, h)
                self.root.tk.call(self.image.name, "put", ppm,
                                  "-format", "ppm", "-to", x, y)
        finally:
            fb.lock.release()

    # -- remote resize ----------------------------------------------------------
    def on_configure(self, event):
        if event.widget is not self.root:
            return
        self.update_scrollbars()
        if not self.opts.remoteresize:
            return
        if self.resize_job:
            self.root.after_cancel(self.resize_job)
        self.resize_job = self.root.after(500, self.send_remote_resize)

    def send_remote_resize(self):
        self.resize_job = None
        conn = self.conn
        if not conn or not conn.fb or not conn.supports_set_desktop_size:
            return
        if self.opts.desktopsize and not self.desktop_size_sent:
            return
        w, h = self.canvas.winfo_width(), self.canvas.winfo_height()
        if w < 16 or h < 16 or (w, h) == (conn.fb.width, conn.fb.height):
            return
        log("Requesting remote desktop size %dx%d" % (w, h))
        conn.set_desktop_size(w, h)

    def check_initial_desktop_size(self):
        if self.desktop_size_sent or not self.opts.desktopsize:
            return
        if not self.conn or not self.conn.supports_set_desktop_size:
            return
        try:
            w, h = (int(v) for v in self.opts.desktopsize.lower().split("x"))
        except ValueError:
            log("Invalid DesktopSize %r" % self.opts.desktopsize)
            self.desktop_size_sent = True
            return
        self.desktop_size_sent = True
        self.conn.set_desktop_size(w, h)

    # -- input ----------------------------------------------------------------
    def bind_input(self):
        c = self.canvas
        c.bind("<KeyPress>", self.on_key_press)
        c.bind("<KeyRelease>", self.on_key_release)
        c.bind("<FocusOut>", self.on_focus_out)
        c.bind("<FocusIn>", self.on_focus_in)
        for i in (1, 2, 3):
            c.bind("<ButtonPress-%d>" % i, self.on_button)
            c.bind("<ButtonRelease-%d>" % i, self.on_button)
        c.bind("<Motion>", self.on_motion)
        c.bind("<MouseWheel>", self.on_wheel)
        c.bind("<Shift-MouseWheel>", self.on_wheel)
        for i in (4, 5):   # X11 wheel
            c.bind("<ButtonPress-%d>" % i, self.on_x11_wheel)
        c.bind("<Enter>", lambda e: c.focus_set())
        self.root.bind("<Configure>", self.on_configure)
        # Keep Tk from doing its own thing with Tab/Alt/F10
        for seq in ("<Tab>", "<Shift-Tab>", "<Alt-KeyPress>", "<F10>"):
            c.bind(seq, lambda e: self.on_key_press(e) or "break")

    def view_only(self):
        return self.viewonly_var.get()

    def on_key_press(self, event):
        menukey = self.opts.menukey
        if menukey and event.keysym == menukey:
            self.show_menu(event)
            return "break"
        if self.view_only() or not self.conn:
            return "break"
        ks = event_to_keysym(event.keysym, event.keysym_num, event.char)
        if ks is None:
            return "break"
        key = event.keycode if ks < 0xFE00 else ("ks", ks)
        old = self.down_keys.get(key)
        if old is not None and old != ks:
            self._send_key(old, False)
        self.down_keys[key] = ks
        self._send_key(ks, True)
        return "break"

    def on_key_release(self, event):
        if self.view_only() or not self.conn:
            return "break"
        ks = event_to_keysym(event.keysym, event.keysym_num, event.char)
        key = ("ks", ks) if ks is not None and ks >= 0xFE00 else \
            event.keycode
        down = self.down_keys.pop(key, None)
        if down is None and ks is not None:
            down = ks
        if down is not None:
            self._send_key(down, False)
        return "break"

    def _send_key(self, keysym, down):
        try:
            self.conn.key_event(keysym, down)
        except OSError:
            pass

    def release_all_keys(self):
        for ks in list(self.down_keys.values()):
            self._send_key(ks, False)
        self.down_keys.clear()

    def on_focus_out(self, event):
        if self.conn and not self.view_only():
            self.release_all_keys()

    def on_focus_in(self, event):
        if not self.opts.sendclipboard or not self.conn or self.view_only():
            return
        try:
            text = self.root.clipboard_get()
        except tk.TclError:
            return
        if text and text != self.last_clipboard:
            self.last_clipboard = text
            try:
                self.conn.client_cut_text(text)
            except OSError:
                pass

    def server_cut_text(self, text):
        if not self.opts.acceptclipboard:
            return
        self.last_clipboard = text
        self.root.clipboard_clear()
        self.root.clipboard_append(text)

    def _fb_coords(self, event):
        ox, oy = self.offset
        x = int(self.canvas.canvasx(event.x)) - ox
        y = int(self.canvas.canvasy(event.y)) - oy
        return x, y

    def _button_bit(self, num):
        # Tk on macOS numbers the right button 2 and the middle button 3
        if sys.platform == "darwin" and num in (2, 3):
            num = 5 - num
        return 1 << (num - 1)

    def on_button(self, event):
        if self.view_only() or not self.conn:
            return
        self.canvas.focus_set()
        bit = self._button_bit(event.num)
        if event.type == tk.EventType.ButtonPress:
            self.button_mask |= bit
        else:
            self.button_mask &= ~bit
        self._flush_pointer()
        self._send_pointer(*self._fb_coords(event))

    def on_motion(self, event):
        if self.view_only() or not self.conn:
            return
        x, y = self._fb_coords(event)
        interval = max(0, self.opts.pointereventinterval) / 1000.0
        now = time.monotonic()
        if now - self.last_pointer_time >= interval:
            self._send_pointer(x, y)
        else:
            self.pending_pointer = (x, y)
            if self.pointer_job is None:
                delay = int((interval - (now - self.last_pointer_time)) *
                            1000) + 1
                self.pointer_job = self.root.after(delay, self._flush_pointer)

    def _flush_pointer(self):
        if self.pointer_job:
            self.root.after_cancel(self.pointer_job)
            self.pointer_job = None
        if self.pending_pointer:
            x, y = self.pending_pointer
            self.pending_pointer = None
            self._send_pointer(x, y)

    def _send_pointer(self, x, y, mask=None):
        self.last_pointer_time = time.monotonic()
        self.last_pointer_pos = (x, y)
        try:
            self.conn.pointer_event(x, y, self.button_mask if mask is None
                                    else mask)
        except OSError:
            pass

    def _wheel_click(self, event, button):
        x, y = self._fb_coords(event)
        bit = 1 << (button - 1)
        self._flush_pointer()
        self._send_pointer(x, y, self.button_mask | bit)
        self._send_pointer(x, y, self.button_mask)

    def on_wheel(self, event):
        if self.view_only() or not self.conn:
            return
        horizontal = bool(event.state & 0x1)
        up = event.delta > 0
        steps = max(1, abs(event.delta) // 120) if sys.platform == "win32" \
            else 1
        button = (6 if up else 7) if horizontal else (4 if up else 5)
        for _ in range(steps):
            self._wheel_click(event, button)

    def on_x11_wheel(self, event):
        if self.view_only() or not self.conn:
            return
        if event.state & 0x1:
            self._wheel_click(event, 6 if event.num == 4 else 7)
        else:
            self._wheel_click(event, event.num)

    # -- F8 menu ---------------------------------------------------------------
    def build_menu(self):
        m = tk.Menu(self.root, tearoff=False)
        m.add_command(label="Disconnect", command=self.quit)
        m.add_separator()
        m.add_checkbutton(label="Full screen", variable=self.fullscreen_var,
                          command=lambda: self.set_fullscreen(
                              self.fullscreen_var.get()))
        m.add_command(label="Resize window to session",
                      command=self.resize_to_session)
        m.add_separator()
        m.add_checkbutton(label="Ctrl", variable=self.ctrl_var,
                          command=lambda: self._toggle_mod(
                              self.ctrl_var, NAMED["Control_L"]))
        m.add_checkbutton(label="Alt", variable=self.alt_var,
                          command=lambda: self._toggle_mod(
                              self.alt_var, NAMED["Alt_L"]))
        if self.opts.menukey:
            m.add_command(label="Send %s" % self.opts.menukey,
                          command=self.send_menu_key)
        m.add_command(label="Send Ctrl-Alt-Del", command=self.send_cad)
        m.add_separator()
        m.add_command(label="Refresh screen", command=self.refresh)
        m.add_checkbutton(label="View only", variable=self.viewonly_var,
                          command=self._view_only_changed)
        m.add_separator()
        m.add_command(label="Connection info...", command=self.show_info)
        m.add_command(label="About SagatVNC Viewer...",
                      command=lambda: dialogs.show_about(self.root))
        m.add_separator()
        m.add_command(label="Dismiss menu", command=lambda: None)
        self.menu = m

    def show_menu(self, event):
        self.release_all_keys()
        x = self.canvas.winfo_rootx() + 20
        y = self.canvas.winfo_rooty() + 20
        try:
            self.menu.tk_popup(x, y)
        finally:
            self.menu.grab_release()

    def _toggle_mod(self, var, keysym):
        if not self.view_only():
            self._send_key(keysym, var.get())

    def _view_only_changed(self):
        if self.view_only():
            self.release_all_keys()

    def send_menu_key(self):
        if self.view_only():
            return
        name = self.opts.menukey
        ks = NAMED.get(name)
        if ks is None and name.startswith("F") and name[1:].isdigit():
            ks = 0xFFBE + int(name[1:]) - 1
        if ks is None and len(name) == 1:
            ks = ord(name)
        if ks:
            self._send_key(ks, True)
            self._send_key(ks, False)

    def send_cad(self):
        if self.view_only():
            return
        for ks in (NAMED["Control_L"], NAMED["Alt_L"], NAMED["Delete"]):
            self._send_key(ks, True)
        for ks in (NAMED["Delete"], NAMED["Alt_L"], NAMED["Control_L"]):
            self._send_key(ks, False)

    def refresh(self):
        try:
            self.conn.request_update(incremental=False)
        except OSError:
            pass

    def set_fullscreen(self, on):
        self.fullscreen = on
        self.fullscreen_var.set(on)
        self.root.attributes("-fullscreen", on)
        self.update_scrollbars()

    def resize_to_session(self):
        if self.fullscreen:
            self.set_fullscreen(False)
        fb = self.conn.fb
        self.root.geometry("%dx%d" % (fb.width, fb.height))

    def show_info(self):
        c = self.conn
        sec = c.security
        info = ("Desktop name: %s\n"
                "Host: %s port: %d\n"
                "Size: %d x %d\n"
                "Pixel format: %s\n"
                "(server default %s)\n"
                "Requested encoding: %s\n"
                "Last used encoding: %s\n"
                "Protocol version: %d.%d\n"
                "Security: %s\n"
                "Bytes received: %d" %
                (c.name, self.host, self.port, c.fb.width, c.fb.height,
                 "depth 24 (32bpp) little-endian rgb888", c.server_pf,
                 enc.encoding_name(c.encodings_list()[1]),
                 enc.encoding_name(c.last_encoding)
                 if c.last_encoding is not None else "-",
                 c.version[0], c.version[1],
                 "encrypted" if sec and sec.secure else "not encrypted",
                 c.stream.bytes_received))
        messagebox.showinfo("VNC connection info", info, parent=self.root)


def main(options):
    log("SagatVNC Viewer %s (Python %s, Tk %s)" %
        (__version__, sys.version.split()[0], tk.TkVersion))
    return ViewerApp(options).run()
