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
from ..rfb.connection import (Connection, ConnectionOptions,
                              DroppedBeforeSession)
from ..rfb.des import deobfuscate_password
from ..rfb.security import AuthFailure, connect_socket, parse_security_types
from ..rfb.stream import ConnectionClosed
from . import dialogs
from . import keyboard as kbd
from .keysyms import NAMED
from .parameters import config_dir, parse_server
from .cursor import CursorManager
from .scaler import Scaler, fit_size
from .win32 import set_local_ime

POLL_MS = 10

# Viewport.cxx: fake key codes for menu keys and lock key sync
FAKE_CTRL_KEY_CODE = 0x10001
FAKE_ALT_KEY_CODE = 0x10002
FAKE_DEL_KEY_CODE = 0x10003
FAKE_MENU_KEY_CODE = 0x10004
FAKE_KEY_CODE = 0xFFFF
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

    def clipboard_announce(self, available):
        self.events.put(("clipannounce", available))

    def name_changed(self, name):
        self.events.put(("name", name))

    def set_cursor(self, w, h, hot_x, hot_y, rgba):
        self.events.put(("cursor", w, h, hot_x, hot_y, rgba))

    def set_cursor_pos(self, x, y):
        self.events.put(("cursorpos", x, y))

    def set_led_state(self, state):
        self.events.put(("led", state))

    def set_desktop_size_done(self, result):
        self.events.put(("resizedone", result))


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
        self.keyboard = kbd.Keyboard(self._key_pressed, self._key_released,
                                     self.root.after, self.root.after_cancel)
        self.fake_lock_events = {}
        self.first_led_state = True
        self.pending_remote_resize = False
        self.last_remote_resize = 0.0
        self.resize_checked = False
        self.window_ready = False
        self.expected_window = None
        self.ready_tries = 0
        self.saved_credentials = None
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
        self.scaler = Scaler()
        self.display_geometry = (0, 0, 0, 0)
        self.layout_job = None
        scaling = options.scaling.strip().lower()
        if scaling not in ("auto", "off"):
            log("Unknown Scaling %r, using Auto" % options.scaling)
            scaling = "auto"
        if scaling == "auto" and not Scaler.available:
            log("Pillow is not installed: scaling to the window is off "
                "(pip install pillow)")
        self.scaling_var = tk.BooleanVar(value=scaling == "auto")
        dpi = options.remotedpiscale
        if not 50 <= dpi <= 500:
            log("RemoteDpiScale %d out of range, using 100" % dpi)
            dpi = 100
        self.remote_dpi_var = tk.IntVar(value=dpi)
        self.calibrating = None        # previous compensation while active
        self.cursors = None
        self.remote_cursor_var = tk.BooleanVar(
            value=bool(options.remotecursorshape))
        self.pointer_inside = False
        self.has_focus = False
        self.swallow_release = False
        self.local_ime_var = tk.BooleanVar(
            value=bool(options.localinputmethod))
        self.autoselect_var = tk.BooleanVar(value=bool(options.autoselect))
        # 3 = full colour, 2/1/0 = LowColorLevel
        self.colour_var = tk.IntVar(
            value=3 if options.fullcolor else options.lowcolorlevel)
        self.quality_var = tk.IntVar(value=options.qualitylevel)

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
            # Validate options before touching the network
            self.conn_options = self.connection_options()
        except ValueError as e:
            self.fatal("Invalid settings:\n\n%s" % e, reconnect=False)
            return 1
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
        co.security_types = parse_security_types(o.securitytypes, log)
        co.x509_ca = o.x509ca
        co.known_hosts = os.path.join(config_dir(), "x509_known_hosts")
        co.auto_select = bool(o.autoselect)
        pe = o.preferredencoding.strip()
        # Only the values upstream's PreferredEncoding enum accepts
        names = {k.lower(): enc.NAMES[k] for k in enc.PREFERRED_NAMES}
        unsupported = {k.lower() for k in enc.UNSUPPORTED_NAMES}
        if pe.lower() in names:
            co.preferred_encoding = names[pe.lower()]
        elif pe.lower() in unsupported:
            log("PreferredEncoding %s is not supported by SagatVNC, using "
                "Tight instead" % pe)
            co.preferred_encoding = enc.TIGHT
        else:
            raise ValueError("Unknown encoding %r" % pe)
        co.custom_compress_level = bool(o.customcompresslevel)
        co.compress_level = o.compresslevel
        co.full_colour = bool(o.fullcolor)
        if not 0 <= o.lowcolorlevel <= 2:
            raise ValueError("LowColorLevel must be 0, 1 or 2")
        co.low_colour_level = o.lowcolorlevel
        if not 0 <= o.qualitylevel <= 9:
            raise ValueError("QualityLevel must be between 0 and 9")
        co.quality_level = o.qualitylevel
        co.no_jpeg = bool(o.nojpeg)
        co.max_cut_text = max(0, o.maxcuttext)
        from ..rfb.decoders import JPEG_AVAILABLE
        if not JPEG_AVAILABLE and not co.no_jpeg:
            log("Pillow is not installed: JPEG is disabled")
        return co

    def start_connection(self, sock):
        self.handler = ConnectionHandler(self)
        self.conn = Connection(sock, self.host, self.handler,
                               self.conn_options)
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
            self.saved_credentials = None
            self.handler.events.put(("error", "Failed to authenticate with "
                                     "the server. Reason given by the "
                                     "server:\n\n%s" % e))
        except DroppedBeforeSession as e:
            self.handler.events.put(("error", str(e)))
        except ConnectionClosed:
            # CConn: end of stream once the session runs -> disconnect()
            # quietly, as TigerVNC does
            log(self.conn.session_summary())
            self.handler.events.put(("closed", None))
        except Exception as e:     # report anything else to the user
            if not self.conn.closed:
                log(self.conn.session_summary())
            if self.conn.closed:
                self.handler.events.put(("closed", None))
            else:
                self.handler.events.put(("error", "%s: %s" %
                                         (type(e).__name__, e)))

    # -- callbacks run on the GUI thread --------------------------------------
    def ask_credentials(self, need_username):
        """CConn::getUserPasswd(): environment, saved, password file,
        then the dialog."""
        env_user = os.environ.get("VNC_USERNAME")
        env_pass = os.environ.get("VNC_PASSWORD")
        if need_username and env_user and env_pass:
            return env_user, env_pass
        if not need_username and env_pass:
            return "", env_pass
        saved = self.saved_credentials
        if saved and (saved[0] or not need_username):
            return saved
        if not need_username and self.opts.passwordfile:
            try:
                with open(os.path.expanduser(self.opts.passwordfile),
                          "rb") as f:
                    return "", deobfuscate_password(f.read(8))
            except OSError as e:
                raise OSError("Failed to open \"%s\": %s" %
                              (self.opts.passwordfile, e.strerror or e))
        secure = bool(self.conn.security and self.conn.security.secure)
        res = dialogs.AuthDialog(self.root, need_username, secure,
                                 self.host,
                                 offer_keep=bool(self.opts.reconnectonerror)
                                 ).run()
        if res is None:
            raise Cancelled()
        user, password, keep = res
        if keep:
            self.saved_credentials = (user, password)
        return user, password

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
                elif kind == "clipannounce":
                    self.clipboard_announce(ev[1])
                elif kind == "name":
                    self.set_title(ev[1])
                elif kind == "cursor":
                    if self.cursors:
                        self.cursors.set_shape(*ev[1:])
                elif kind == "cursorpos":
                    self.set_cursor_pos(*ev[1:])
                elif kind == "led":
                    self.set_led_state(ev[1])
                elif kind == "resizedone":
                    self.set_desktop_size_done(ev[1])
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
        self.resize_checked = False
        self.pending_remote_resize = False
        self.first_led_state = True
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
        # Like TigerVNC, only send the clipboard when it changes after the
        # connection is made, never whatever happened to be in it before.
        # Some servers (winvnc on the secure login desktop) can't take
        # clipboard data and may drop the connection.
        try:
            self.last_clipboard = self.root.clipboard_get()
        except tk.TclError:
            self.last_clipboard = None
        if self.canvas is None:
            root = self.root
            root.configure(background="black")
            self.canvas = tk.Canvas(root, highlightthickness=0, borderwidth=0,
                                    background="black", takefocus=True)
            self.cursors = CursorManager(self.canvas,
                                         bool(self.opts.alwayscursor),
                                         self.opts.cursortype,
                                         self.remote_cursor_var.get(),
                                         bool(self.opts.blankcursorasibeam))
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
        # The outer Win32 window only exists once Tk has mapped it
        self.root.after(100, self.apply_local_ime)
        self.canvas.focus_set()
        self.window_ready = False
        self.ready_tries = 0
        self.root.after(50, self._wait_for_window)

    def _wait_for_window(self):
        """Wait until the window is mapped at its real size.  Before
        that Tk reports its 200x200 default, and a remote resize sent then
        made Linux servers shrink their desktop to 200x200 and back,
        leaving only the top-left corner repainted.  (Upstream only sends
        resizes from real window resizes, so it never does that.)"""
        self.ready_tries += 1
        self.root.update_idletasks()
        size = (self.root.winfo_width(), self.root.winfo_height())
        settled = self.root.winfo_ismapped() and (
            self.expected_window is None or self.fullscreen or
            self._window_maximized() or
            (abs(size[0] - self.expected_window[0]) <= 2 and
             abs(size[1] - self.expected_window[1]) <= 2))
        if not settled and self.ready_tries < 40:     # give up after ~2 s
            self.root.after(50, self._wait_for_window)
            return
        self.window_ready = True
        self.layout()
        self.remote_resize()

    def apply_local_ime(self):
        on = self.local_ime_var.get()
        if set_local_ime(self.root, on):
            log("Local input method %s" % ("enabled" if on else "disabled"))

    def scaling_active(self):
        return self.scaling_var.get() and Scaler.available

    def _window_maximized(self):
        try:
            return self.root.state() == "zoomed"
        except tk.TclError:
            return False

    def resize_framebuffer(self, width, height, initial=False):
        if not initial:
            # Only follow the new size if the window currently fits the
            # desktop exactly (and isn't maximized, full screen or waiting
            # on our own resize request), like DesktopWindow
            _, _, dw, dh = self.display_geometry
            fits = (self.canvas.winfo_width(), self.canvas.winfo_height()) \
                == (dw, dh) and self.scaler.identity
            if self.fullscreen or self._window_maximized() or \
                    self.pending_remote_resize or self.resize_job or \
                    not fits:
                self.root.update_idletasks()
                self.layout(force=True)
                return
        if not self.fullscreen and not self.opts.maximize:
            if initial and self.opts.geometry:
                self.root.geometry(self.opts.geometry)
            else:
                # Fit the work area; with scaling keep the aspect ratio so
                # a desktop larger than the screen shows whole, not cropped.
                sw = self.root.winfo_screenwidth() - 40
                sh = self.root.winfo_screenheight() - 80
                if self.scaling_active() and (width > sw or height > sh):
                    w, h = fit_size(width, height, sw, sh)
                else:
                    w, h = min(width, sw), min(height, sh)
                self.root.geometry("%dx%d" % (w, h))
                self.expected_window = (w, h)
        self.root.update_idletasks()
        self.layout(force=True)

    def layout(self, force=False):
        """Size and place the desktop image for the current window size."""
        if not self.canvas or not self.conn or not self.conn.fb:
            return
        fbw, fbh = self.conn.fb.width, self.conn.fb.height
        if self.scaling_active():
            self.hbar.grid_remove()
            self.vbar.grid_remove()
            self.root.update_idletasks()
            # Without scrollbars the canvas fills the toplevel, and the
            # toplevel learns its new size first - use that, so we never
            # lay out against a canvas still at its 200x200 default
            cw = max(1, self.root.winfo_width())
            ch = max(1, self.root.winfo_height())
            dw, dh = fit_size(fbw, fbh, cw, ch)
            self.canvas.configure(scrollregion=(0, 0, cw, ch))
            self.canvas.xview_moveto(0)
            self.canvas.yview_moveto(0)
        else:
            cw = self.root.winfo_width()
            ch = self.root.winfo_height()
            if cw < fbw and not self.fullscreen:
                self.hbar.grid(row=1, column=0, sticky="we")
            else:
                self.hbar.grid_remove()
            if ch < fbh and not self.fullscreen:
                self.vbar.grid(row=0, column=1, sticky="ns")
            else:
                self.vbar.grid_remove()
            dw, dh = fbw, fbh
            self.canvas.configure(scrollregion=(0, 0, fbw, fbh))
            self.root.update_idletasks()
            cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
        # Centre the desktop (black bars where the aspect ratio differs)
        ox = max(0, (cw - dw) // 2)
        oy = max(0, (ch - dh) // 2)
        self.canvas.coords(self.image_item, ox, oy)
        self.offset = (ox, oy)
        new = (fbw, fbh, dw, dh)
        if force or new != self.display_geometry:
            self.display_geometry = new
            self.scaler.configure(fbw, fbh, dw, dh)
            self.image.configure(width=dw, height=dh)
            if self.cursors:
                self.cursors.set_scale((self.scaler.sx + self.scaler.sy) / 2)
            with self.handler.damage_lock:
                self.handler.damage = [(0, 0, fbw, fbh)]

    def paint(self):
        if self.image is None or self.handler is None:
            return
        self.check_initial_desktop_size()
        fb = self.conn.fb
        if fb is None or not fb.lock.acquire(blocking=False):
            return
        try:
            if self.display_geometry[:2] != (fb.width, fb.height):
                return          # resize pending; layout() will repaint
            rects = self.handler.take_damage()
            if not rects:
                return
            sc = self.scaler
            out = []
            for x, y, w, h in rects:
                x, y = max(0, x), max(0, y)
                w, h = min(w, fb.width - x), min(h, fb.height - y)
                if w > 0 and h > 0:
                    out.append(sc.dest_rect(x, y, w, h))
            if len(out) > MAX_RECTS_PER_PAINT:
                x0 = min(r[0] for r in out)
                y0 = min(r[1] for r in out)
                x1 = max(r[0] + r[2] for r in out)
                y1 = max(r[1] + r[3] for r in out)
                out = [(x0, y0, x1 - x0, y1 - y0)]
            for x, y, w, h in out:
                if w <= 0 or h <= 0:
                    continue
                ppm = b"P6 %d %d 255\n" % (w, h) + sc.render(fb, x, y, w, h)
                self.root.tk.call(self.image.name, "put", ppm,
                                  "-format", "ppm", "-to", x, y)
        finally:
            fb.lock.release()

    # -- remote resize (DesktopWindow::remoteResize) ---------------------------
    def on_configure(self, event):
        # The layout depends on the *canvas* size, and the canvas gets its
        # new size after the toplevel: reacting to the toplevel alone could
        # lay out with the canvas still at its 200x200 default, leaving the
        # desktop drawn small in the top-left corner
        if event.widget is not self.root and event.widget is not self.canvas:
            return
        # Rescaling a full frame costs tens of ms: coalesce resize events
        if self.layout_job:
            self.root.after_cancel(self.layout_job)
        self.layout_job = self.root.after(40, self._do_layout)
        self.remote_resize()

    def _do_layout(self):
        self.layout_job = None
        self.layout()

    def remote_resize(self):
        """Ask the server for our window size: one request in flight at a
        time and no more than one per 100 ms, as DesktopWindow does."""
        if self.resize_job:
            self.root.after_cancel(self.resize_job)
            self.resize_job = None
        conn = self.conn
        if self.view_only() or not self.opts.remoteresize:
            return
        if not self.window_ready:
            return            # _wait_for_window() calls us when it is
        if not conn or not conn.fb or not conn.supports_set_desktop_size:
            return
        if self.pending_remote_resize:
            return
        since = (time.monotonic() - self.last_remote_resize) * 1000
        if since < 100:
            self.resize_job = self.root.after(int(100 - since) + 1,
                                              self.remote_resize)
            return
        w, h = self.canvas.winfo_width(), self.canvas.winfo_height()
        if not self.desktop_size_sent and self.opts.desktopsize:
            # An explicit size has been requested
            self.desktop_size_sent = True
            try:
                w, h = (int(v) for v in
                        self.opts.desktopsize.lower().split("x"))
            except ValueError:
                log("Invalid DesktopSize %r" % self.opts.desktopsize)
                return
        if w < 16 or h < 16 or (w, h) == (conn.fb.width, conn.fb.height):
            return
        log("Requesting remote desktop size %dx%d" % (w, h))
        self.pending_remote_resize = True
        self.last_remote_resize = time.monotonic()
        try:
            conn.set_desktop_size(w, h)
        except OSError:
            pass

    def set_desktop_size_done(self, result):
        """DesktopWindow::setDesktopSizeDone()"""
        self.pending_remote_resize = False
        if result != 0:
            log("SetDesktopSize failed: %d" % result)
            return
        self.remote_resize()      # the window may have changed meanwhile

    def check_initial_desktop_size(self):
        # The first ExtendedDesktopSize tells us the server can resize
        if self.conn and self.conn.supports_set_desktop_size and \
                not self.resize_checked:
            self.resize_checked = True
            self.remote_resize()

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
        c.bind("<Leave>", self.on_leave)
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

    def _swallow_fake_lock_key(self, event):
        """Lock keys we toggled ourselves for LED sync come back as Tk
        events; KeyboardWin32 drops them by their fake scan code."""
        n = self.fake_lock_events.get(event.keysym, 0)
        if n:
            self.fake_lock_events[event.keysym] = n - 1
            return True
        return False

    def on_key_press(self, event):
        if self.calibrating is not None and event.keysym == "Escape":
            self.cancel_calibration()
            return "break"
        if self._swallow_fake_lock_key(event):
            return "break"
        menukey = self.opts.menukey
        if menukey and event.keysym == menukey:
            self.keyboard.resolve_altgr(False)
            self.show_menu(event)
            return "break"
        if self.view_only() or not self.conn:
            return "break"
        self.keyboard.key_press(event)
        return "break"

    def on_key_release(self, event):
        if self._swallow_fake_lock_key(event):
            return "break"
        if self.view_only() or not self.conn:
            return "break"
        # A release without a press (e.g. the menu key) is ignored by
        # Connection.send_key_release, as in CConnection::sendKeyRelease
        self.keyboard.key_release(event)
        return "break"

    def _key_pressed(self, system_key_code, key_code, keysym):
        try:
            self.conn.send_key_press(system_key_code, key_code, keysym)
        except OSError:
            pass

    def _key_released(self, system_key_code):
        try:
            self.conn.send_key_release(system_key_code)
        except OSError:
            pass

    def release_all_keys(self):
        """Viewport::resetKeyboard()"""
        if self.conn:
            try:
                self.conn.release_all_keys()
            except OSError:
                pass
        self.keyboard.reset()

    def on_focus_out(self, event):
        self.has_focus = False
        # We won't get more key events, so reset our knowledge about keys
        self.release_all_keys()

    def on_focus_in(self, event):
        """Viewport FL_FOCUS"""
        self.has_focus = True
        if sys.platform == "win32" and not self.local_ime_var.get():
            set_local_ime(self.root, False)    # cheap; survives remaps
        if not self.conn:
            return
        self.flush_pending_clipboard()
        # Lock keys may have got out of sync while we didn't have focus
        self.push_led_state()
        # Resend the menu's Ctrl/Alt if they are still on
        if not self.view_only():
            if self.ctrl_var.get():
                self._key_pressed(FAKE_CTRL_KEY_CODE, 0x1D, NAMED["Control_L"])
            if self.alt_var.get():
                self._key_pressed(FAKE_ALT_KEY_CODE, 0x38, NAMED["Alt_L"])

    # -- lock key LEDs (Viewport::setLEDState / pushLEDState) -----------------
    def set_led_state(self, state):
        if self.first_led_state:
            # The first message only announces support; push our state
            self.first_led_state = False
            if self.has_focus:
                self.push_led_state()
            return
        if self.view_only() or not self.has_focus:
            return
        for name in kbd.set_led_state(state):
            self.fake_lock_events[name] = \
                self.fake_lock_events.get(name, 0) + 2    # press + release

    def push_led_state(self):
        if self.view_only() or not self.conn or \
                self.conn.server_led_state == enc.LED_UNKNOWN:
            return
        local = kbd.get_led_state()
        if local == enc.LED_UNKNOWN:
            return
        server = self.conn.server_led_state
        for bit, key_code, keysym in (
                (enc.LED_CAPS_LOCK, 0x3A, 0xFFE5),
                (enc.LED_NUM_LOCK, 0x45, 0xFF7F),
                (enc.LED_SCROLL_LOCK, 0x46, 0xFF14)):
            if (local & bit) != (server & bit):
                log("Inserting fake lock key to get in sync with server")
                self._key_pressed(FAKE_KEY_CODE, key_code, keysym)
                self._key_released(FAKE_KEY_CODE)

    # -- clipboard ----------------------------------------------------------------
    def flush_pending_clipboard(self):
        """Viewport::flushPendingClipboard(): tell the server about a local
        clipboard change made while we didn't have focus."""
        if not self.opts.sendclipboard or self.view_only():
            return
        try:
            text = self.root.clipboard_get()
        except tk.TclError:
            return
        if text and text != self.last_clipboard:
            self.last_clipboard = text
            try:
                self.conn.announce_clipboard(text)
            except OSError:
                pass

    def clipboard_announce(self, available):
        """TigerVNC Viewport::handleClipboardAnnounce().

        Only fetch the server's clipboard while the viewer has focus.  This
        matters: a fetch makes the server read its clipboard, and while
        another program (e.g. AnyDesk) is writing it, TigerVNC 1.16.2's
        server can fail there and drop the connection.
        """
        if self.view_only() or not self.opts.acceptclipboard or \
                not self.conn:
            return
        if not available:
            log("Clipboard is no longer available on server")
            return
        if not self.has_focus:
            log("Got notification of new clipboard on server whilst not "
                "focused, ignoring")
            return
        try:
            self.conn.request_clipboard()
        except OSError:
            pass

    def server_cut_text(self, text):
        """TigerVNC Viewport::handleClipboardData()."""
        if not self.opts.acceptclipboard or not self.has_focus:
            return
        self.last_clipboard = text
        self.root.clipboard_clear()
        self.root.clipboard_append(text)

    # -- pointer --------------------------------------------------------------------
    def _fb_coords(self, event):
        ox, oy = self.offset
        x = self.canvas.canvasx(event.x) - ox
        y = self.canvas.canvasy(event.y) - oy
        return self.scaler.to_source(x, y)

    def _button_bit(self, num):
        # Tk on macOS numbers the right button 2 and the middle button 3
        if sys.platform == "darwin" and num in (2, 3):
            num = 5 - num
        return 1 << (num - 1)

    def on_button(self, event):
        self.keyboard.mouse_event()
        if self.view_only() or not self.conn:
            return
        self.canvas.focus_set()
        if self.calibrating is not None and \
                event.type == tk.EventType.ButtonPress:
            self.finish_calibration(*self._fb_coords(event))
            self.swallow_release = True
            return
        if self.swallow_release and event.type != tk.EventType.ButtonPress:
            self.swallow_release = False
            return
        bit = self._button_bit(event.num)
        if event.type == tk.EventType.ButtonPress:
            if not self._over_desktop(event):
                return            # a click on the black bars isn't ours
            self.button_mask |= bit
        else:
            if not self.button_mask & bit:
                return            # its press never went to the server
            self.button_mask &= ~bit
        self._flush_pointer()
        self._send_pointer(*self._fb_coords(event))
        if not self.button_mask and not self._over_desktop(event):
            self._pointer_left(event)         # a drag ended outside

    def _over_desktop(self, event):
        """Is the pointer over the desktop image (not the black bars or
        outside the window)?  Upstream's Viewport widget is only as big as
        the desktop, so it gets no events elsewhere."""
        ox, oy = self.offset
        x = self.canvas.canvasx(event.x) - ox
        y = self.canvas.canvasy(event.y) - oy
        _, _, dw, dh = self.display_geometry
        return 0 <= x < dw and 0 <= y < dh

    def _pointer_left(self, event):
        """Viewport FL_LEAVE: one last (clamped) move event, then stop
        tracking; outside the desktop the normal arrow is shown."""
        self.pointer_inside = False
        if self.cursors:
            self.cursors.outside()
        if self.view_only() or not self.conn:
            return
        self._flush_pointer()
        self._send_pointer(*self._fb_coords(event))

    def _pointer_entered(self):
        self.pointer_inside = True
        if self.cursors:
            self.cursors.show()

    def on_motion(self, event):
        self.keyboard.mouse_event()
        inside = self._over_desktop(event)
        if not inside and not self.button_mask:
            # Not dragging: nothing goes to the server out here
            if self.pointer_inside:
                self._pointer_left(event)
            return
        if inside and not self.pointer_inside:
            self._pointer_entered()
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

    def on_leave(self, event):
        """Left the window.  (FLTK sends no FL_LEAVE during a drag; Tk
        can, so only with no buttons held.)"""
        if self.button_mask or not self.pointer_inside:
            return
        self._pointer_left(event)

    def _flush_pointer(self):
        if self.pointer_job:
            self.root.after_cancel(self.pointer_job)
            self.pointer_job = None
        if self.pending_pointer:
            x, y = self.pending_pointer
            self.pending_pointer = None
            self._send_pointer(x, y)

    def remote_pointer_pos(self, x, y):
        """Framebuffer position -> position to put on the wire.

        A server that is not DPI aware (TigerVNC's winvnc, for one)
        may inject the pointer using Windows' scaled-down logical screen
        size. Dividing by the remote scale cancels that out.
        """
        scale = self.remote_dpi_var.get()
        if scale == 100:
            return x, y
        return int(x * 100 / scale), int(y * 100 / scale)

    def _send_pointer(self, x, y, mask=None):
        self.last_pointer_time = time.monotonic()
        self.last_pointer_pos = (x, y)
        x, y = self.remote_pointer_pos(x, y)
        try:
            self.conn.pointer_event(x, y, self.button_mask if mask is None
                                    else mask)
        except OSError:
            pass

    def _wheel_click(self, event, button):
        """Viewport FL_MOUSEWHEEL: a quick press of the wheel "button",
        followed by an immediate release - one per wheel event."""
        x, y = self._fb_coords(event)
        bit = 1 << (button - 1)
        self._flush_pointer()
        self._send_pointer(x, y, self.button_mask | bit)
        self._send_pointer(x, y, self.button_mask)

    def _shift_held(self):
        if sys.platform == "win32":
            import ctypes
            return bool(ctypes.windll.user32.GetKeyState(0x10) & 0x8000)
        return False

    def on_wheel(self, event):
        self.keyboard.mouse_event()
        if self.view_only() or not self.conn or not event.delta or                 not self._over_desktop(event):
            return
        # Tk on Windows reports a horizontal wheel (WM_MOUSEHWHEEL) as
        # Shift+wheel.  A Shift that is really held is just Shift + the
        # vertical wheel, which the server sees as such.
        horizontal = bool(event.state & 0x1) and not self._shift_held()
        if horizontal:
            self._wheel_click(event, 6 if event.delta > 0 else 7)
        else:
            self._wheel_click(event, 4 if event.delta > 0 else 5)

    def on_x11_wheel(self, event):
        self.keyboard.mouse_event()
        if self.view_only() or not self.conn or                 not self._over_desktop(event):
            return
        self._wheel_click(event, event.num)

    def set_cursor_pos(self, x, y):
        """Viewport::setCursorPos(): the server moved the pointer; warp
        ours if we have focus and the pointer is over the desktop."""
        if not self.has_focus or not self.canvas:
            return
        scale = self.remote_dpi_var.get() / 100.0
        fx, fy = x * scale, y * scale
        ox, oy = self.offset
        cx = int(fx * self.scaler.sx) + ox - int(self.canvas.canvasx(0))
        cy = int(fy * self.scaler.sy) + oy - int(self.canvas.canvasy(0))
        px = self.root.winfo_pointerx() - self.canvas.winfo_rootx()
        py = self.root.winfo_pointery() - self.canvas.winfo_rooty()
        if not (0 <= px < self.canvas.winfo_width() and
                0 <= py < self.canvas.winfo_height()):
            return
        self.canvas.event_generate("<Motion>", warp=True, x=cx, y=cy)

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
        m.add_checkbutton(label="Scale to window", variable=self.scaling_var,
                          command=self._scaling_changed,
                          state="normal" if Scaler.available else "disabled")
        dpi_menu = tk.Menu(m, tearoff=False)
        for pct in (100, 125, 150, 175, 200, 225, 250, 300):
            dpi_menu.add_radiobutton(
                label="%d%%%s" % (pct, " (off)" if pct == 100 else ""),
                value=pct, variable=self.remote_dpi_var,
                command=self._remote_dpi_changed)
        dpi_menu.add_separator()
        dpi_menu.add_command(label="Calibrate...",
                             command=self.start_calibration)
        m.add_cascade(label="Remote DPI compensation", menu=dpi_menu)
        m.add_separator()
        m.add_checkbutton(label="Auto select (color and quality)",
                          variable=self.autoselect_var,
                          command=self._picture_changed)
        colour_menu = tk.Menu(m, tearoff=False)
        for value, label in ((3, "Full color"), (2, "Medium (256 colors)"),
                             (1, "Low (64 colors)"), (0, "Very low (8 colors)")):
            colour_menu.add_radiobutton(label=label, value=value,
                                        variable=self.colour_var,
                                        command=self._picture_changed)
        m.add_cascade(label="Color level", menu=colour_menu)
        quality_menu = tk.Menu(m, tearoff=False)
        for q in range(9, -1, -1):
            quality_menu.add_radiobutton(
                label="%d%s" % (q, {9: " (best)", 0: " (smallest)"}.get(q, "")),
                value=q, variable=self.quality_var,
                command=self._picture_changed)
        m.add_cascade(label="JPEG quality", menu=quality_menu)
        self.picture_menus = (colour_menu, quality_menu)
        m.add_checkbutton(label="Remote cursor shape",
                          variable=self.remote_cursor_var,
                          command=lambda: self.cursors.set_remote_shape(
                              self.remote_cursor_var.get()),
                          state="normal" if sys.platform == "win32"
                          else "disabled")
        m.add_checkbutton(label="Local input method",
                          variable=self.local_ime_var,
                          command=self.apply_local_ime,
                          state="normal" if sys.platform == "win32"
                          else "disabled")
        m.add_separator()
        m.add_checkbutton(label="Ctrl", variable=self.ctrl_var,
                          command=lambda: self._toggle_mod("ctrl"))
        m.add_checkbutton(label="Alt", variable=self.alt_var,
                          command=lambda: self._toggle_mod("alt"))
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

    def _toggle_mod(self, which):
        """ID_CTRL / ID_ALT: fake key codes, so they're released on
        focus loss and pressed again on focus (see on_focus_in)."""
        if which == "ctrl":
            var, code, key_code, ks = (self.ctrl_var, FAKE_CTRL_KEY_CODE,
                                       0x1D, NAMED["Control_L"])
        else:
            var, code, key_code, ks = (self.alt_var, FAKE_ALT_KEY_CODE,
                                       0x38, NAMED["Alt_L"])
        if self.view_only():
            return
        if var.get():
            self._key_pressed(code, key_code, ks)
        else:
            self._key_released(code)

    def _picture_changed(self):
        """Apply color level / JPEG quality / AutoSelect from the menu."""
        auto = self.autoselect_var.get()
        colour = self.colour_var.get()
        if self.conn:
            self.conn.change_settings(
                auto_select=auto, full_colour=colour == 3,
                low_colour_level=min(colour, 2),
                quality_level=self.quality_var.get())
        log("Picture settings: %s, color %s, JPEG quality %d" %
            ("auto select" if auto else "manual",
             "full" if colour == 3 else "level %d" % colour,
             self.quality_var.get()))

    # -- pointer calibration ---------------------------------------------------
    def start_calibration(self):
        """Measure how far a non-DPI-aware server moves the cursor.

        With compensation off, such a server puts the remote cursor at
        pos * f, so it reaches the remote screen's bottom-right corner when
        the local pointer is only at (width / f, height / f).  The user
        moves there and clicks; f follows from where they clicked.
        """
        if self.view_only() or not self.conn:
            return
        messagebox.showinfo(
            "Calibrate pointer",
            "Compensation is switched off while calibrating.\n\n"
            "Slowly move the mouse until the REMOTE cursor (the arrow drawn "
            "in the remote desktop) just touches the bottom-right corner of "
            "the remote screen, then click once.  The click is not sent "
            "to the server.\n\nPress Esc to cancel.", parent=self.root)
        self.calibrating = self.remote_dpi_var.get()
        self.remote_dpi_var.set(100)
        self.canvas.configure(cursor="crosshair")
        self.canvas.focus_set()
        log("Pointer calibration started")

    def cancel_calibration(self):
        self.remote_dpi_var.set(self.calibrating)
        self.calibrating = None
        self.cursors.apply()
        log("Pointer calibration cancelled")

    def finish_calibration(self, x, y):
        fb = self.conn.fb
        self.calibrating = None
        self.cursors.apply()
        fx = fb.width / max(1, x + 1)
        fy = fb.height / max(1, y + 1)
        log("Calibration click at %d,%d of %dx%d: factors %.3f x %.3f" %
            (x, y, fb.width, fb.height, fx, fy))
        if fx < 1.02 and fy < 1.02:
            self.remote_dpi_var.set(100)
            messagebox.showinfo(
                "Calibrate pointer",
                "The remote cursor reached the corner together with your "
                "mouse, so no compensation is needed (it is now off).\n\n"
                "If the remote cursor could NOT reach the corner at all, "
                "the server moves it too little; that can't be corrected "
                "from the viewer - run the server DPI aware instead.",
                parent=self.root)
            return
        pct = int(round((fx + fy) / 2 * 100))
        pct = max(100, min(500, pct))
        self.remote_dpi_var.set(pct)
        note = ""
        if abs(fx - fy) > 0.08:
            note = ("\n\nNote: horizontal and vertical differ (%.0f%% vs "
                    "%.0f%%); the average is used." % (fx * 100, fy * 100))
        messagebox.showinfo(
            "Calibrate pointer",
            "Remote DPI compensation is now %d%%.%s\n\nTo keep it, add "
            "this line to your .tigervnc file:\n\n    RemoteDpiScale=%d" %
            (pct, note, pct), parent=self.root)
        self._remote_dpi_changed()

    def _remote_dpi_changed(self):
        log("Remote DPI compensation: %d%%" % self.remote_dpi_var.get())
        # Move the remote pointer to the corrected position right away
        if self.conn and not self.view_only():
            self._send_pointer(*self.last_pointer_pos)

    def _scaling_changed(self):
        self.layout(force=True)

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
            self._key_pressed(FAKE_MENU_KEY_CODE, 0, ks)
            self._key_released(FAKE_MENU_KEY_CODE)

    def send_cad(self):
        if self.view_only():
            return
        # ID_CTRLALTDEL; leaves the menu's own Ctrl/Alt alone
        self._key_pressed(FAKE_CTRL_KEY_CODE, 0x1D, NAMED["Control_L"])
        self._key_pressed(FAKE_ALT_KEY_CODE, 0x38, NAMED["Alt_L"])
        self._key_pressed(FAKE_DEL_KEY_CODE, 0xD3, NAMED["Delete"])
        self._key_released(FAKE_DEL_KEY_CODE)
        if not self.alt_var.get():
            self._key_released(FAKE_ALT_KEY_CODE)
        if not self.ctrl_var.get():
            self._key_released(FAKE_CTRL_KEY_CODE)

    def refresh(self):
        try:
            self.conn.refresh_framebuffer()
        except OSError:
            pass

    def set_fullscreen(self, on):
        self.fullscreen = on
        self.fullscreen_var.set(on)
        self.root.attributes("-fullscreen", on)
        self.root.update_idletasks()
        self.layout()

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
                "Shown at: %d x %d (%s)\n"
                "Remote DPI compensation: %d%%\n"
                "Pixel format: %s\n"
                "Line speed estimate: %d kbit/s\n"
                "JPEG quality: %s\n"
                "(server default %s)\n"
                "Requested encoding: %s\n"
                "Last used encoding: %s\n"
                "Protocol version: %d.%d\n"
                "Security: %s\n"
                "Bytes received: %d" %
                (c.name, self.host, self.port, c.fb.width, c.fb.height,
                 self.display_geometry[2], self.display_geometry[3],
                 "1:1" if self.scaler.identity else
                 "Lanczos-3" if self.scaler.support == 3.0 else
                 "bicubic",
                 self.remote_dpi_var.get(),
                 c.pf, c.bps_estimate // 1000,
                 c.quality if c.jpeg_enabled() else "off",
                 c.server_pf,
                 enc.encoding_name(enc.TIGHT if c.options.auto_select
                                   else c.options.preferred_encoding),
                 enc.encoding_name(c.last_encoding)
                 if c.last_encoding is not None else "-",
                 c.version[0], c.version[1],
                 "encrypted" if sec and sec.secure else "not encrypted",
                 c.stream.bytes_received))
        messagebox.showinfo("VNC connection info", info, parent=self.root)


def main(options):
    from .dpi import enable_dpi_awareness
    awareness = enable_dpi_awareness()
    if awareness:
        log("DPI awareness: %s" % awareness)
    log("SagatVNC Viewer %s (Python %s, Tk %s)" %
        (__version__, sys.version.split()[0], tk.TkVersion))
    return ViewerApp(options).run()
