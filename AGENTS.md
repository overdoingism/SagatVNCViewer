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
tests/                 unittest; fakeserver.py = encoders + scripted server
upstream/tigervnc/     reference checkout of TigerVNC (git-ignored)
```

## Key design decisions (don't undo these by accident)
- **Fixed pixel format.** The viewer always sends SetPixelFormat with
  `PixelFormat.preferred()`: 32bpp, depth 24, little-endian, red shift 0,
  green 8, blue 16. On the wire each pixel is then R,G,B,X. Every decoder
  relies on this:
  ZRLE CPIXEL and Tight TPIXEL are then plain RGB, and Raw becomes RGB by
  dropping every 4th byte. If you add low-colour mode, you need pixel
  translation in every decoder.
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
- **Tight JPEG is never requested**: no QualityLevel pseudo-encoding is
  sent, so servers should not use it. The decoder raises if a server sends
  it anyway.
- Clipboard uses classic Latin-1 cut text. ExtendedClipboard is *not*
  advertised, so negative-length ServerCutText is skipped.

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
python -m unittest discover -s tests -t .   # all tests, ~0.1 s
python -m sagatvnc -help                     # option list
python -m sagatvnc host:1                    # run against a real server
```
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
Rich cursor (Cursor / CursorWithAlpha), ExtendedClipboard (UTF-8),
ContinuousUpdates + Fence, RA2/RA2ne/DH security, Tight JPEG (optional
Pillow), options dialog, `-via` SSH tunnelling, multi-monitor full screen.
