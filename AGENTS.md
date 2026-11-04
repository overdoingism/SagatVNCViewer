# AGENTS.md — notes for coding agents working on SagatVNC Viewer

## What this is
A pure-Python port of the **viewer** half of TigerVNC (`vncviewer` +
the client side of `common/rfb`). License **GPL-2.0-or-later** (same as
TigerVNC). Standard library only. Please don't add runtime dependencies
without asking the maintainer. Pillow, `cryptography` and the like would
be optional extras at most.

## Layout
```
sagatvnc/
  __main__.py          CLI entry (python -m sagatvnc / sagatvncviewer)
  rfb/                 protocol only, NO GUI imports (keep it that way)
    connection.py      CConnection/CMsgReader/CMsgWriter equivalent; runs
                       on a worker thread, reports via a handler object
    security.py        None, VncAuth, Plain, VeNCrypt (+TLS/X509, TOFU)
    decoders.py        Raw, CopyRect, RRE, CoRRE, Hextile, ZRLE, Tight
    framebuffer.py     RGB (3 bytes/pixel) buffer guarded by an RLock
    pixelformat.py     PIXEL_FORMAT + rgbx_to_rgb
    des.py             pure-Python DES for VNC auth and vncpasswd files
    stream.py          blocking buffered socket reader/writer
    encodings.py       numeric constants
  viewer/              Tk front end
    app.py             ViewerApp: desktop window, input, F8 menu, painting
    dialogs.py         server / auth / about dialogs
    parameters.py      TigerVNC-style option parsing, server string parsing
    keysyms.py         Tk event -> X keysym
    configfile.py      .tigervnc load/save (parameters.cxx loadViewerParameters)
    scaler.py          window scaling: Lanczos-3 down / bicubic up via Pillow
    dpi.py             per-monitor DPI awareness (must run before Tk)
    win32.py           Windows-only tweaks (local IME on/off)
    cursor.py          server cursor shape -> .cur file -> Tk cursor
    keyboard.py        KeyboardWin32 port: scan codes, AltGr, LEDs
packaging/             PyInstaller build (build_exe.py), generated icon
tests/                 unittest; fakeserver.py = encoders + scripted server
upstream/tigervnc/     reference checkout of TigerVNC (git-ignored)
```

## Porting rule: follow upstream, not intuition
Earlier versions were partly written from general RFB knowledge, and that
caused real bugs. For example, the Cursor and ExtendedClipboard
pseudo-encodings were not advertised, which triggered a TigerVNC 1.16.2
server bug through its legacy clipboard path. So:
- Before changing protocol or input behaviour, read the upstream function
  and port it. Name it in a comment, e.g. `CConnection::requestNewUpdate()`.
- Where this port *must* differ (Tk can't do something, or Python-only
  features), say so in the comment, together with the reason.
- `tests/test_upstream_fidelity.py` pins ported behaviour. Each test names
  its upstream function. Use `FakeServer.sync()` (a fence round trip)
  before asserting on decoded state: the next update request goes out at
  update *start*, so seeing it proves nothing.

Deliberate differences from upstream, kept on purpose:
- One SetPixelFormat is always sent at init, even to pre-3.8 servers,
  because the decoders only handle the formats we ask for. Later changes
  are refused on pre-3.8 servers, as in `setPF()`.
- F8 popup menu (`MenuKey`) instead of upstream's ShortcutModifiers combos,
  which have no Windows counterpart. The owner chose this.
- The cursor: the server's shape by default, togglable to the plain
  arrow (F8 menu, `RemoteCursorShape`). Every cursor encoding is always
  advertised, so the server never draws a cursor into the image. Windows'
  LoadCursorFromFile (Tk "@file") stretches cursors to SM_CXCURSOR, which
  distorts them and moves the hotspot. `cursor.fit_to_canvas` pads them to
  exactly that size instead.
  Cursors are shown 1:1, as upstream does, and NOT shrunk with the view:
  winvnc sends the XOR I-beam as a 1px black line with a 1px white outline,
  and smooth resampling turns that into an invisible grey smear. Bigger
  views enlarge cursors by whole pixels (nearest). Shrinking happens only
  when a cursor is bigger than SM_CXCURSOR. For black/white cursors it
  uses per-tone max pooling (`shrink_cursor`). One-tone cursors get an
  opposite-tone outline (`ensure_contrast`), like winvnc's, so they show
  on any background.
- Pointer events only over the desktop image (upstream's Viewport is only
  that big): nothing over the black bars unless dragging, and one clamped
  "leave" event when the pointer crosses out.
- The clipboard is fetched as soon as the server notifies, but only while
  focused (`Viewport::handleClipboardAnnounce`). That is upstream
  behaviour, not a laziness hack.
- Python-only extras: scaling, RemoteDpiScale and its calibration, the
  IME toggle, DPI awareness, the colour/quality menu, rotating logs,
  session summaries.

## Key design decisions (don't undo these by accident)
- **Pixel formats.** Full colour is `PixelFormat.preferred()`: 32bpp,
  depth 24, little-endian, red shift 0, green 8, blue 16, so on the wire
  each pixel is R,G,B,X. Low colour uses TigerVNC's three 8bpp formats
  (`LOW_COLOUR_LEVELS`). Decoders never touch pixel bytes directly; they
  go through `PixelCodec` (`bypp`/`to_rgb`, ZRLE `cpix`, Tight `tpix`).
  Format changes are queued (`Connection.change_settings`) and sent only
  between updates, followed by a full refresh, so no update is decoded
  with the wrong format.
- **AutoSelect** ports CConn.cxx: bandwidth estimate per update (start
  20 Mbit/s, weight = elapsed_us*1000/1000ms capped at 20%), quality 8
  above 16 Mbit/s else 6, low colour below 256 kbit/s.
- **Framebuffer is packed RGB** because Tk's PPM loader takes exactly that.
  Painting = `photo put <P6 data> -to x y` per damaged rect (see
  `ViewerApp.paint`). Past `MAX_RECTS_PER_PAINT` rects, the damage is merged
  into one bounding box.
- **Threading.** Network code runs on one daemon thread. Tk must only be
  touched from the main thread: the handler queues events and
  `ViewerApp.poll()` drains them every `POLL_MS`. Blocking questions
  (password, certificate) use `ConnectionHandler._call_gui`. Client→server
  writes may come from any thread (`Stream.write` holds a lock). The GUI
  takes `fb.lock` non-blocking, because the decoder holds it while it reads
  from the socket.
- **JPEG** (Tight subencoding 9 and the standalone JPEG encoding 21) is
  decoded with Pillow. QualityLevel is only sent when Pillow is available
  and NoJPEG is off, since sending it is what makes servers use JPEG.
  `JPEGDecoder` re-inserts the DHT/DQT tables that servers leave out.
- **Scaling (not in TigerVNC).** `dpi.enable_dpi_awareness()` must run
  before any Tk window exists, or Windows bitmap-stretches the viewer.
  `scaler.Scaler.render` does two explicit single-direction Pillow passes
  (horizontal, then vertical) on a padded crop. Do NOT collapse them into
  one `resize(box=...)`: Pillow then picks the pass order and intermediate
  size per call, and patches stop matching the full frame (seams at text
  edges, up to ~25 levels off; see tests/test_scaler.py). Pillow is the
  only optional dependency. Without it, scaling is off and the view is 1:1.
  Mouse coordinates go through `Scaler.to_source`.
- **RemoteDpiScale** works around servers that are not DPI aware (TigerVNC
  winvnc: `win/rfb_win32/SInput.cxx` normalises by `GetSystemMetrics`,
  which returns the logical size). `ViewerApp.remote_pointer_pos` divides
  by the remote scale right before the pointer goes on the wire. It is
  applied after `Scaler.to_source`, never inside it. *Calibrate...*
  (`start_calibration`/`finish_calibration`) measures the factor as
  fb_size / click_pos with compensation off, and swallows the click.
- Clipboard is only sent when it changes after connecting (as TigerVNC
  does), never the pre-existing contents. Servers on the secure desktop
  can't take it.
- **Local IME is off by default** (`win32.set_local_ime`, which calls
  ImmAssociateContextEx with NULL and IACE_CHILDREN on the toplevel). It
  must run after Tk maps the window, and is reapplied on FocusIn. Keys
  then arrive as plain keysyms and the remote IME composes.
- Errors go to a dialog only when there is no console (`_WINDOWED` in
  `__main__`). Never pop dialogs from a console run: CI and agents hang.
- **Cursor**: we advertise the Cursor pseudo-encoding (-239), so servers
  stop drawing the cursor into the framebuffer. A non-DPI-aware winvnc
  draws it misplaced, which looked like a pointer-mapping bug but
  wasn't. `CursorManager` writes a 32 bpp .cur per shape (Windows only)
  and scales it with the view.
- **Clipboard**: ExtendedClipboard (UTF-8, CRLF, NUL-terminated, zlib
  for provide), following CConnection.cxx: reply to caps with
  UTF8|request|peek|notify|provide and size 0, request as soon as the
  server notifies, and announce local changes via notify (or an
  unsolicited provide if the server allows it). Servers without caps get
  classic Latin-1 ClientCutText, which can't carry CJK.

## Working with upstream
- TigerVNC reference: `git clone --depth 1 https://github.com/TigerVNC/tigervnc upstream/tigervnc`
  (first imported at upstream commit `009df04af8fe`, 2026-10-31).
- Useful upstream files: `common/rfb/CConnection.cxx`, `CMsgReader.cxx`,
  `*Decoder.cxx`, `CSecurity*.cxx`, `vncviewer/CConn.cxx`,
  `DesktopWindow.cxx`, `Viewport.cxx`, `parameters.cxx`.
- When porting a file, keep the upstream copyright lines in the header of
  the Python module (GPL attribution), next to ours.
- **TigerVNC's `AI_POLICY.md` rejects significant LLM-generated
  contributions.** This repo is a separate project. Do **not** send code from
  here to TigerVNC as a patch.
- Option names follow `vncviewer/parameters.cxx`. Keep the same names and
  defaults when you add options, so users' muscle memory carries over.

## Commands
```bash
python -m unittest discover -s tests -t .   # all tests, ~0.3 s
# (scaler tests skip without Pillow: use .venv\Scripts\python to run them)
python -m sagatvnc -help                     # option list
python -m sagatvnc host:1                    # run against a real server
.venv\Scripts\python packaging\build_exe.py  # dist\SagatVNCViewer.exe
```
The exe is `--onefile --windowed`, so `sys.stderr` is None when frozen.
`__main__._setup_windowed_logging` redirects output to
`%APPDATA%\SagatVNC\sagatvnc.log`, and errors must go to message boxes.
Dropping a file on the exe = `argv[1]` is that path. `looks_like_config_file`
uses TigerVNC's rule (a path separator means a file), plus `*.tigervnc`.
The icon is generated by `packaging/make_icon.py`. Don't copy TigerVNC's
logo (not affiliated).
Tests must stay headless (no Tk) except for manual GUI checks. To check the
GUI, run `ViewerApp` against `tests/fakeserver.FakeServer` on a TCP port and
read pixels back with `app.image.get(x, y)`.

## Conventions
- Python 3.9+ compatible, PEP 8, 79-column lines, no type-checker dependency.
- Each source file starts with the GPL header. Ported files also list the
  upstream copyright holders.
- Protocol bugs: add a decoder or end-to-end test in `tests/test_rfb.py`
  that uses the encoders in `fakeserver.py`.

## Roadmap ideas
Remaining items from the fidelity audit: X509 trust store/CRL/TOFU keyed on
the public key (upstream format), ExtendedMouseButtons, QEMUAudio,
keyboard grab (FullscreenSystemKeys), multi-monitor full screen,
`default.tigervnc` at startup, only the GUI subset of parameters from
.tigervnc files, repaint during long updates.
CursorWithAlpha, ContinuousUpdates + Fence, RA2/RA2ne/DH security,
options dialog, `-via` SSH tunnelling, multi-monitor full screen, custom
cursors on Linux/macOS (Tk needs XBM / platform files there).
